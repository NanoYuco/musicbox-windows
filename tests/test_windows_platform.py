"""Native Windows regressions; app imports happen only in isolated children."""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import wave
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows behavior")


def isolated_env(root):
    env = os.environ.copy()
    for key, part in {
        "XDG_CONFIG_HOME": "config",
        "XDG_DATA_HOME": "data",
        "XDG_CACHE_HOME": "cache",
        "XDG_RUNTIME_DIR": "runtime",
        "TEMP": "temp",
        "TMP": "temp",
    }.items():
        directory = root / part
        directory.mkdir(parents=True, exist_ok=True)
        env[key] = str(directory)
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    return env


def python(code, env, **kwargs):
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=kwargs.pop("timeout", 20),
        **kwargs,
    )


def test_installed_windows_terminal_dependency(tmp_path):
    # Removing the Windows dependency breaks a normal installed interpreter.
    result = python("import curses; import _curses", isolated_env(tmp_path))
    assert result.returncode == 0, result.stderr


def test_local_console_entry(tmp_path):
    # A Unix-only import must not prevent local CLI commands from starting.
    result = subprocess.run(
        [
            str(Path(sys.executable).with_name("musicbox.exe")),
            "config",
            "get",
            "music_quality",
            "--json",
        ],
        env=isolated_env(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"] is True


@pytest.mark.parametrize("abrupt", [False, True], ids=["release", "process-exit"])
def test_lock_excludes_independent_processes(tmp_path, abrupt):
    # Removing the OS lock permits two independent owners; stale files must not block reuse.
    env = isolated_env(tmp_path / "中文 状态")
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from NEMbox.daemon import acquire_lock, release_lock; "
            "fd = acquire_lock(); print('held' if fd is not None else 'busy', flush=True); "
            "input(); release_lock(fd)",
        ],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    probe = (
        "from NEMbox.daemon import acquire_lock, release_lock; fd = acquire_lock(); "
        "print('busy' if fd is None else 'held'); release_lock(fd)"
    )
    assert (
        holder.stdin is not None
        and holder.stdout is not None
        and holder.stderr is not None
    )
    try:
        assert holder.stdout.readline().strip() == "held", holder.stderr.read()
        result = python(probe, env)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "busy"
        if abrupt:
            holder.terminate()
        else:
            holder.stdin.write("\n")
            holder.stdin.flush()
        holder.wait(timeout=10)
        result = python(probe, env)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "held"
    finally:
        if holder.poll() is None:
            holder.kill()
        holder.communicate(timeout=10)


def test_default_runtime_is_user_writable(tmp_path):
    # A /tmp fallback writes outside the Windows user's runtime directory.
    env = isolated_env(tmp_path)
    env.pop("XDG_RUNTIME_DIR")
    local = tmp_path / "本地 应用"
    local.mkdir()
    env["LOCALAPPDATA"] = str(local)
    result = python(
        "import json; from NEMbox.const import Constant; "
        "print(json.dumps(Constant.runtime_dir))",
        env,
    )
    assert result.returncode == 0, result.stderr
    assert Path(json.loads(result.stdout)).is_relative_to(local)
    runtime = Path(json.loads(result.stdout))
    runtime.mkdir(parents=True)
    (runtime / "writable").write_bytes(b"ok")


def test_shared_runtime_keeps_configuration_domains_separate(tmp_path):
    # A fixed uid=0 endpoint incorrectly couples independent Windows state domains.
    first = isolated_env(tmp_path / "first")
    second = isolated_env(tmp_path / "second")
    second["XDG_RUNTIME_DIR"] = first["XDG_RUNTIME_DIR"]
    code = "import json; from NEMbox.const import Constant; print(json.dumps([Constant.lock_path, Constant.socket_path]))"
    one = python(code, first)
    two = python(code, second)
    assert one.returncode == two.returncode == 0, one.stderr + two.stderr
    locks_one, endpoint_one = json.loads(one.stdout)
    locks_two, endpoint_two = json.loads(two.stdout)
    assert locks_one != locks_two
    assert endpoint_one != endpoint_two
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from NEMbox.daemon import acquire_lock; "
            "assert acquire_lock() is not None; print('held', flush=True); input()",
        ],
        env=first,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert (
        holder.stdin is not None
        and holder.stdout is not None
        and holder.stderr is not None
    )
    try:
        assert holder.stdout.readline().strip() == "held", holder.stderr.read()
        owner = python(
            "from NEMbox.daemon import acquire_lock, release_lock; fd = acquire_lock(); print(fd is not None); release_lock(fd)",
            second,
        )
        assert owner.returncode == 0, owner.stderr
        assert owner.stdout.strip() == "True"
    finally:
        holder.communicate(input="\n", timeout=10)


