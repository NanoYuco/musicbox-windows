"""Output-boundary regressions: virtual curses cells alone miss these bugs."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows console API")


@pytest.fixture(scope="module", params=["legacy", "wt", "conemu", "legacy256", "wt256"])
def output(request, tmp_path_factory):
    root = tmp_path_factory.mktemp("console-" + request.param)
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
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    env.pop("WT_SESSION", None)
    env.pop("ConEmuANSI", None)
    if request.param in {"wt", "wt256"}:
        env["WT_SESSION"] = "musicbox-test-console"
    elif request.param == "conemu":
        env["ConEmuANSI"] = "ON"
    env["TERM"] = "xterm-256color" if request.param.endswith("256") else "xterm"
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    destination = root / "actual-output.json"
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("windows_console_worker.py")),
            str(destination),
        ],
        cwd=root,
        env=env,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        startupinfo=startup,
    )
    try:
        code = process.wait(timeout=45)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    assert destination.exists(), f"console worker exit={code}; no output record"
    data = json.loads(destination.read_text(encoding="utf-8"))
    assert code == 0, data.get("error", data)
    assert data["size"] == [24, 80], "test requires its own 80-column console"
    assert data["hashes_before"] == data["hashes_after"]
    assert data["environment_before_init"] == data["environment_after_init"]
    return data


def test_cjk_ascii_have_real_columns_without_added_spaces(output):
    # Catches application padding becoming *extra* physical spaces on ANSI output.
    row = output["width"]
    cells = row["cells"]
    assert cells[0]["char"] == "中"
    assert cells[2]["char"] == "文", row["visible"]
    assert cells[4]["char"] == "A", row["visible"]
    assert cells[5]["char"] == "B", row["visible"]
    assert row["visible"] == "中文AB"


def test_menu_enter_back_restores_actual_screen(output):
    # Real Menu events and Ui.refresh; only remote toplist data is fixed.
    opened = [row["visible"] for row in output["menu_open"]]
    returned = [row["visible"] for row in output["menu_back"]]
    assert returned == opened
    assert opened[7].strip() == "网易云音乐"
    assert output["menu_toplists"][7]["visible"].strip() == "网易云音乐 > 排行榜"
    assert not any("ding..." in row for row in returned)


def test_unknown_known_and_incremental_progress_reach_physical_output(output):
    # Catches the reported (3003:48-:--), including every subsequent update.
    for stage in output["progress"]:
        row = stage["output"]["visible"].strip()
        assert row.endswith(stage["expected"]), (stage["label"], row)
        assert row.count("(") == row.count(")") == 1


def test_mode_labels_and_progress_head_keep_their_real_columns(output):
    labels = ["顺序播放", "顺序循环", "单曲循环", "随机播放", "随机循环"]
    for stage in output["progress"]:
        row = stage["output"]["visible"].strip()
        assert row.startswith(labels[stage["mode"]] + " ["), (stage["label"], row)
        assert row.count("[") == row.count("]") == 1
        if ">" in row:
            assert row.index("[") < row.index(">") < row.index("]"), row
        if not stage["playing"]:
            assert ">" not in row


def test_stop_erases_actual_progress_and_old_lyrics(output):
    # Catches stale :48) even when all virtual cells were already blanked.
    assert [row["visible"] for row in output["stopped"]] == ["", "", "", ""]


def test_short_colored_text_replaces_long_text_without_tail(output):
    # Catches both text and attribute-only changes lost at the refresh boundary.
    assert output["colored_long"]["visible"] == "中文AB 长尾"
    assert output["colored_short"]["visible"] == "短A"
    assert output["colored_short"]["cells"][2]["char"] == "A"
    assert (
        output["colored_long"]["cells"][0]["attr"]
        != output["colored_short"]["cells"][0]["attr"]
    )
    assert output["recolored"]["visible"] == "短A"
    assert (
        output["recolored"]["cells"][0]["attr"]
        != output["colored_short"]["cells"][0]["attr"]
    )


def test_long_lyrics_no_lyrics_and_empty_lists_clean_actual_rows(output):
    assert (
        output["lyrics"][4]["visible"].strip().startswith("Translation || 长歌词Mixed")
    )
    assert output["lyrics"][4]["cells"][79]["char"] == " "
    assert output["no_lyrics"][4]["visible"].strip() == "暂无歌词 ~>_<~"
    assert output["no_lyrics"][5]["visible"] == ""
    assert output["empty_menu"][7]["visible"].strip() == "空"
    assert output["empty_menu"][8]["visible"].strip() == "这里什么都没有 -，-"
    assert not any(row["visible"] for row in output["empty_menu"][9:])


def test_scrolling_and_resize_restore_actual_boundaries(output):
    assert (
        output["scroll_start"][9]["visible"].strip().startswith("-> 0. 中文ABC日文xyz")
    )
    assert output["scroll_later"][9]["visible"].strip().startswith("-> 0. BC日文xyz")
    assert "xyz" not in output["scroll_short"][9]["visible"]
    assert output["scroll_short"][10]["visible"] == ""
    assert output["tiny"][0]["visible"].strip() == "窗口太小，请放大窗口"
    assert [row["visible"] for row in output["restored"]] == [
        row["visible"] for row in output["menu_open"]
    ]


def test_real_curses_input_keeps_text_until_cancel(output):
    # Unicode injection tests get_wch and refresh, not a human IME candidate UI.
    assert output["input_value"] == "中文B"
    assert output["input_row"]["visible"].strip() == "中文B"
    assert output["cancel_value"] == ""
    assert output["cancel_row"]["visible"] == ""
