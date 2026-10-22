"""Observe native reads in a fresh, owned console without repairing its output."""

import ctypes as c
import curses
import hashlib
import json
import signal
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from NEMbox.config import Config
from NEMbox.menu import Menu
from NEMbox.storage import Storage
from NEMbox.windows_screen import WindowsScreen
from tests.windows_console_worker import Console, Coord, Rect, source_hashes


class BufferInfo(c.Structure):
    _fields_ = [
        ("size", Coord),
        ("cursor", Coord),
        ("attributes", c.c_ushort),
        ("window", Rect),
        ("maximum", Coord),
    ]


class CursorInfo(c.Structure):
    _fields_ = [("size", c.c_uint32), ("visible", c.c_int)]


def cursor_state(console):
    info, cursor = BufferInfo(), CursorInfo()
    console.kernel.GetConsoleScreenBufferInfo.argtypes = [
        c.c_void_p,
        c.POINTER(BufferInfo),
    ]
    console.kernel.GetConsoleCursorInfo.argtypes = [c.c_void_p, c.POINTER(CursorInfo)]
    if not console.kernel.GetConsoleScreenBufferInfo(console.handle, c.byref(info)):
        raise c.WinError(c.get_last_error())
    if not console.kernel.GetConsoleCursorInfo(console.handle, c.byref(cursor)):
        raise c.WinError(c.get_last_error())
    return {"position": [info.cursor.Y, info.cursor.X], "visible": bool(cursor.visible)}


def prepare_console(console):
    # Set the owned console's initial size BEFORE curses/Menu initialization.
    console.kernel.SetConsoleWindowInfo.argtypes = [
        c.c_void_p,
        c.c_int,
        c.POINTER(Rect),
    ]
    console.kernel.SetConsoleScreenBufferSize.argtypes = [c.c_void_p, Coord]
    for rect, size in [
        (Rect(0, 0, 39, 9), None),
        (None, Coord(80, 24)),
        (Rect(0, 0, 79, 23), None),
    ]:
        ok = (
            console.kernel.SetConsoleWindowInfo(console.handle, True, c.byref(rect))
            if rect is not None
            else console.kernel.SetConsoleScreenBufferSize(console.handle, size)
        )
        if not ok:
            raise c.WinError(c.get_last_error())


class ObservedWindow:
    """Forward every operation; observe immediately around REAL native input.

    In particular, noutrefresh/refresh are never stubbed. The observer thread
    only reads this process's console and cannot draw or call curses.
    """

    def __init__(self, window, menu, console, result, actions):
        self.window = window
        self.menu = menu
        self.console = console
        self.result = result
        self.actions = iter(actions)
        self.count = 0
        self.input_mode = False

    def __getattr__(self, name):
        return getattr(self.window, name)

    def capture(self, label, **extra):
        frame = {
            "label": label,
            "index": self.menu.index,
            "rows": [
                self.console.row(row, self.menu.ui.x) for row in range(self.menu.ui.y)
            ],
            "cursor": cursor_state(self.console),
            **extra,
        }
        self.result["frames"].append(frame)

    def read(self, name):
        self.count += 1
        label = f"{name}-{self.count}"
        # get_wch timeout falls back to a genuine getch in Ui._read_line_input.
        action = None if self.input_mode and name == "getch" else next(self.actions)
        if action == "interrupt":
            signal.raise_signal(signal.SIGINT)
        elif action is not None:
            if name == "get_wch":
                curses.unget_wch(action)
            else:
                curses.ungetch(ord(action))
        self.capture(label + "-before", action=action)
        entered, returned = threading.Event(), threading.Event()
        errors = []

        def observe_wait():
            try:
                if not entered.wait(2):
                    raise TimeoutError("native read never entered")
                time.sleep(0.2)
                self.capture(label + "-waiting", blocked=not returned.is_set())
            except BaseException:
                errors.append(traceback.format_exc())

        observer = None
        if action is None:
            self.window.timeout(800)
            observer = threading.Thread(target=observe_wait)
            observer.start()
        started = time.monotonic()
        try:
            entered.set()
            value = getattr(self.window, name)()
        except curses.error:
            value = "curses.error"
            raise
        finally:
            returned.set()
            elapsed = time.monotonic() - started
            if observer is not None:
                observer.join(timeout=3)
                if observer.is_alive() or errors:
                    raise RuntimeError(errors or "console observer did not finish")
            self.capture(label + "-after", value=value, elapsed=elapsed, action=action)
        return value

    def getch(self):
        return self.read("getch")

    def get_wch(self):
        return self.read("get_wch")


def collect(result, scenario):
    console = Console()
    menu = None
    try:
        prepare_console(console)
        config = Config()
        config.config["notifier"]["value"] = False
        config.config["input_timeout"]["value"] = 800
        if scenario == "saved":
            storage = Storage()
            storage.database["songs"] = {
                "1": {
                    "song_id": 1,
                    "song_name": "短曲",
                    "artist": "歌手",
                    "album_name": "专辑",
                    "quality": "MP3",
                    "mp3_url": "",
                    "lyric": [],
                    "tlyric": [],
                }
            }
            cast(dict[str, Any], storage.database["player_info"]).update(
                player_list=["1"],
                player_list_type="songs",
                player_list_title="本地",
                playing_order=[0],
                idx=0,
                ridx=0,
            )
            storage.save()
        menu = Menu()
        # PDCurses activates its own screen buffer during initscr. Reopen
        # CONOUT$ now so observations target that active buffer, not the
        # original buffer used only to prepare the initial console size.
        console.close()
        console = Console()
        result["size"] = [menu.ui.y, menu.ui.x]
        result["saved_song_loaded"] = menu.player.playing_name
        if scenario == "input":
            actions = [None, "中", "文", "A", "\b", "B", "\n", "旧", "字", "\x1b"]
        else:
            actions = [
                None,
                None,
                None,
                "j",
                "j",
                "interrupt" if scenario == "interrupt" else "q",
            ]
        screen = cast(WindowsScreen, menu.screen)
        observed = ObservedWindow(screen._window, menu, console, result, actions)
        screen._window = observed
        if scenario == "input":
            observed.input_mode = True
            result["input_value"] = menu.ui.get_param("搜索音乐")
            observed.capture("input-confirmed")
            result["cancel_value"] = menu.ui.get_param("取消输入")
            observed.capture("input-cancelled")
            menu.build_menu_processbar()
            observed.capture("input-menu-restored")
        else:
            # No warm-up reads, resize, manual draw, or extra refresh precedes this.
            menu.start()
            result["exit_requested"] = menu.quit
        result["stopped"] = getattr(menu, "_stopped", False)
    finally:
        if menu is not None:
            menu.stop()
            curses.echo()
            curses.nocbreak()
            curses.endwin()
            result["ended_curses"] = curses.isendwin()
        console.close()


def main():
    def hashes():
        values = source_hashes()
        for name in ("windows_startup_worker.py", "test_windows_startup_input.py"):
            values["tests/" + name] = hashlib.sha256(
                Path(__file__).with_name(name).read_bytes()
            ).hexdigest()
        return values

    result = {"frames": [], "hashes_before": hashes(), "python": sys.version}
    try:
        collect(result, sys.argv[2])
    except BaseException:
        result["error"] = traceback.format_exc()
    finally:
        result["hashes_after"] = hashes()
        Path(sys.argv[1]).write_text(
            json.dumps(result, ensure_ascii=True, indent=2), encoding="utf-8"
        )
    return 1 if "error" in result else 0


if __name__ == "__main__":
    sys.exit(main())