def cli(env, *args):
    return subprocess.run(
        [sys.executable, "-m", "NEMbox", *args],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )


def test_foreground_daemon_communicates_across_processes(tmp_path):
    # A Unix-only transport leaves a native Windows service unreachable.
    env = isolated_env(tmp_path / "中文 服务")
    service = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os; print(os.getpid(), flush=True); "
            "from NEMbox.daemon import main; raise SystemExit(main())",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert service.stdout is not None and service.stderr is not None
    try:
        actual_pid = int(service.stdout.readline())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status = cli(env, "daemon", "status", "--json")
            assert status.returncode == 0, status.stderr
            data = json.loads(status.stdout)["data"]
            if data["running"]:
                break
            if service.poll() is not None:
                break
            time.sleep(0.05)
        assert data["running"] is True, (
            service.communicate(timeout=1)
            if service.poll() is not None
            else status.stdout
        )
        assert data["pid"] == actual_pid
        result = cli(env, "volume", "37", "--json")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["data"]["volume"] == 37
        result = cli(env, "daemon", "stop", "--json")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["data"] == {"running": False, "stopped": True}
        exit_code = service.wait(timeout=10)
        assert exit_code == 0, service.stderr.read()
    finally:
        cli(env, "daemon", "stop", "--json")
        if service.poll() is None:
            service.terminate()
        service.communicate(timeout=10)


