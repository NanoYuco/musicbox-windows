"""Loading/lyrics must reach Windows output without observer refreshes."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_ui_loading_lyrics import LOADING

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows console API")


@pytest.fixture(scope="module", params=["legacy", "wt"])
def physical_output(request, tmp_path_factory):
    root = tmp_path_factory.mktemp("loading-lyrics-" + request.param)
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
        directory.mkdir(exist_ok=True)
        env[key] = str(directory)
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", TERM="xterm")
    env.pop("WT_SESSION", None)
    env.pop("ConEmuANSI", None)
    if request.param == "wt":
        env["WT_SESSION"] = "musicbox-owned-loading-lyrics-test"
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    destination = root / "actual-output.json"
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("windows_loading_lyrics_worker.py")),
            str(destination),
        ],
        cwd=root,
        env=env,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        startupinfo=startup,
    )
    try:
        code = process.wait(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    assert destination.exists(), f"worker exit={code}; missing actual output"
    data = json.loads(destination.read_text(encoding="utf-8"))
    assert code == 0, data.get("error", data)
    assert data["size"] == [24, 80]
    assert data["hashes_before"] == data["hashes_after"]
    return data


def test_playlist_request_sees_clean_loading_without_changing_other_cells(
    physical_output,
):
    # A missing clear breaks real cells and must fail before data returns.
    for stage in physical_output["loading"]:
        assert stage["during"][7]["visible"].strip() == LOADING
        for row in range(24):
            if row != 7:
                assert stage["during"][row] == stage["before"][row]
        assert stage["ready"][7]["visible"].strip() == stage["title"] + " > 音乐"
        assert "新歌曲" in stage["ready"][9]["visible"]
        assert stage["back"] == stage["before"]


def test_cached_colon_lines_pause_resume_last_and_rewind_reach_console(physical_output):
    for frame in physical_output["lyrics"]:
        if frame["label"] == "normal-dot":
            continue
        current, following = (
            ("末句译 || 末句", "")
            if frame["label"].startswith("last")
            else ("初句译 || 初句", "次句译 || 次句")
        )
        assert (frame["current"], frame["next"]) == (current, following)
        assert sorted(
            frame["rows"][row]["visible"].strip() for row in (4, 5)
        ) == sorted([current, following])
        assert "[" not in frame["rows"][4]["visible"] + frame["rows"][5]["visible"]
        current_row = next(
            row for row in (4, 5) if frame["rows"][row]["visible"].strip() == current
        )
        colored = next(
            cell for cell in frame["rows"][current_row]["cells"] if cell["char"].strip()
        )
        assert colored["attr"] & 0x0F == 4  # Windows FOREGROUND_RED


def test_normal_dot_song_keeps_original_red_white_rows(physical_output):
    frame = physical_output["lyrics"][-1]
    assert frame["rows"][4]["visible"].strip() == "我迎着秋风几缕"
    assert frame["rows"][5]["visible"].strip() == "化开冬日残存的忧郁"
    for row, color in [(4, 7), (5, 4)]:
        cell = next(c for c in frame["rows"][row]["cells"] if c["char"].strip())
        assert cell["attr"] & 0x0F == color


def test_stop_clears_physical_progress_and_lyrics(physical_output):
    assert [row["visible"] for row in physical_output["stopped"][3:7]] == [
        "",
        "",
        "",
        "",
    ]
