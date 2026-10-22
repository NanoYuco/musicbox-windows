#!/usr/bin/env python
# @Author: omi
# @Date:   2014-07-15 15:48:27
# @Last Modified by:   AlanAlbert
# @Last Modified time: 2018-11-21 14:00:00
"""
网易云音乐 Player
"""

from __future__ import annotations

import contextlib

# Let's make some noise
import json
import os
import queue
import random
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import (  # noqa: UP035 — List avoids shadowing by @property list
    Any,
    List,
    cast,
)

from . import logger
from .api import NetEase
from .cache import Cache
from .config import Config
from .storage import Storage
from .ui import Ui
from .utils import notify

if os.name == "nt":
    from . import windows_mpg123, windows_mpv

log = logger.getLogger(__name__)


class NullUi:
    """Headless UI stub for the daemon: every render call is a no-op.

    The real ``Ui`` grabs the terminal via ``curses.initscr()`` on construction,
    which must never happen inside ``musicboxd``.
    """

    def build_playinfo(self, *args, **kwargs):
        return None

    def update_size(self, *args, **kwargs):
        return None


class Player:
    MODE_ORDERED = 0
    MODE_ORDERED_LOOP = 1
    MODE_SINGLE_LOOP = 2
    MODE_RANDOM = 3
    MODE_RANDOM_LOOP = 4
    SUBPROCESS_LIST = []
    MUSIC_THREADS = []

    def __init__(self, ui=None):
        self.config = Config()
        self.ui = ui if ui is not None else Ui()
        self.popen_handler: Any = None
        self.current_backend = ""
        self.mpv_ipc_path = ""
        self.playback_token = 0
        self._playback_lock = threading.RLock()
        self.playback_error = None
        self.playback_state = "stopped"
        # flag stop, prevent thread start
        self.playing_flag = False
        self.refresh_url_flag = False
        self.process_length = 0
        self.process_location = 0
        self.storage = Storage()
        self.cache = Cache()
        self.end_callback: Callable[[], None] | None = None
        self.playing_song_changed_callback: Callable[[], None] | None = None
        self.api = NetEase()
        self.playinfo_starts = time.time()

    @property
    def info(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.storage.database["player_info"])

    @property
    def songs(self) -> dict[str, dict[str, Any]]:
        return cast(dict[str, dict[str, Any]], self.storage.database["songs"])

    @property
    def index(self):
        return self.info["idx"]

    @property
    def list(self) -> List[str]:  # noqa: UP006
        return cast(list[str], self.info["player_list"])

    @property
    def order(self) -> List[int]:  # noqa: UP006
        return cast(list[int], self.info["playing_order"])

    @property
    def mode(self):
        return self.info["playing_mode"]

    @property
    def is_ordered_mode(self):
        return self.mode == Player.MODE_ORDERED

    @property
    def is_ordered_loop_mode(self):
        return self.mode == Player.MODE_ORDERED_LOOP

    @property
    def is_single_loop_mode(self):
        return self.mode == Player.MODE_SINGLE_LOOP

    @property
    def is_random_mode(self):
        return self.mode == Player.MODE_RANDOM

    @property
    def is_random_loop_mode(self):
        return self.mode == Player.MODE_RANDOM_LOOP

    @property
    def config_notifier(self):
        return self.config.get("notifier")

    @property
    def config_mpg123(self):
        return self.config.get("mpg123_parameters")

    @property
    def config_mpv(self):
        return self.config.get("mpv_parameters")

    @property
    def current_song(self) -> dict[str, Any]:
        if not self.songs:
            return {}

        if not self.is_index_valid:
            return {}
        song_id = self.list[self.index]
        return self.songs.get(song_id, {})

    @property
    def playing_id(self):
        return self.current_song.get("song_id")

    @property
    def playing_name(self):
        return self.current_song.get("song_name")

    @property
    def is_empty(self):
        return len(self.list) == 0

    @property
    def is_index_valid(self):
        return 0 <= self.index < len(self.list)

    def notify_playing(self):
        if not self.current_song:
            return

        if not self.config_notifier:
            return

        song = self.current_song
        notify(
            "正在播放: {}\n{}-{}".format(
                song["song_name"], song["artist"], song["album_name"]
            )
        )

    def notify_copyright_issue(self):
        log.warning(f"Song {self.playing_id} is unavailable due to copyright issue.")
        notify("版权限制，无法播放此歌曲")

    def change_mode(self, step=1):
        self.info["playing_mode"] = (self.info["playing_mode"] + step) % 5

    def build_playinfo(self):
        if threading.current_thread() is not threading.main_thread():
            return
        if not self.current_song:
            self.ui.build_playinfo("", "", "", "", 0, pause=True)
            return

        quality = self.current_song["quality"]
        audio_quality = self.current_song.get("audio_quality", "")
        if audio_quality and int(time.time() - self.playinfo_starts) % 6 >= 3:
            quality = audio_quality
        self.ui.build_playinfo(
            self.current_song["song_name"],
            self.current_song["artist"],
            self.current_song["album_name"],
            quality,
            self.playinfo_starts,
            pause=not self.playing_flag,
            state=getattr(self, "playback_state", None),
        )

    def add_songs(self, songs):
        for song in songs:
            song_id = str(song["song_id"])
            self.info["player_list"].append(song_id)
            if song_id in self.songs:
                self.songs[song_id].update(song)
            else:
                self.songs[song_id] = song

    def refresh_urls(self):
        songs = cast(list[dict[str, Any]], self.api.dig_info(self.list, "refresh_urls"))
        if not songs:
            return
        for song in songs:
            song_id = str(song["song_id"])
            if song_id in self.songs:
                self.songs[song_id]["mp3_url"] = song["mp3_url"]
                self.songs[song_id]["type"] = song.get("type", "")
                self.songs[song_id]["level"] = song.get("level", "")
                self.songs[song_id]["expires"] = song["expires"]
                self.songs[song_id]["get_time"] = song["get_time"]
            else:
                self.songs[song_id] = song
        if not self.is_index_valid:
            return
        current = self.songs.get(str(self.list[self.index]), {})
        if current.get("mp3_url"):
            self.refresh_url_flag = True

    def _advance_on_playback_failure(self):
        """Skip after playback failure; stop when no other track would be tried."""
        if len(self.list) <= 1:
            self.playing_flag = False
            self.stop()
            return
        current_id = self.playing_id
        self.stop()
        self.next_idx()
        if not self.is_index_valid or self.playing_id == current_id:
            self.playing_flag = False
            self.stop()
            return
        self.replay()

    @staticmethod
    def _reap_process(process):
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        for name in ("stdin", "stdout", "stderr"):
            stream = getattr(process, name, None)
            if stream is not None:
                with contextlib.suppress(OSError, ValueError):
                    stream.close()

    def stop(self):
        with getattr(self, "_playback_lock", contextlib.nullcontext()):
            self.playback_token = getattr(self, "playback_token", 0) + 1
            self.playing_flag = False
            self.process_location = self.process_length = 0
            self.playback_state = "stopped"
            process = self.popen_handler
            if process is not None and process.poll() is None:
                if (
                    self.current_backend == "mpg123"
                    and process.stdin
                    and not process.stdin.closed
                ):
                    with contextlib.suppress(OSError, ValueError):
                        process.stdin.write(b"Q\n")
                        process.stdin.flush()
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        process.wait(timeout=2)
                self._reap_process(process)
            if os.name == "nt":
                self._close_mpg123_pipe()

    def _close_mpg123_pipe(self):
        stream = getattr(self, "mpg123_command_stream", None)
        if stream is not None:
            stream.close()
            self.mpg123_command_stream = None

    def tune_volume(self, up=0):
        self.set_volume(self.info["playing_volume"] + up)

    def set_volume(self, volume):
        """Set absolute volume (0-100+). Persists to storage even when stopped."""
        volume = max(0, int(volume))
        if not self.popen_handler or self.popen_handler.poll() is not None:
            self.info["playing_volume"] = volume
            return
        if self.current_backend == "mpv":
            if self._send_mpv_command(["set_property", "volume", volume]):
                self.info["playing_volume"] = volume
            return
        try:
            self.popen_handler.stdin.write(f"V {volume}\n".encode())
            self.popen_handler.stdin.flush()
            self.info["playing_volume"] = volume
        except Exception as e:
            log.warn(e)

    def switch(self):
        if not self.popen_handler:
            return
        if self.popen_handler.poll() is not None:
            return
        if self.current_backend == "mpv":
            if not self._send_mpv_command(["set_property", "pause", self.playing_flag]):
                return False
            self.playing_flag = not self.playing_flag
            self.playback_state = "playing" if self.playing_flag else "paused"
            self.playinfo_starts = time.time()
            self.build_playinfo()
            return
        self.playing_flag = not self.playing_flag
        if not self.popen_handler.stdin.closed:
            self.popen_handler.stdin.write(b"P\n")
            self.popen_handler.stdin.flush()

        self.playinfo_starts = time.time()
        self.build_playinfo()

    @staticmethod
    def _is_flac_url(url):
        if not url:
            return False
        url_part = url.split("?", 1)[0].lower()
        return url_part.endswith(".flac") or ".flac/" in url_part

    @classmethod
    def _is_flac_song(cls, song, url):
        song_type = str(song.get("type") or "").lower()
        level = str(song.get("level") or "").lower()
        return bool(
            song_type == "flac"
            or level in {"lossless", "hires", "jymaster"}
            or cls._is_flac_url(url)
        )

    def _play_backend(self, song, url):
        backend = str(self.config.get("player_backend") or "mpg123").lower()
        if backend == "mpv" or self._is_flac_song(song, url):
            return "mpv"
        if (
            os.name == "nt"
            and not self.config_mpg123
            and self._backend_executable("mpv")
        ):
            # Native mpg123 1.33.x buffers remote feedback for several seconds.
            # Keep explicit mpg123 options; empty Windows defaults use real mpv IPC.
            return "mpv"
        return "mpg123"

    @staticmethod
    def _backend_executable(name):
        executable = shutil.which(name + ".exe" if os.name == "nt" else name)
        if executable or os.name != "nt":
            return executable
        bundled = Path(__file__).resolve().parent.parent / name / (name + ".exe")
        return str(bundled) if bundled.is_file() else None

    def _new_mpv_ipc_path(self):
        if os.name == "nt":
            return (
                rf"\\.\pipe\musicbox-mpv-{os.getpid()}-{id(self)}-{self.playback_token}"
            )
        return os.path.join(
            tempfile.gettempdir(),
            f"musicbox-mpv-{os.getpid()}-{id(self)}-{self.playback_token}.sock",
        )

    def _cleanup_mpv_ipc(self, path=None):
        path = path if path is not None else getattr(self, "mpv_ipc_path", "")
        if not path:
            return
        try:
            if os.name != "nt":
                os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            log.warn(e)
        if path == getattr(self, "mpv_ipc_path", ""):
            self.mpv_ipc_path = ""

    def _is_current_playback(self, token, process=None):
        if token != self.playback_token:
            return False
        return not (process is not None and self.popen_handler is not process)

    def _send_mpv_command(self, command):
        path = getattr(self, "mpv_ipc_path", "")
        if not path:
            return False
        try:
            if os.name == "nt":
                return windows_mpv.command(path, command).get("error") == "success"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(path)
                payload = json.dumps({"command": command}).encode() + b"\n"
                client.sendall(payload)
            return True
        except OSError as e:
            log.warn(e)
            return False

    def _request_mpv_property(self, name, path=None):
        path = path or getattr(self, "mpv_ipc_path", "")
        if not path:
            return None
        try:
            if os.name == "nt":
                response = windows_mpv.command(path, ["get_property", name])
                return (
                    response.get("data") if response.get("error") == "success" else None
                )
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(0.2)
                client.connect(path)
                payload = json.dumps({"command": ["get_property", name]}).encode()
                client.sendall(payload + b"\n")
                data = b""
                while b"\n" not in data:
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    data += chunk
            if not data:
                return None
            response = json.loads(data.split(b"\n", 1)[0].decode())
            if response.get("error") == "success":
                return response.get("data")
        except (OSError, ValueError):
            pass
        return None

    @staticmethod
    def _format_sample_rate(rate):
        if not rate:
            return ""
        khz = float(rate) / 1000
        text = f"{khz:.1f}".rstrip("0").rstrip(".")
        return f"{text}kHz"

    @staticmethod
    def _format_sample_bits(fmt):
        fmt = str(fmt or "").lower()
        if fmt == "float":
            return "32bit"
        if fmt == "double":
            return "64bit"
        digits = "".join(ch for ch in fmt if ch.isdigit())
        return f"{digits}bit" if digits else ""

    @classmethod
    def _format_audio_params(cls, params):
        if not isinstance(params, dict):
            return ""
        parts = [
            cls._format_sample_rate(params.get("samplerate")),
            cls._format_sample_bits(params.get("format")),
        ]
        return " ".join(part for part in parts if part)

    def _refresh_mpv_audio_info(self, ipc_path, token, process):
        if not self._is_current_playback(token, process):
            return False
        label = self._format_audio_params(
            self._request_mpv_property("audio-params", ipc_path)
        )
        if not label or not self._is_current_playback(token, process):
            return False
        song = self.current_song
        if not song:
            return False
        song["audio_quality"] = label
        return True

    @staticmethod
    def _capture_diagnostics(process, stream=None):
        diagnostics = deque(maxlen=32)
        stream = process.stderr if stream is None else stream

        def read():
            if stream is None:
                return
            try:
                for raw in iter(lambda: stream.readline(1024), b""):
                    diagnostics.append(raw.decode("utf-8", errors="replace").lower())
            except (OSError, ValueError):
                return

        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        return diagnostics, thread

    @staticmethod
    def _audio_initialization_failed(diagnostics):
        return any(
            marker in line
            for line in tuple(diagnostics)
            for marker in (
                "output format",
                "audio output",
                "audio_out",
                "audio device",
                "[ao",
                "out123",
                "driver module",
            )
        )

    def _backend_failure(self, kind):
        messages = {
            "backend_unavailable": "本地播放器不可用，请检查 mpv / mpg123 安装路径",
            "audio_initialization": "音频输出初始化失败，请检查播放器输出模块或设备",
            "media_error": "音频加载或解码失败，请检查网络或音源可用性",
        }
        self.stop()
        self.playback_error = {"kind": kind, "message": messages[kind]}
        self.playback_state = "error"
        # Backend diagnostics can contain signed URLs. Report only a bounded category.
        log.warning(
            "%s (backend=%s, song=%s)", kind, self.current_backend, self.playing_id
        )
        notify(messages[kind])

    def run_mpv(self, on_exit, url, expires=-1, get_time=-1, duration=0, token=0):
        if not url:
            if self._is_current_playback(token):
                self.notify_copyright_issue()
                self._advance_on_playback_failure()
            return
        process = None
        ipc_path = ""
        diagnostics = deque(maxlen=32)
        reader = None
        try:
            with getattr(self, "_playback_lock", contextlib.nullcontext()):
                if not self._is_current_playback(token):
                    return
                self.current_backend = "mpv"
                ipc_path = self._new_mpv_ipc_path()
                self.mpv_ipc_path = ipc_path
                para = [
                    self._backend_executable("mpv") or "mpv",
                    "--no-video",
                    "--really-quiet",
                    "--msg-level=all=warn",
                    f"--input-ipc-server={ipc_path}",
                    f"--volume={self.info['playing_volume']}",
                    *self.config_mpv,
                    url,
                ]
                process = subprocess.Popen(
                    para,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                self.popen_handler = process
                self.process_location = self.process_length = 0
            diagnostics, reader = self._capture_diagnostics(process, process.stdout)
            audio_info_loaded = False
            while process.poll() is None:
                with getattr(self, "_playback_lock", contextlib.nullcontext()):
                    if not self._is_current_playback(token, process):
                        return
                    position = self._request_mpv_property("time-pos", ipc_path)
                    length = self._request_mpv_property("duration", ipc_path)
                    paused = self._request_mpv_property("pause", ipc_path)
                    buffering = self._request_mpv_property("paused-for-cache", ipc_path)
                    if not self._is_current_playback(token, process):
                        return
                    if isinstance(position, (int, float)):
                        self.process_location = max(0, position)
                    self.process_length = (
                        max(0, length) if isinstance(length, (int, float)) else 0
                    )
                    if isinstance(paused, bool):
                        self.playing_flag = not paused
                    self.playback_state = (
                        "paused"
                        if paused
                        else "buffering"
                        if buffering
                        else "loading"
                        if position is None
                        else "playing"
                    )
                    if not audio_info_loaded:
                        audio_info_loaded = self._refresh_mpv_audio_info(
                            ipc_path, token, process
                        )
                time.sleep(0.2)
            if reader:
                reader.join(timeout=0.3)
            with getattr(self, "_playback_lock", contextlib.nullcontext()):
                if not self._is_current_playback(token, process):
                    return
                if process.returncode == 0:
                    self.next()
                elif self._audio_initialization_failed(diagnostics):
                    self._backend_failure("audio_initialization")
                elif (
                    expires >= 0 and get_time >= 0 and time.time() >= expires + get_time
                ):
                    self.refresh_urls()
                    if self.refresh_url_flag:
                        self.stop()
                        self.playing_flag = True
                        self.start_playing(lambda: 0, self.current_song)
                        self.refresh_url_flag = False
                    else:
                        self._backend_failure("media_error")
                else:
                    self._backend_failure("media_error")
        except OSError:
            if self._is_current_playback(token):
                self._backend_failure("backend_unavailable")
        finally:
            self._reap_process(process)
            if reader:
                reader.join(timeout=0.3)
            self._cleanup_mpv_ipc(ipc_path)

    def run_mpg123(self, on_exit, url, expires=-1, get_time=-1, token=None):
        token = self.playback_token if token is None else token
        process = commands = None
        diagnostics = deque(maxlen=32)
        reader = None
        try:
            if not self._is_current_playback(token):
                return
            if not url:
                self.notify_copyright_issue()
                self._advance_on_playback_failure()
                return
            executable = self._backend_executable("mpg123") or "mpg123"
            parameters = self.config_mpg123
            if os.name == "nt":
                parameters = windows_mpg123.output_parameters(executable, parameters)
            para = [executable, "-R", *parameters]
            command_path = rf"\\.\pipe\musicbox-mpg123-{os.getpid()}-{id(self)}-{token}"
            if os.name == "nt":
                para += ["--fifo", command_path]
            with getattr(self, "_playback_lock", contextlib.nullcontext()):
                if not self._is_current_playback(token):
                    return
                self.current_backend = "mpg123"
                process = subprocess.Popen(
                    para,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                self.popen_handler = process
            diagnostics, reader = self._capture_diagnostics(process)
            assert process.stdin is not None and process.stdout is not None
            protocol_output = process.stdout
            if os.name == "nt":
                commands = windows_mpg123.open_commands(process, command_path)
                with getattr(self, "_playback_lock", contextlib.nullcontext()):
                    if not self._is_current_playback(token, process):
                        return
                    process.stdin.close()
                    process.stdin = commands
                    self.mpg123_command_stream = commands
            lines = queue.Queue()

            def read_protocol():
                try:
                    for raw in iter(protocol_output.readline, b""):
                        lines.put(raw.decode("utf-8", errors="replace").strip())
                except (OSError, ValueError):
                    pass
                finally:
                    lines.put(None)

            threading.Thread(target=read_protocol, daemon=True).start()
            self.tune_volume()
            process.stdin.write(b"L " + url.encode("utf-8") + b"\n")
            process.stdin.flush()
            frames = 0
            finished = False
            while self._is_current_playback(token, process):
                if self._audio_initialization_failed(diagnostics):
                    self._backend_failure("audio_initialization")
                    return
                try:
                    line = lines.get(timeout=0.1)
                except queue.Empty:
                    if process.poll() is not None:
                        break
                    continue
                if line is None:
                    break
                if line.startswith("@F "):
                    parts = line.split()
                    with getattr(self, "_playback_lock", contextlib.nullcontext()):
                        if not self._is_current_playback(token, process):
                            return
                        self.process_location = float(parts[3])
                        self.process_length = float(parts[3]) + float(parts[4])
                        frames += 1
                elif line == "@P 1":
                    self.playback_state = "paused"
                    self.playing_flag = False
                elif line == "@P 2":
                    self.playback_state = "playing"
                    self.playing_flag = True
                elif line == "@P 0":
                    finished = frames > 0
                    break
                elif line.startswith("@E"):
                    break
            with getattr(self, "_playback_lock", contextlib.nullcontext()):
                if not self._is_current_playback(token, process):
                    return
                if finished:
                    self.next()
                elif self._audio_initialization_failed(diagnostics):
                    self._backend_failure("audio_initialization")
                elif (
                    expires >= 0 and get_time >= 0 and time.time() >= expires + get_time
                ):
                    self.refresh_urls()
                    if self.refresh_url_flag:
                        self.stop()
                        self.playing_flag = True
                        self.start_playing(lambda: 0, self.current_song)
                        self.refresh_url_flag = False
                    else:
                        self._backend_failure("media_error")
                else:
                    self._backend_failure("media_error")
        except (OSError, ValueError):
            if self._is_current_playback(token):
                self._backend_failure(
                    "audio_initialization"
                    if self._audio_initialization_failed(diagnostics)
                    else "backend_unavailable"
                )
        finally:
            self._reap_process(process)
            if reader:
                reader.join(timeout=0.3)
            if commands is not None:
                commands.close()
                if getattr(self, "mpg123_command_stream", None) is commands:
                    self.mpg123_command_stream = None

    def download_lyric(self, is_transalted=False):
        key = "lyric" if not is_transalted else "tlyric"
        song_id = str(self.playing_id)
        token = self.playback_token
        song = self.songs.get(song_id)
        if song is None or song.get(key):
            return
        if not is_transalted:
            lyric = self.api.song_lyric(song["song_id"])
        else:
            lyric = self.api.song_tlyric(song["song_id"])
        if token == self.playback_token and str(self.playing_id) == song_id:
            song[key] = lyric

    def download_song(self, song_id, song_name, artist, url, song_type="", level=""):
        def write_path(song_id, path):
            self.songs[str(song_id)]["cache"] = path

        self.cache.add(song_id, song_name, artist, url, write_path, song_type, level)
        self.cache.start_download()

    def start_playing(self, on_exit, args):
        """
        Runs the given args in subprocess.Popen, and then calls the function
        on_exit when the subprocess completes.
        on_exit is a callable object, and args is a lists/tuple of args
        that would give to subprocess.Popen.
        """
        # print(args.get('cache'))
        url = (
            args["cache"]
            if "cache" in args and os.path.isfile(args["cache"])
            else args["mp3_url"]
        )
        backend = self._play_backend(args, url)
        runner = self.run_mpv if backend == "mpv" else self.run_mpg123
        self.playback_token += 1
        token = self.playback_token
        self.process_location = self.process_length = 0
        self.playback_error = None
        self.playback_state = "loading"
        if "cache" in args and os.path.isfile(args["cache"]):
            if backend == "mpv":
                thread = threading.Thread(
                    target=runner,
                    args=(
                        on_exit,
                        args["cache"],
                        -1,
                        -1,
                        args.get("duration", 0),
                        token,
                    ),
                )
            else:
                thread = threading.Thread(
                    target=runner, args=(on_exit, args["cache"], -1, -1, token)
                )
        else:
            player_args = (on_exit, args["mp3_url"], args["expires"], args["get_time"])
            if backend == "mpv":
                player_args = (*player_args, args.get("duration", 0), token)
            else:
                player_args = (*player_args, token)
            thread = threading.Thread(
                target=runner,
                args=player_args,
            )
            cache_thread = threading.Thread(
                target=self.download_song,
                args=(
                    args["song_id"],
                    args["song_name"],
                    args["artist"],
                    args["mp3_url"],
                    args.get("type", ""),
                    args.get("level", ""),
                ),
            )
            cache_thread.start()
        thread.daemon = True
        thread.start()
        self.MUSIC_THREADS.append(thread)
        self.MUSIC_THREADS = [i for i in self.MUSIC_THREADS if i.is_alive()]
        lyric_download_thread = threading.Thread(
            target=self.download_lyric, daemon=True
        )
        lyric_download_thread.start()
        tlyric_download_thread = threading.Thread(
            target=self.download_lyric, args=(True,), daemon=True
        )
        tlyric_download_thread.start()
        # returns immediately after the thread starts
        return thread

    def replay(self):
        if not self.is_index_valid:
            self.stop()
            if self.end_callback:
                log.debug("Callback")
                self.end_callback()
            return

        if not self.current_song:
            return

        self.playing_flag = True
        self.playinfo_starts = time.time()
        self.build_playinfo()
        self.notify_playing()
        self.start_playing(lambda: 0, self.current_song)

    def shuffle_order(self):
        del self.order[:]
        self.order.extend(list(range(0, len(self.list))))
        random.shuffle(self.order)
        self.info["random_index"] = 0

    def new_player_list(self, type, title, datalist, offset):
        self.info["player_list_type"] = type
        self.info["player_list_title"] = title
        # self.info['idx'] = offset
        self.info["player_list"] = []
        self.info["playing_order"] = []
        self.info["random_index"] = 0
        self.add_songs(datalist)

    def append_songs(self, datalist):
        self.add_songs(datalist)

    # switch_flag为true表示：
    # 在播放列表中 || 当前所在列表类型不在"songs"、"djprograms"、"fmsongs"中
    def play_or_pause(self, idx, switch_flag):
        if self.is_empty:
            return

        # if same "list index" and "playing index" --> same song :: pause/resume it
        if self.index == idx and switch_flag:
            if not self.popen_handler or self.popen_handler.poll() is not None:
                self.replay()
            else:
                self.switch()
        else:
            self.info["idx"] = idx
            self.stop()
            self.replay()

    def _swap_song(self):
        now_songs = self.order.index(self.index)
        self.order[0], self.order[now_songs] = self.order[now_songs], self.order[0]

    def _need_to_shuffle(self):
        playing_order = self.order
        random_index = self.info["random_index"]
        return bool(
            random_index >= len(playing_order)
            or playing_order[random_index] != self.index
        )

    def next_idx(self):
        if not self.is_index_valid:
            return self.stop()
        playlist_len = len(self.list)

        if self.mode == Player.MODE_ORDERED:
            # make sure self.index will not over
            if self.info["idx"] < playlist_len:
                self.info["idx"] += 1

        elif self.mode == Player.MODE_ORDERED_LOOP:
            self.info["idx"] = (self.index + 1) % playlist_len

        elif self.mode == Player.MODE_SINGLE_LOOP:
            self.info["idx"] = self.info["idx"]

        else:
            playing_order_len = len(self.order)
            if self._need_to_shuffle():
                self.shuffle_order()
                # When you regenerate playing list
                # you should keep previous song same.
                self._swap_song()
                playing_order_len = len(self.order)

            self.info["random_index"] += 1

            # Out of border
            if self.mode == Player.MODE_RANDOM_LOOP:
                self.info["random_index"] %= playing_order_len

            # Random but not loop, out of border, stop playing.
            if self.info["random_index"] >= playing_order_len:
                self.info["idx"] = playlist_len
            else:
                self.info["idx"] = self.order[self.info["random_index"]]

        if self.playing_song_changed_callback is not None:
            self.playing_song_changed_callback()

    def next(self):
        self.stop()
        self.next_idx()
        self.replay()

    def prev_idx(self):
        if not self.is_index_valid:
            self.stop()
            return
        playlist_len = len(self.list)

        if self.mode == Player.MODE_ORDERED:
            if self.info["idx"] > 0:
                self.info["idx"] -= 1

        elif self.mode == Player.MODE_ORDERED_LOOP:
            self.info["idx"] = (self.info["idx"] - 1) % playlist_len

        elif self.mode == Player.MODE_SINGLE_LOOP:
            self.info["idx"] = self.info["idx"]

        else:
            playing_order_len = len(self.order)
            if self._need_to_shuffle():
                self.shuffle_order()
                playing_order_len = len(self.order)

            self.info["random_index"] -= 1
            if self.info["random_index"] < 0:
                if self.mode == Player.MODE_RANDOM:
                    self.info["random_index"] = 0
                else:
                    self.info["random_index"] %= playing_order_len
            self.info["idx"] = self.order[self.info["random_index"]]

        if self.playing_song_changed_callback is not None:
            self.playing_song_changed_callback()

    def prev(self):
        self.stop()
        self.prev_idx()
        self.replay()

    def shuffle(self):
        self.stop()
        self.info["playing_mode"] = Player.MODE_RANDOM
        self.shuffle_order()
        self.info["idx"] = self.info["playing_order"][self.info["random_index"]]
        self.replay()

    def seek(self, position, relative=False):
        """Seek the current track. Returns True on success.

        Only mpv (which exposes an IPC ``seek`` command) is supported;
        mpg123 streaming has no reliable second->frame mapping, so the daemon
        surfaces a structured ``not_supported`` for it.
        """
        if not self.popen_handler or self.popen_handler.poll() is not None:
            return False
        if self.current_backend != "mpv":
            return False
        mode = "relative" if relative else "absolute"
        if not self._send_mpv_command(["seek", position, mode]):
            return False
        actual = self._request_mpv_property("time-pos")
        if isinstance(actual, (int, float)):
            self.process_location = max(0, actual)
        return True

    def volume_up(self):
        self.tune_volume(5)

    def volume_down(self):
        self.tune_volume(-5)

    def update_size(self):
        self.ui.update_size()
        self.build_playinfo()

    def cache_song(self, song_id, song_name, artist, song_url, song_type="", level=""):
        def on_exit(song_id, path):
            self.songs[str(song_id)]["cache"] = path
            self.cache.enable = False

        self.cache.enable = True
        self.cache.add(song_id, song_name, artist, song_url, on_exit, song_type, level)
        self.cache.start_download()