def test_daemon_rejects_unauthenticated_loopback_control(tmp_path):
    # Loopback alone lets another local user change playback; unauthenticated JSON must do nothing.
    env = isolated_env(tmp_path)
    code = """
from NEMbox.daemon import MusicboxDaemon
d = MusicboxDaemon()
bind = d._bind
def report_port():
    bind()
    assert d._server.getsockname()[0] == "127.0.0.1"
    print(d._server.getsockname()[1], flush=True)
d._bind = report_port
raise SystemExit(d.serve())
"""
    service = subprocess.Popen(
        [sys.executable, "-c", code],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert service.stdout is not None and service.stderr is not None
    try:
        port = int(service.stdout.readline())
        preset = cli(env, "volume", "37", "--json")
        assert preset.returncode == 0, preset.stderr
        for payload in (
            {"id": 1, "method": "player.volume", "params": {"value": 99}},
            {
                "body": {"id": 1, "method": "player.volume", "params": {"value": 99}},
                "nonce": "untrusted",
                "mac": "0" * 64,
            },
        ):
            with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
                client.sendall(json.dumps(payload).encode() + b"\n")
                assert client.recv(4096) == b""
        status = cli(env, "status", "--json")
        assert status.returncode == 0, status.stderr
        assert json.loads(status.stdout)["data"]["volume"] == 37
    finally:
        cli(env, "daemon", "stop", "--json")
        if service.poll() is None:
            service.terminate()
        service.communicate(timeout=10)


def test_background_cli_lifecycle_and_tui_lock(tmp_path):
    # os.fork cannot detach a Windows daemon; CLI exit must leave a controllable service.
    env = isolated_env(tmp_path / "独立 后台")
    try:
        result = cli(env, "daemon", "status", "--json")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["data"]["running"] is False
        result = cli(env, "daemon", "start", "--json")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["data"] == {"running": True, "started": True}
        result = cli(env, "daemon", "status", "--json")
        pid = json.loads(result.stdout)["data"]["pid"]
        result = cli(env, "daemon", "start", "--json")
        assert json.loads(result.stdout)["data"] == {"running": True, "started": False}
        assert (
            json.loads(cli(env, "daemon", "status", "--json").stdout)["data"]["pid"]
            == pid
        )
        lock = python(
            "from NEMbox.daemon import acquire_lock; print(acquire_lock())", env
        )
        assert lock.returncode == 0, lock.stderr
        assert lock.stdout.strip() == "None"
        tui = cli(env)
        assert tui.returncode == 4, tui.stderr
        result = cli(env, "stop", "--json")
        assert result.returncode == 0, result.stderr
        assert (
            json.loads(cli(env, "daemon", "status", "--json").stdout)["data"]["pid"]
            == pid
        )
        result = cli(env, "daemon", "restart", "--json")
        assert result.returncode == 0, result.stderr
        new_pid = json.loads(cli(env, "daemon", "status", "--json").stdout)["data"][
            "pid"
        ]
        assert new_pid != pid
    finally:
        cli(env, "daemon", "stop", "--json")
    result = cli(env, "status", "--no-daemon-autostart", "--json")
    assert result.returncode == 4
    assert json.loads(result.stderr)["error"]["type"] == "daemon_not_running"
    lock = python(
        "from NEMbox.daemon import acquire_lock, release_lock; fd = acquire_lock(); print(fd is not None); release_lock(fd)",
        env,
    )
    assert lock.returncode == 0, lock.stderr
    assert lock.stdout.strip() == "True"


def test_failed_windows_bind_releases_owned_resources(tmp_path):
    # A failed discovery write must not strand this new listener and the shared lock.
    env = isolated_env(tmp_path)
    result = python(
        """
import os
from NEMbox.const import Constant
from NEMbox.daemon import MusicboxDaemon, acquire_lock, release_lock
os.makedirs(Constant.socket_path)
daemon = MusicboxDaemon()
try:
    daemon.serve()
except OSError:
    pass
fd = acquire_lock()
print(fd is not None)
print(daemon._server is None or daemon._server.fileno() == -1)
release_lock(fd)
""",
        env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["True", "True"]


def test_tui_owner_blocks_daemon_then_control_autostarts(tmp_path):
    # The service must honor the very same OS lock as the TUI owner in another process.
    env = isolated_env(tmp_path)
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from NEMbox.daemon import acquire_lock; "
            "assert acquire_lock() is not None; print('held', flush=True); input()",
        ],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert (
        holder.stdin is not None
        and holder.stdout is not None
        and holder.stderr is not None
    )
    try:
        assert holder.stdout.readline().strip() == "held", holder.stderr.read()
        result = cli(env, "daemon", "start", "--json")
        assert result.returncode == 1, result.stdout
        assert json.loads(result.stderr)["error"]["type"] == "api_error"
        assert (
            json.loads(cli(env, "daemon", "status", "--json").stdout)["data"]["running"]
            is False
        )
        assert cli(env).returncode == 1
    finally:
        holder.communicate(input="\n", timeout=10)
    try:
        result = cli(env, "status", "--json")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["data"]["state"] == "stopped"
        assert (
            json.loads(cli(env, "daemon", "status", "--json").stdout)["data"]["running"]
            is True
        )
    finally:
        cli(env, "daemon", "stop", "--json")


def test_real_mpv_windows_raw_json_control(tmp_path):
    # Unix sockets (or AF_PIPE framing) cannot query/control mpv's raw Windows pipe.
    if not shutil.which("mpv"):
        pytest.skip("mpv executable required for native backend integration")
    sample = tmp_path / "本地 音频.wav"
    with wave.open(str(sample), "wb") as output:
        output.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * 44100 * 30)
    result = python(
        """
import sys, threading, time, traceback
from NEMbox.player import Player, NullUi
p = Player(ui=NullUi())
p.config.config["mpv_parameters"]["value"] = ["--no-config", "--ao=null"]
p.playing_flag = True
errors = []
def play():
    try:
        p.run_mpv(lambda: None, sys.argv[1], duration=30, token=0)
    except BaseException as exc:
        errors.append(traceback.format_exc())
thread = threading.Thread(target=play)
thread.start()
def wait_property(name, expected):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        try:
            value = p._request_mpv_property(name)
        except (OSError, AttributeError):
            value = None
        if value == expected:
            return
        time.sleep(.05)
    raise AssertionError((name, expected, value, errors))
try:
    wait_property("pause", False)
    wait_property("duration", 30)
    p.switch()
    wait_property("pause", True)
    p.set_volume(37)
    wait_property("volume", 37)
    assert p.seek(8)
    position = p._request_mpv_property("time-pos")
    assert 7.5 <= position <= 8.5, position
    p.switch()
    wait_property("pause", False)
finally:
    p.stop()
    thread.join(timeout=6)
assert not thread.is_alive()
assert not errors, errors
assert p.popen_handler.poll() is not None
assert p.mpv_ipc_path == ""
""".replace("sys.argv[1]", repr(str(sample))),
        isolated_env(tmp_path),
    )
    assert result.returncode == 0, result.stderr


