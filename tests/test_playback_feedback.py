"""Backend state, command failures and superseded playback regressions."""

import io
import os
import shutil
import threading
from types import SimpleNamespace

import pytest

from NEMbox import player as module
from NEMbox.daemon import MusicboxDaemon
from NEMbox.player import NullUi
from tests.test_player import _player_with_queue
from tests.test_windows_platform import isolated_env, python


class RunningProcess:
    def poll(self):
        return None


def test_mpv_position_and_length_come_from_media_not_wall_clock(monkeypatch):
    p = _player_with_queue([1])
    p.ui = NullUi()
    p.config = SimpleNamespace(get=lambda _: [])
    p.playback_token = 0
    p.mpv_ipc_path = ""
    p.process_location = p.process_length = 0
    samples = []

    class MediaProcess:
        returncode = 0
        stderr = io.BytesIO()
        stdout = io.BytesIO()
        ticks = 0

        def poll(self):
            self.ticks += 1
            return None if self.ticks <= 3 else 0

    monkeypatch.setattr(module.subprocess, "Popen", lambda *_a, **_k: MediaProcess())
    p._request_mpv_property = lambda name, *_: {
        "time-pos": 7.25,
        "duration": 30.0,
        "pause": True,
        "paused-for-cache": False,
        "core-idle": False,
        "audio-params": {"samplerate": 44100, "format": "s16"},
    }.get(name)
    monkeypatch.setattr(
        module.time,
        "sleep",
        lambda _: samples.append(
            (p.process_location, p.process_length, p.playing_flag)
        ),
    )
    p.next = lambda: None
    p.run_mpv(lambda: None, "sample.flac", duration=999)
    assert samples[-1] == (7.25, 30.0, False)


def test_failed_pause_does_not_claim_success():
    p = _player_with_queue([1])
    p.current_backend, p.popen_handler = "mpv", RunningProcess()
    p._send_mpv_command = lambda _: False
    p.build_playinfo = lambda: None
    p.switch()
    assert p.playing_flag is True


def test_failed_volume_does_not_persist_unapplied_value():
    p = _player_with_queue([1])
    p.current_backend, p.popen_handler = "mpv", RunningProcess()
    p._send_mpv_command = lambda _: False
    p.set_volume(15)
    assert p.info["playing_volume"] == 60
    p.tune_volume(-10)
    assert p.info["playing_volume"] == 60


def test_seek_reads_backend_position_instead_of_guessing():
    p = _player_with_queue([1])
    p.current_backend, p.popen_handler = "mpv", RunningProcess()
    p.process_location, p.process_length = 10, 100
    p._send_mpv_command = lambda _: True
    p._request_mpv_property = lambda *_: 19.75
    assert p.seek(20)
    assert p.process_location == 19.75


def test_stop_without_live_process_clears_stale_time_and_flag():
    p = _player_with_queue([1])
    p.process_location, p.process_length, p.playback_token = 17, 30, 1
    p.stop()
    assert (p.process_location, p.process_length, p.playing_flag) == (0, 0, False)
    assert p.playback_token > 1


def test_daemon_exposes_actual_loading_and_backend_failure():
    p = _player_with_queue([1])
    p.process_location = p.process_length = 0
    p.current_backend = "mpv"
    p.popen_handler = RunningProcess()
    p.playback_state = "buffering"
    p.playback_error = None
    daemon = MusicboxDaemon.__new__(MusicboxDaemon)
    daemon.player = p
    assert daemon._status()["state"] == "buffering"
    p.popen_handler = None
    p.playback_state = "error"
    p.playback_error = {
        "kind": "audio_initialization",
        "message": "audio device initialization failed",
    }
    assert daemon._status().get("error") == p.playback_error


def test_slow_lyric_response_cannot_be_written_to_next_song():
    p = _player_with_queue([1, 2])
    p.playback_token = 1

    def finish_after_switch(_song_id):
        p.info["idx"] = 1
        p.playback_token = 2
        return ["old song's words"]

    p.api = SimpleNamespace(song_lyric=finish_after_switch)
    p.download_lyric()
    assert "old song's words" not in p.songs["2"].get("lyric", [])


def test_background_playinfo_cannot_draw_over_input_page():
    p = _player_with_queue([1])
    p.songs["1"].update(artist="artist", album_name="album", quality="MP3")
    p.playinfo_starts = 0
    drawing_threads = []
    p.ui = SimpleNamespace(
        build_playinfo=lambda *_a, **_k: drawing_threads.append(threading.get_ident())
    )
    worker = threading.Thread(target=p.build_playinfo)
    worker.start()
    worker.join()
    assert drawing_threads == []


