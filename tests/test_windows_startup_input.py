"""Catch native input overwriting rows the Windows output adapter has cached."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows console API")

MODES = ["legacy", "wt", "conemu", "legacy256", "wt256"]
MENU_TEXT = [
    "排行榜",
    "艺术家",
    "新碟上架",
    "精选歌单",
    "我的歌单",
    "我的云盘",
    "主播电台",
    "每日推荐歌曲",
    "每日推荐歌单",
    "私人FM",
]


def run_console(root, mode, scenario):
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
    env.pop("WT_SESSION", None)
    env.pop("ConEmuANSI", None)
    if mode in {"wt", "wt256"}:
        env["WT_SESSION"] = "musicbox-owned-startup-test"
    elif mode == "conemu":
        env["ConEmuANSI"] = "ON"
    env["TERM"] = "xterm-256color" if mode.endswith("256") else "xterm"
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    destination = root / "actual-output.json"
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("windows_startup_worker.py")),
            str(destination),
            scenario,
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
    assert destination.exists(), f"worker exit={code}; missing actual console output"
    data = json.loads(destination.read_text(encoding="utf-8"))
    assert code == 0, data.get("error", data)
    assert data["size"] == [24, 80]
    assert data["hashes_before"] == data["hashes_after"]
    assert data["ended_curses"]
    return data


def assert_menu(frame):
    # Literal labels checked independently of Menu.datalist and rendering helpers.
    rows = [row["visible"].strip() for row in frame["rows"]]
    assert rows[7] == "网易云音乐", (frame["label"], rows)
    for index, text in enumerate(MENU_TEXT):
        prefix = "-> " if index == frame["index"] else ""
        assert rows[index + 9] == f"{prefix}{index}. {text}", (frame["label"], rows)
    assert all(not row for row in rows[19:]), (frame["label"], rows)
    assert all(not rows[row] for row in [0, 2, 3, 4, 5, 6, 8]), (frame["label"], rows)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("scenario", ["empty", "saved"])
def test_first_native_read_keeps_whole_menu_while_waiting_and_after_j_j(
    tmp_path, mode, scenario
):
    # Missing cursor coordination lets wgetch bypass the adapter and corrupt
    # untouched rows, including 3-9 after only rows 0-2 have been selected.
    data = run_console(tmp_path, mode, scenario)
    assert data["saved_song_loaded"] == ("短曲" if scenario == "saved" else None)
    frames = data["frames"]
    assert len(frames) == 15
    for frame in frames:
        assert_menu(frame)
        assert not frame["cursor"]["visible"]
        if frame["label"].endswith("-waiting"):
            assert frame["blocked"], frame["label"]
        if frame.get("action", "key") is None and frame["label"].endswith("-after"):
            assert frame["value"] == -1
            assert frame["elapsed"] >= 0.65, frame
    assert frames[-1]["index"] == 2
    assert [f["value"] for f in frames if "value" in f] == [
        -1,
        -1,
        -1,
        ord("j"),
        ord("j"),
        ord("q"),
    ]
    for number, frame in enumerate(frames):
        header = (
            # Existing layout intentionally leaves two columns after quality.
            "已停止 MP3  短曲 - 歌手  < 专辑 >"
            if scenario == "saved" and number >= 3
            else ""
        )
        assert frame["rows"][1]["visible"].strip() == header, frame["label"]
    assert data["stopped"]


@pytest.mark.parametrize("mode", MODES)
def test_native_wide_input_preserves_page_and_visible_cursor(tmp_path, mode):
    # Removing coordination must also fail for wget_wch, including the rows
    # outside the edited input and the physical cursor after a wide character.
    data = run_console(tmp_path, mode, "input")
    assert data["input_value"] == "中文B"
    assert data["cancel_value"] == ""
    before = [
        f
        for f in data["frames"]
        if f["label"].startswith("get_wch-") and f["label"].endswith("-before")
    ]
    expected_inputs = ["", "", "中", "中文", "中文A", "中文", "中文B", "", "旧", "旧字"]
    expected_columns = [16, 16, 18, 20, 21, 20, 21, 16, 18, 20]
    assert len(before) == len(expected_inputs)
    for frame, text, column in zip(
        before, expected_inputs, expected_columns, strict=True
    ):
        after_label = frame["label"].removesuffix("-before") + "-after"
        after = next(f for f in data["frames"] if f["label"] == after_label)
        for reading in (frame, after):
            assert reading["rows"][10]["visible"].strip() == text, reading["label"]
            assert reading["cursor"] == {"position": [10, column], "visible": True}, (
                reading["label"]
            )
    for frame in data["frames"]:
        if frame["label"] == "input-menu-restored":
            assert_menu(frame)
            assert not frame["cursor"]["visible"]
            continue
        rows = [row["visible"].strip() for row in frame["rows"]]
        assert rows[5] in {"搜索音乐", "取消输入"}, (frame["label"], rows)
        assert all(not text for row, text in enumerate(rows) if row not in {5, 10}), (
            frame["label"],
            rows,
        )
        if frame["label"].endswith("-waiting"):
            assert frame["blocked"]
            assert frame["cursor"] == {"position": [10, 16], "visible": True}


@pytest.mark.parametrize("mode", ["legacy", "wt"])
def test_interrupt_finishes_native_menu_and_restores_curses(tmp_path, mode):
    data = run_console(tmp_path, mode, "interrupt")
    assert data["exit_requested"]
    assert data["stopped"]