def test_windows_menu_registers_supported_signals(tmp_path):
    # SIGWINCH does not exist on Windows; a terminal stand-in isolates that startup branch.
    result = python(
        """
from types import SimpleNamespace
from NEMbox import menu
from NEMbox.player import Player, NullUi
screen = SimpleNamespace(keypad=lambda value: None)
menu.C.initscr = lambda: screen
menu.Ui = lambda: SimpleNamespace(y=40, screen=screen)
menu.Player = lambda ui=None: Player(ui=ui)
instance = menu.Menu()
assert instance.title == "网易云音乐"
assert instance.screen is screen
""",
        isolated_env(tmp_path),
    )
    assert result.returncode == 0, result.stderr


def test_windows_update_does_not_own_terminal_or_block_exit(tmp_path):
    # fork is unavailable; re-creating Menu in an update worker touches the terminal.
    started = time.monotonic()
    result = python(
        """
import threading
from NEMbox.menu import Menu
entered = threading.Event()
class VersionAPI:
    def get_version(self):
        assert threading.current_thread() is not threading.main_thread()
        entered.set()
        threading.Event().wait(10)
        return {"info": {"version": "0.5.3"}}
instance = Menu.__new__(Menu)
instance.api = VersionAPI()
def no_more_menus(self):
    raise AssertionError("update task constructed another Menu")
Menu.__init__ = no_more_menus
def main_menu():
    assert threading.current_thread() is threading.main_thread()
    assert entered.wait(2), "update never checked the version"
instance.start = main_menu
instance.start_fork("0.5.3")
""",
        isolated_env(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    assert time.monotonic() - started < 5, "update task blocked process exit"


@pytest.mark.parametrize("downloader", ["aria2c", "wget"])
def test_windows_cache_cancel_waits_for_owned_process(tmp_path, downloader):
    # SIGKILL is absent; swallowing AttributeError leaves the real download process running.
    result = python(
        """
import subprocess, sys
from NEMbox.cache import Cache
child = subprocess.Popen([sys._base_executable, "-c", "import time; time.sleep(60)"])
cache = Cache()
cache.aria2c = cache.wget = None
setattr(cache, DOWNLOADER, child)
try:
    cache.quit()
    assert child.poll() is not None, "owned downloader survived cancellation"
finally:
    if child.poll() is None:
        child.kill()
    child.wait(timeout=5)
""".replace("DOWNLOADER", repr(downloader)),
        isolated_env(tmp_path),
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mode", ["forged", "silent"])
def test_windows_client_authentication_and_response_timeout(tmp_path, mode):
    # A forged reply must not look successful; an idle peer must respect the request timeout.
    result = python(
        """
import json, os, socket, threading, time
from NEMbox.const import Constant
from NEMbox.daemon import send_request
from NEMbox.windows_runtime import write_endpoint
os.makedirs(Constant.runtime_dir, exist_ok=True)
with socket.socket() as server:
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    server.settimeout(3)
    write_endpoint(Constant.socket_path, server.getsockname()[1])
    def respond():
        with server.accept()[0] as connection:
            connection.settimeout(2)
            data = b""
            while b"\\n" not in data:
                data += connection.recv(4096)
            if MODE == "silent":
                time.sleep(.5)
            else:
                request = json.loads(data)
                reply = {"body": {"ok": True, "data": {}}, "nonce": request["nonce"], "mac": "0"*64}
                connection.sendall(json.dumps(reply).encode() + b"\\n")
    thread = threading.Thread(target=respond)
    thread.start()
    started = time.monotonic()
    try:
        send_request("player.status", timeout=.15)
    except (ValueError, TimeoutError) as exc:
        assert isinstance(exc, TimeoutError if MODE == "silent" else ValueError)
    else:
        raise AssertionError("untrusted or silent server accepted")
    assert time.monotonic() - started < 1
    thread.join(timeout=2)
    assert not thread.is_alive()
os.unlink(Constant.socket_path)
""".replace("MODE", repr(mode)),
        isolated_env(tmp_path),
    )
    assert result.returncode == 0, result.stderr


def test_mpv_timeout_closes_raw_pipe_handles(tmp_path):
    # A nonresponding real byte pipe must not block or leak an overlapped client handle.
    result = python(
        """
import _winapi, ctypes, gc, os, threading, time
from ctypes import wintypes
from NEMbox.player import Player
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
def handles():
    value = wintypes.DWORD()
    assert kernel.GetProcessHandleCount(ctypes.c_void_p(-1), ctypes.byref(value))
    return value.value
p = Player.__new__(Player)
before = handles()
for index in range(3):
    path = rf"\\\\.\\pipe\\musicbox-timeout-{os.getpid()}-{index}"
    handle = _winapi.CreateNamedPipe(path, _winapi.PIPE_ACCESS_DUPLEX, 0, 1, 4096, 4096, 0, 0)
    def accept():
        _winapi.ConnectNamedPipe(handle, overlapped=False)
        time.sleep(.3)
    thread = threading.Thread(target=accept)
    thread.start()
    p.mpv_ipc_path = path
    started = time.monotonic()
    try:
        assert p._request_mpv_property("pause") is None
        assert time.monotonic() - started < 1
    finally:
        thread.join(timeout=2)
        _winapi.CloseHandle(handle)
    assert not thread.is_alive()
gc.collect()
assert handles() <= before + 2, (before, handles())
""",
        isolated_env(tmp_path),
    )
    assert result.returncode == 0, result.stderr


def test_failed_mpg123_pipe_startup_reaps_owned_child(tmp_path):
    # Failing to connect the new FIFO must close and reap only its own backend.
    result = python(
        """
import subprocess, sys
from NEMbox import player as module
from NEMbox.player import Player, NullUi
launch = subprocess.Popen
def no_pipe(args, **kwargs):
    if "mpg123" in str(args[0]).lower():
        return launch([sys._base_executable, "-c", "import time; time.sleep(30)"], **kwargs)
    return launch(args, **kwargs)
module.subprocess.Popen = no_pipe
p = Player(ui=NullUi())
p.config.config["mpg123_parameters"]["value"] = ["-o", "win32"]
p.playing_flag = True
try:
    p.run_mpg123(lambda: None, "local.mp3")
    assert p.popen_handler.poll() is not None
    assert p.playing_flag is False
    assert p.popen_handler.stdin.closed and p.popen_handler.stdout.closed
finally:
    if p.popen_handler.poll() is None:
        p.popen_handler.kill()
    p.popen_handler.wait(timeout=3)
""",
        isolated_env(tmp_path),
    )
    assert result.returncode == 0, result.stderr


def test_real_mpg123_windows_remote_control(tmp_path):
    # Native mpg123 requires --fifo; stdin -R leaves playback and Q unprocessed.
    sample = os.environ.get("MUSICBOX_TEST_MP3")
    if not shutil.which("mpg123") or not sample:
        pytest.skip("mpg123 and MUSICBOX_TEST_MP3 local fixture required")
    result = python(
        """
import sys, threading, time, traceback
from NEMbox.player import Player, NullUi
p = Player(ui=NullUi())
p.config.config["mpg123_parameters"]["value"] = ["-o", "win32"]
p.playing_flag = True
errors = []
lines = []
from NEMbox import player as module
launch = module.subprocess.Popen
class ProtocolLog:
    def __init__(self, stream):
        self.stream = stream
    def readline(self):
        line = self.stream.readline()
        lines.append(line.decode("utf-8", errors="replace").strip())
        return line
    def __getattr__(self, name):
        return getattr(self.stream, name)
def record_protocol(*args, **kwargs):
    process = launch(*args, **kwargs)
    process.stdout = ProtocolLog(process.stdout)
    return process
module.subprocess.Popen = record_protocol
def play():
    try:
        p.run_mpg123(lambda: None, SAMPLE)
    except BaseException as exc:
        errors.append(traceback.format_exc())
thread = threading.Thread(target=play, daemon=True)
thread.start()
try:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and p.process_location < .1:
        time.sleep(.05)
    assert p.process_location >= .1, ("no decoded MP3 progress", errors)
    p.switch()
    time.sleep(.3)
    paused = p.process_location
    time.sleep(.4)
    assert p.process_location == paused
    p.set_volume(37)
    assert p.seek(8) is False
    p.switch()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and p.process_location <= paused:
        time.sleep(.05)
    assert p.process_location > paused, (paused, p.process_location, p.playing_flag, p.popen_handler.poll(), errors, lines[-6:])
    assert "@P 1" in lines and "@P 2" in lines, lines[-8:]
    assert "@V 37.000000%" in lines, lines[-8:]
    stopper = threading.Thread(target=p.stop, daemon=True)
    stopper.start()
    stopper.join(timeout=3)
    assert not stopper.is_alive(), "stop did not complete"
    assert p.popen_handler.poll() is not None
    assert p.popen_handler.stdin.closed
finally:
    p.playing_flag = False
    if p.popen_handler is not None:
        if p.popen_handler.poll() is None:
            p.popen_handler.kill()
        p.popen_handler.wait(timeout=3)
    thread.join(timeout=3)
assert not thread.is_alive()
assert not errors, errors
""".replace("SAMPLE", repr(sample)),
        isolated_env(tmp_path),
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