def test_stale_mpv_response_cannot_overwrite_new_song(monkeypatch):
    p = _player_with_queue([1, 2])
    p.ui, p.config = NullUi(), SimpleNamespace(get=lambda _: [])
    p.playback_token, p.mpv_ipc_path = 1, ""
    p.process_location, p.process_length = 0, 0

    class MediaProcess:
        stderr, stdout = io.BytesIO(), io.BytesIO()
        returncode = 0
        ticks = 0

        def poll(self):
            self.ticks += 1
            return None if self.ticks < 3 else 0

        def terminate(self):
            self.ticks = 10

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(module.subprocess, "Popen", lambda *_a, **_k: MediaProcess())

    def response(name, *_):
        p.playback_token = 2
        p.process_location, p.process_length = 0, 0
        return {"time-pos": 22, "duration": 30}.get(name)

    p._request_mpv_property = response
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    p.run_mpv(lambda: None, "old.flac", duration=999, token=1)
    assert (p.process_location, p.process_length) == (0, 0)


def test_old_ipc_cleanup_keeps_new_playback_address():
    p = _player_with_queue([1])
    p.playback_token = 1
    old_path = p._new_mpv_ipc_path()
    p.playback_token = 2
    p.mpv_ipc_path = p._new_mpv_ipc_path()
    current_path = p.mpv_ipc_path
    p._cleanup_mpv_ipc(old_path)
    assert p.mpv_ipc_path == current_path and p.mpv_ipc_path


def test_stale_audio_quality_cannot_be_written_to_next_song():
    p = _player_with_queue([1, 2])
    process = RunningProcess()
    p.popen_handler, p.playback_token = process, 1

    def delayed(_name, _path):
        p.info["idx"], p.playback_token = 1, 2
        return {"samplerate": 44100, "format": "s16"}

    p._request_mpv_property = delayed
    p._refresh_mpv_audio_info("old", 1, process)
    assert "audio_quality" not in p.songs["2"]


def test_same_song_can_restart_after_process_exits():
    p = _player_with_queue([1])
    p.songs["1"].update(artist="a", album_name="b", quality="MP3")
    p.playing_flag = False
    p.popen_handler = SimpleNamespace(poll=lambda: 0)
    p.build_playinfo = p.notify_playing = lambda: None
    p.start_playing = lambda *_: None
    p.play_or_pause(0, True)
    assert p.playing_flag


@pytest.mark.skipif(os.name != "nt", reason="native Windows backend diagnosis")
@pytest.mark.parametrize(
    "backend,options",
    [("mpg123", ["-o", "missing-output"]), ("mpv", ["--ao=missing-output"])],
)
def test_local_output_error_is_not_reported_as_copyright(tmp_path, backend, options):
    sample = os.environ.get("MUSICBOX_TEST_MP3")
    if not sample or not shutil.which(backend):
        pytest.skip("real local backend and sample required")
    code = (
        r"""
import json, threading, time
from NEMbox import player as module
from NEMbox.player import Player, NullUi
notices=[]
module.notify=notices.append
p=Player(ui=NullUi())
p.config.config['player_backend']['value']=BACKEND
p.config.config[BACKEND+'_parameters']['value']=OPTIONS
p.new_player_list('songs','local',[dict(song_id=1,song_name='fixture',artist='',album_name='',quality='MP3',mp3_url='',cache=SAMPLE,duration=30,lyric=[''],tlyric=[''])],0)
p.info['idx']=0
try:
    p.replay()
    deadline=time.monotonic()+4
    while time.monotonic()<deadline and p.playing_flag:
        time.sleep(.05)
    print(json.dumps(dict(notices=notices,error=getattr(p,'playback_error',None),playing=p.playing_flag),ensure_ascii=False),flush=True)
    assert not any('版权' in n for n in notices), notices
    assert getattr(p,'playback_error',{}), 'no readable local failure'
    assert p.playback_error['kind']=='audio_initialization',p.playback_error
    assert not p.playing_flag
finally:
    p.stop()
    p.cache.quit()
""".replace("BACKEND", repr(backend))
        .replace("OPTIONS", repr(options))
        .replace("SAMPLE", repr(sample))
    )
    result = python(code, isolated_env(tmp_path), timeout=15)
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="native mpg123 output initialization")
def test_explicit_mpg123_empty_parameters_initialize_available_win32_output(tmp_path):
    sample = os.environ.get("MUSICBOX_TEST_MP3")
    if not sample or not shutil.which("mpg123"):
        pytest.skip("real mpg123 and local sample required")
    code = r"""
import threading, time
from NEMbox.player import Player, NullUi
p=Player(ui=NullUi())
p.config.config['mpg123_parameters']['value']=[]
p.playing_flag=True
worker=threading.Thread(target=p.run_mpg123,args=(lambda:None,SAMPLE),daemon=True)
worker.start()
try:
    deadline=time.monotonic()+6
    while time.monotonic()<deadline and p.process_location<.1:
        time.sleep(.05)
    assert p.process_location>.1, ('no native output initialized',p.process_location)
    assert p.config_mpg123==[], 'must not rewrite configuration'
finally:
    p.stop()
    worker.join(timeout=3)
assert not worker.is_alive()
""".replace("SAMPLE", repr(sample))
    result = python(code, isolated_env(tmp_path), timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="native stale playback lifecycle")
