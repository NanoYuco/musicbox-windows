"""Column-consistent output for the single-cell Windows curses implementation.

PDCurses' Win32 and ANSI writers disagree about wide-character padding. Keep
its input/window handling, but emit changed rows in physical columns ourselves.
"""

import ctypes
import curses
import weakref
from ctypes import wintypes

from .scrollstring import _units, truelen


class WindowsScreen:
    def __init__(self, window):
        self._window = window
        # This adapter positions the physical cursor in refresh(). PDCurses'
        # cursor can therefore differ even when the window is clean. Keep
        # wgetch/get_wch from invoking its writer just to reconcile cursors;
        # curs_set still controls visibility, including during text input.
        self._window.leaveok(True)
        self._rows = []
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self._kernel.CreateFileW.restype = wintypes.HANDLE
        self._kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel.GetConsoleMode.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self._kernel.WriteConsoleW.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        self._handle = self._kernel.CreateFileW(
            "CONOUT$", 0xC0000000, 3, None, 3, 0, None
        )
        if self._handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        self._close = weakref.finalize(self, self._kernel.CloseHandle, self._handle)

    def __getattr__(self, name):
        return getattr(self._window, name)

    @staticmethod
    def _style(attributes):
        foreground, background = curses.pair_content(curses.pair_number(attributes))
        # PDCurses' first sixteen color indexes use Win32's BGR bit order.
        ansi = (0, 4, 2, 6, 1, 5, 3, 7)
        codes = ["0"]
        for value, base, default in ((foreground, 30, 39), (background, 40, 49)):
            if value < 0:
                codes.append(str(default))
            elif value < 16:
                codes.append(str(base + ansi[value % 8] + (60 if value >= 8 else 0)))
            else:
                codes.append(f"{base + 8};5;{value}")
        for flag, code in (
            (curses.A_BOLD, "1"),
            (curses.A_DIM, "2"),
            (curses.A_UNDERLINE, "4"),
            (curses.A_REVERSE, "7"),
        ):
            if attributes & flag:
                codes.append(code)
        return "\x1b[" + ";".join(codes) + "m"

    def _render_row(self, cells):
        # Decode UTF-16 surrogate pairs before grouping combining/emoji units.
        text = "".join(chr(value & curses.A_CHARTEXT) for value in cells)
        text = text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
        parts = []
        column = padding = 0
        previous = None
        for unit in _units(text):
            native_width = len(unit.encode("utf-16-le")) // 2
            if padding:
                padding -= native_width
            else:
                attributes = cells[column] & curses.A_ATTRIBUTES
                if attributes != previous:
                    parts.append(self._style(attributes))
                    previous = attributes
                parts.append(unit)
                # These cells reserve a wide glyph's columns in PDCurses; they
                # must never become authored spaces in the terminal stream.
                padding = max(0, truelen(unit) - native_width)
            column += native_width
        return "".join(parts)

    def refresh(self):
        rows, columns = self._window.getmaxyx()
        cursor = self._window.getyx()
        current = [
            tuple(self._window.inch(row, col) for col in range(columns))
            for row in range(rows)
        ]
        self._window.move(*cursor)
        # Clear wgetch's touched-window refresh trigger; leaveok above disables
        # its cursor-only trigger. Physical rows/cursor remain adapter-owned.
        # Do not call doupdate/physical refresh.
        self._window.noutrefresh()
        output = []
        for row, cells in enumerate(current):
            if row >= len(self._rows) or cells != self._rows[row]:
                output.append(f"\x1b[{row + 1};1H" + self._render_row(cells))
        output.append(f"\x1b[0m\x1b[{cursor[0] + 1};{cursor[1] + 1}H")
        mode = wintypes.DWORD()
        if not self._kernel.GetConsoleMode(self._handle, ctypes.byref(mode)):
            raise ctypes.WinError(ctypes.get_last_error())
        # Enable VT while writing and disable right-edge wrapping. Restore the
        # exact buffer mode afterwards, including when output fails.
        if not self._kernel.SetConsoleMode(
            self._handle, (mode.value | 0x0005) & ~0x0002
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            buffer = ctypes.create_unicode_buffer("".join(output))
            length = ctypes.sizeof(buffer) // ctypes.sizeof(ctypes.c_wchar) - 1
            written = wintypes.DWORD()
            if not self._kernel.WriteConsoleW(
                self._handle, buffer, length, ctypes.byref(written), None
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if written.value != length:
                raise OSError("Incomplete Windows console write")
            self._rows = current
        finally:
            self._kernel.SetConsoleMode(self._handle, mode.value)

    def getch(self):
        self.refresh()
        return self._window.getch()

    def get_wch(self):
        self.refresh()
        return self._window.get_wch()
