"""Read only this test subprocess's console, after real Ui/Menu refreshes.

This is not desktop automation or a screenshot. The parent gives each worker
its own console and isolated configuration before any MusicBox import.
"""

import ctypes as c
import curses
import hashlib
import importlib.metadata
import json
import os
import sys
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from NEMbox import scrollstring as scrolling
from NEMbox.menu import Menu


class Coord(c.Structure):
    _fields_ = [("X", c.c_short), ("Y", c.c_short)]


class Rect(c.Structure):
    _fields_ = [
        ("Left", c.c_short),
        ("Top", c.c_short),
        ("Right", c.c_short),
        ("Bottom", c.c_short),
    ]


class Character(c.Union):
    _fields_ = [("UnicodeChar", c.c_wchar), ("AsciiChar", c.c_char)]


class Cell(c.Structure):
    _fields_ = [("Char", Character), ("Attributes", c.c_ushort)]


class Console:
    def __init__(self):
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateFileW.argtypes = [
            c.c_wchar_p,
            c.c_uint32,
            c.c_uint32,
            c.c_void_p,
            c.c_uint32,
            c.c_uint32,
            c.c_void_p,
        ]
        self.kernel.CreateFileW.restype = c.c_void_p
        self.kernel.ReadConsoleOutputW.argtypes = [
            c.c_void_p,
            c.POINTER(Cell),
            Coord,
            Coord,
            c.POINTER(Rect),
        ]
        self.kernel.ReadConsoleOutputW.restype = c.c_int
        self.kernel.CloseHandle.argtypes = [c.c_void_p]
        self.handle = self.kernel.CreateFileW(
            "CONOUT$", 0xC0000000, 3, None, 3, 0, None
        )
        if self.handle == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())

    def row(self, row, columns):
        cells = (Cell * columns)()
        rect = Rect(0, row, columns - 1, row)
        if not self.kernel.ReadConsoleOutputW(
            self.handle, cells, Coord(columns, 1), Coord(0, 0), c.byref(rect)
        ):
            raise c.WinError(c.get_last_error())
        raw = [
            {"char": cell.Char.UnicodeChar, "attr": cell.Attributes} for cell in cells
        ]
        # COMMON_LVB_TRAILING_BYTE is the console's physical continuation cell.
        # It is not an authored space. Keep every other space, including bugs.
        visible = "".join(cell["char"] for cell in raw if not cell["attr"] & 0x0200)
        return {"visible": visible.rstrip(), "cells": raw}

    def close(self):
        self.kernel.CloseHandle(self.handle)