def test_cancelled_native_worker_cannot_launch_after_stop(tmp_path):
    sample = os.environ.get("MUSICBOX_TEST_MP3")
    if not sample or not shutil.which("mpg123"):
        pytest.skip("real backend and local sample required")
    code = r"""
import threading, time
from NEMbox import player as module
from NEMbox.player import Player, NullUi
p=Player(ui=NullUi())
p.config.config['mpg123_parameters']['value']=['-o','win32']
p.new_player_list('songs','local',[dict(song_id=1,song_name='fixture',artist='',album_name='',quality='MP3',mp3_url='',cache=SAMPLE,duration=30,lyric=[''],tlyric=[''])],0)
p.info['idx']=0
release=threading.Event()
original=module.threading.Thread
def deferred(*args,**kwargs):
    target=kwargs['target']
    if target.__name__=='run_mpg123':
        def blocked(*a,**k):
            release.wait(3)
            target(*a,**k)
        kwargs['target']=blocked
    return original(*args,**kwargs)
module.threading.Thread=deferred
worker=p.start_playing(lambda:None,p.current_song)
p.stop()
release.set()
try:
    worker.join(timeout=1)
    assert not worker.is_alive(), 'superseded worker launched playback after stop'
    assert p.popen_handler is None
finally:
    p.stop()
    worker.join(timeout=3)
""".replace("SAMPLE", repr(sample))
    result = python(code, isolated_env(tmp_path), timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="native Windows playback feedback")
def test_windows_existing_empty_parameters_have_continuous_real_feedback(tmp_path):
    # A 4.7-second buffered burst must fail even though position eventually grows.
    sample = os.environ.get("MUSICBOX_TEST_MP3")
    if not sample or not (shutil.which("mpv") and shutil.which("mpg123")):
        pytest.skip("real mpg123, mpv and MUSICBOX_TEST_MP3 required")
    code = r"""
import json, time
from NEMbox.player import Player, NullUi
p = Player(ui=NullUi())
p.config.config['notifier']['value'] = False
p.config.config['player_backend']['value'] = 'mpg123'
p.config.config['mpg123_parameters']['value'] = []
song = dict(song_id=1,song_name='本地 MP3',artist='fixture',album_name='',quality='MP3',mp3_url='',cache=SAMPLE,expires=-1,get_time=-1,duration=999,lyric=[''],tlyric=[''])
p.new_player_list('songs','local',[song],0)
p.info['idx']=0
p.info['playing_volume']=10
started=time.monotonic()
updates=[]
try:
    p.replay()
    previous=-1
    while time.monotonic()-started < 10.5:
        position=p.process_location
        if position != previous:
            updates.append((round(time.monotonic()-started,3),round(position,3)))
            previous=position
        time.sleep(.05)
    moving=[u for u in updates if u[1] > 0]
    print(json.dumps(dict(backend=p.current_backend,length=p.process_length,updates=updates),ensure_ascii=False),flush=True)
    assert moving and moving[0][0] < 2, ('first feedback too late',updates)
    assert moving[-1][0] > 9.5, ('no continuing feedback',updates)
    assert max(b[0]-a[0] for a,b in zip(moving,moving[1:])) < 2, updates
    assert 29 < p.process_length < 31, p.process_length
finally:
    p.stop()
    p.cache.quit()
""".replace("SAMPLE", repr(sample))
    result = python(code, isolated_env(tmp_path), timeout=25)
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