def source_hashes():
    repo = Path(__file__).resolve().parents[1]
    paths = [*sorted((repo / "NEMbox").glob("*.py")), Path(__file__)]
    return {
        str(path.relative_to(repo)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }


def observe(menu, console):
    menu.screen.refresh()
    return [console.row(row, menu.ui.x) for row in range(menu.ui.y)]


def collect(result):
    menu = Menu()
    console = None
    try:
        curses.resize_term(24, 80)
        menu.ui.update_size()
        console = Console()
        result["size"] = [menu.ui.y, menu.ui.x]
        result["environment_after_init"] = {
            key: os.environ.get(key) for key in ("WT_SESSION", "ConEmuANSI", "TERM")
        }
        menu.ui.addstr(0, 0, "中文AB")
        result["width"] = observe(menu, console)[0]
        menu.screen.clear()
        menu.screen.refresh()
        menu.api = SimpleNamespace(
            toplists=[
                "飙升榜",
                "新歌榜",
                "原创榜",
                "热歌榜",
                "网易云古典榜",
                "网易电音榜",
                "网易云中文说唱榜",
                "小天才榜",
                "实时分享榜",
                "网易云全球说唱榜",
            ]
        )
        menu.stack = [[menu.datatype, menu.title, menu.datalist, 0, 0]]
        menu.build_menu_processbar()
        result["menu_open"] = observe(menu, console)
        menu.enter_page_event()
        menu.build_menu_processbar()
        result["menu_toplists"] = observe(menu, console)
        menu.back_page_event()
        menu.build_menu_processbar()
        result["menu_back"] = observe(menu, console)

        song = {
            "song_id": 1,
            "song_name": "歌曲Hitchcock",
            "artist": "歌手",
            "lyric": [],
        }
        # Literal, independently checked times. This includes the precise
        # unknown -> known -> third-second sequence reported by the user.
        cases = [
            ("unknown", 0, 0, True, 0, "(00:00/--:--)"),
            ("known", 0, 228, True, 0, "(00:00/03:48)"),
            ("one", 1, 228, True, 0, "(00:01/03:48)"),
            ("two", 2, 228, True, 0, "(00:02/03:48)"),
            ("three", 3, 228, True, 0, "(00:03/03:48)"),
            ("nine", 9, 228, True, 0, "(00:09/03:48)"),
            ("ten", 10, 228, True, 0, "(00:10/03:48)"),
            ("before_minute", 59, 228, True, 0, "(00:59/03:48)"),
            ("minute", 60, 228, True, 0, "(01:00/03:48)"),
            ("paused", 60, 228, False, 0, "(01:00/03:48)"),
            ("paused_again", 60, 228, False, 0, "(01:00/03:48)"),
            ("resumed", 61, 228, True, 0, "(01:01/03:48)"),
            ("seek_forward", 70, 228, True, 0, "(01:10/03:48)"),
            ("seek_back", 8, 228, True, 0, "(00:08/03:48)"),
            ("new_unknown", 0, 0, True, 0, "(00:00/--:--)"),
            ("new_known", 0, 30, True, 0, "(00:00/00:30)"),
            ("ordered_loop", 3, 30, True, 1, "(00:03/00:30)"),
            ("single_loop", 3, 30, True, 2, "(00:03/00:30)"),
            ("random", 3, 30, True, 3, "(00:03/00:30)"),
            ("random_loop", 3, 30, True, 4, "(00:03/00:30)"),
        ]
        result["progress"] = []
        for label, position, duration, playing, mode, expected in cases:
            if label == "new_unknown":
                song = dict(song, song_id=2)
            menu.ui.build_process_bar(song, position, duration, playing, mode)
            result["progress"].append(
                {
                    "label": label,
                    "position": position,
                    "duration": duration,
                    "playing": playing,
                    "mode": mode,
                    "expected": expected,
                    "output": observe(menu, console)[3],
                }
            )
        menu.ui.build_process_bar({}, 0, 0, False, 0)
        result["stopped"] = observe(menu, console)[3:7]

        menu.ui.addstr(0, 0, "中文AB 长尾", curses.color_pair(1))
        result["colored_long"] = observe(menu, console)[0]
        menu.ui._clear_rows(0, 1)
        menu.ui.addstr(0, 0, "短A", curses.color_pair(3))
        result["colored_short"] = observe(menu, console)[0]
        menu.ui.addstr(0, 0, "短A", curses.color_pair(2))
        result["recolored"] = observe(menu, console)[0]
        song = dict(
            song,
            song_id=3,
            lyric=["[00:00.00]" + "长歌词Mixed" * 30, "[00:20.00]后句"],
            tlyric=["[00:00.00]Translation", "[00:20.00]Next"],
        )
        menu.ui.build_process_bar(song, 1, 228, True, 0)
        result["lyrics"] = observe(menu, console)
        menu.ui.build_process_bar(
            dict(song, song_id=4, lyric=[], tlyric=[]), 1, 228, True, 0
        )
        result["no_lyrics"] = observe(menu, console)
        menu.ui.build_menu("songs", "空", [], 0, 0, 10, 100)
        result["empty_menu"] = observe(menu, console)
        songs = [dict(song, song_name="中文ABC日文xyz" * 12, artist="", album_name="")]
        old_clock = scrolling.time
        try:
            scrolling.time = lambda: 100
            menu.ui.build_menu("songs", "滚动", songs, 0, 0, 10, 100)
            result["scroll_start"] = observe(menu, console)
            scrolling.time = lambda: 104
            menu.ui.build_menu("songs", "滚动", songs, 0, 0, 10, 100)
            result["scroll_later"] = observe(menu, console)
            menu.ui.build_menu(
                "songs", "短", [dict(songs[0], song_name="短")], 0, 0, 10, 100
            )
            result["scroll_short"] = observe(menu, console)
        finally:
            scrolling.time = old_clock
        curses.resize_term(10, 36)
        menu.ui.update_size()
        menu.build_menu_processbar()
        result["tiny"] = observe(menu, console)
        curses.resize_term(24, 80)
        menu.ui.update_size()
        menu.build_menu_processbar()
        result["restored"] = observe(menu, console)
        for key in reversed("中文A\bB\n"):
            curses.unget_wch(key)
        result["input_value"] = menu.ui._read_line_input(10, 16)
        # No refresh added here: detect damage caused by input's implicit refresh.
        result["input_row"] = console.row(10, menu.ui.x)
        for key in reversed("旧字\x1b"):
            curses.unget_wch(key)
        result["cancel_value"] = menu.ui._read_line_input(10, 16)
        result["cancel_row"] = observe(menu, console)[10]
    finally:
        if console is not None:
            console.close()
        menu.stop()
        curses.endwin()


def main():
    result = {
        "started": time.time(),
        "pid": os.getpid(),
        "python": sys.version,
        "windows_curses": importlib.metadata.version("windows-curses"),
        "environment_before_init": {
            key: os.environ.get(key) for key in ("WT_SESSION", "ConEmuANSI", "TERM")
        },
        "hashes_before": source_hashes(),
        "evidence_kind": "own console output cells; no native GUI screenshot",
    }
    try:
        collect(result)
    except BaseException:
        result["error"] = traceback.format_exc()
    finally:
        result["hashes_after"] = source_hashes()
        result["ended"] = time.time()
        Path(sys.argv[1]).write_text(
            json.dumps(result, ensure_ascii=True, indent=2), encoding="utf-8"
        )
    return 1 if "error" in result else 0


if __name__ == "__main__":
    sys.exit(main())
