#!/usr/bin/env python
# @Author: omi
# @Date:   2014-08-24 21:51:57
# KenHuang:
# 1.增加显示颜色自定义；
# 2.调整显示格式；
"""
网易云音乐 Ui
"""

import contextlib
import curses
import importlib
import os
import re
import unicodedata
from collections.abc import Callable
from typing import Any

from . import logger
from .config import Config
from .scrollstring import _units, scrollstring, truelen, truelen_cut
from .storage import Storage
from .utils import decode_terminal_input

log = logger.getLogger(__name__)

_BACKSPACE_KEYS = {8, 127, 263, 330, getattr(curses, "KEY_BACKSPACE", 259)}


def _is_backspace_key(ch) -> bool:
    if isinstance(ch, str):
        return len(ch) == 1 and ord(ch) in (8, 127)
    return ch in _BACKSPACE_KEYS


def _is_enter_key(ch) -> bool:
    if isinstance(ch, str):
        return ch in ("\n", "\r")
    return ch in (curses.KEY_ENTER, 10, 13)


def _is_escape_key(ch) -> bool:
    if isinstance(ch, str):
        return ch == "\x1b"
    return ch == 27


def _safe_curs_set(visibility):
    with contextlib.suppress(curses.error):
        curses.curs_set(visibility)


def playinfo_song_start(indented_startcol, prefix, quality):
    return indented_startcol + truelen(prefix + quality) + 2


try:
    dbus: Any | None = importlib.import_module("dbus")
except ImportError:
    dbus = None
    log.warn("Qt dbus module is not installed.")
    log.warn("Osdlyrics is not available.")


def break_substr(s, start, max_len=80):
    res = []
    while s:
        line = truelen_cut(s, max_len)
        if not line:
            break
        res.append(line)
        s = s[len(line) :]
    return "\n{}".format(" " * start).join(res)


def break_str(s, start, max_len=80):
    res = []
    for substr in s.splitlines():
        res.append(break_substr(substr, start, max_len))
    return "\n{}".format(" " * start).join(res)


class Ui:
    def __init__(self):
        self.should_exit: Callable[[], bool] = lambda: False
        self.screen = curses.initscr()
        self._single_cell_curses = False
        if os.name == "nt":
            probe = curses.newpad(1, 4)
            probe.addstr(0, 0, "中")
            self._single_cell_curses = probe.getyx()[1] == 1
            if self._single_cell_curses:
                from .windows_screen import WindowsScreen

                self.screen = WindowsScreen(self.screen)
        curses.start_color()
        if Config().get("curses_transparency"):
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_GREEN, -1)
            curses.init_pair(2, curses.COLOR_CYAN, -1)
            curses.init_pair(3, curses.COLOR_RED, -1)
            curses.init_pair(4, curses.COLOR_YELLOW, -1)
        else:
            colors = Config().get("colors")
            if (
                "TERM" in os.environ
                and os.environ["TERM"] == "xterm-256color"
                and colors
            ):
                curses.use_default_colors()
                for i in range(1, 6):
                    color = colors["pair" + str(i)]
                    curses.init_pair(i, color[0], color[1])
                self.screen.bkgd(32, curses.color_pair(5))
            else:
                curses.init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK)
                curses.init_pair(2, curses.COLOR_CYAN, curses.COLOR_BLACK)
                curses.init_pair(3, curses.COLOR_RED, curses.COLOR_BLACK)
                curses.init_pair(4, curses.COLOR_YELLOW, curses.COLOR_BLACK)
        # term resize handling
        self.config = Config()
        self.y, self.x = self.screen.getmaxyx()
        self.playerX = 1  # terminalsize.get_terminal_size()[1] - 10
        self.playerY = 0
        self.update_margin()
        self.update_space()
        self.lyric = ""
        self.now_lyric = ""
        self.post_lyric = ""
        self.now_lyric_index = 0
        self.now_tlyric_index = 0
        self.tlyric = ""
        self.storage = Storage()
        self.newversion = False

    def addstr(self, *args):
        if len(args) == 1:
            y, x = self.screen.getyx()
            text, attributes = args[0], ()
        else:
            y, x, text, *attributes = args
        rows, columns = self.screen.getmaxyx()
        x = max(0, x)
        if not 0 <= y < rows or x >= columns - 1:
            return
        # Curses must receive Unicode, and must never wrap at the right edge.
        # Explicit multi-line comment bodies are laid out by their caller.
        text = "".join(
            c if c.isprintable() or c in "\u200d\ufe0f" else " " for c in str(text)
        )
        text = truelen_cut(unicodedata.normalize("NFC", text), columns - x - 1)
        if text:
            if getattr(self, "_single_cell_curses", False):
                # Reserve layout cells for PDCurses' single-cell storage. The
                # Windows output adapter omits these continuation cells when
                # emitting Unicode, rather than adding visible spaces.
                for unit in _units(text):
                    width = truelen(unit)
                    if not width:
                        continue
                    native_width = len(unit.encode("utf-16-le")) // 2
                    if native_width > width:
                        unit, native_width = "□", 1
                    self.screen.addstr(
                        y, x, unit + " " * (width - native_width), *attributes
                    )
                    x += width
                self.screen.move(y, x)
            else:
                self.screen.addstr(y, x, text, *attributes)

    def _clear_rows(self, first, last):
        rows, _ = self.screen.getmaxyx()
        for row in range(max(0, first), min(rows, last)):
            self.screen.move(row, 0)
            self.screen.clrtoeol()

    def _small_window(self):
        rows, columns = self.screen.getmaxyx()
        return rows < 14 or columns < 40

    def _show_small_window(self):
        self.screen.erase()
        self.addstr(0, 0, "窗口太小，请放大窗口")
        self.screen.refresh()

    def _read_key(self):
        self.screen.timeout(100)
        while not getattr(self, "should_exit", lambda: False)():
            key = self.screen.getch()
            if key != -1:
                return key
        return 27

    def update_margin(self):
        # Left margin
        self.left_margin_ratio = self.config.get("left_margin_ratio")
        if self.left_margin_ratio == 0:
            self.startcol = 0
        else:
            self.startcol = max(int(float(self.x) / self.left_margin_ratio), 0)
        self.indented_startcol = max(self.startcol - 3, 0)
        # Right margin
        self.right_margin_ratio = self.config.get("right_margin_ratio")
        if self.right_margin_ratio == 0:
            self.endcol = self.x - 1
        else:
            self.endcol = max(
                int(float(self.x) - float(self.x) / self.right_margin_ratio),
                self.startcol + 1,
            )

        self.indented_endcol = max(self.endcol - 3, 0)
        self.endcol = min(self.endcol, self.x - 1)
        self.content_width = max(0, self.endcol - self.startcol - 1)

    def build_playinfo(
        self, song_name, artist, album_name, quality, start, pause=False, state=None
    ):
        curses.noecho()
        _safe_curs_set(0)
        # refresh top 2 line
        self._clear_rows(1, 3)
        if self._small_window() or not song_name:
            return
        prefix = "_ _ z Z Z " if pause else "♫  ♪ ♫  ♪ "
        if state in {"stopped", "error"}:
            prefix = "播放失败 " if state == "error" else "已停止 "
        self.addstr(1, self.indented_startcol, prefix + quality, curses.color_pair(3))

        if artist or album_name:
            song_info = f"{song_name}{self.space}{artist}  < {album_name} >"
        else:
            song_info = song_name

        song_start = min(
            playinfo_song_start(self.indented_startcol, prefix, quality),
            self.indented_endcol - 1,
        )
        if truelen(song_info) <= self.endcol - song_start:
            self.addstr(
                1,
                song_start,
                song_info,
                curses.color_pair(4),
            )
        else:
            song_info = scrollstring(song_info + " ", start)
            self.addstr(
                1,
                song_start,
                truelen_cut(str(song_info), self.endcol - song_start),
                curses.color_pair(4),
            )

        self.screen.refresh()

    def update_lyrics(self, now_playing, lyrics, tlyrics):
        timestamp = re.compile(r"\[([0-5][0-9]):([0-5][0-9])[.:]([0-9]+)\]")

        def select_lines(lines, index):
            timed = []
            plain = []
            for line in lines:
                match = timestamp.match(line)
                if match:
                    minutes, seconds, fraction = match.groups()
                    time = int(minutes) * 60 + int(seconds) + float("0." + fraction)
                    timed.append((time, timestamp.sub("", line)))
                elif line.strip() and not line.lstrip().startswith("["):
                    plain.append(line)
            # Untimed metadata, malformed tags and empty tail lines cannot
            # claim the current playback time and advance a timed sequence.
            if timed:
                index = min(index, len(timed) - 1)
                while (
                    index + 1 < len(timed) and now_playing >= timed[index + 1][0] - 0.5
                ):
                    index += 1
                return (
                    index,
                    timed[index][1],
                    (timed[index + 1][1] if index + 1 < len(timed) else ""),
                )
            # Plain lyrics have no clock to advance them; keep them readable.
            return 0, plain[0] if plain else "", plain[1] if len(plain) > 1 else ""

        def append_translation(translated_lyric, origin_lyric):
            if translated_lyric == "" or origin_lyric == "":
                return translated_lyric + origin_lyric
            return translated_lyric + " || " + origin_lyric

        self.now_lyric_index, self.now_lyric, self.post_lyric = select_lines(
            lyrics, self.now_lyric_index
        )
        if tlyrics:
            self.now_tlyric_index, translated, next_translated = select_lines(
                tlyrics, self.now_tlyric_index
            )
            self.now_lyric = append_translation(translated, self.now_lyric)
            self.post_lyric = append_translation(next_translated, self.post_lyric)

    def build_process_bar(
        self, song, now_playing, total_length, playing_flag, playing_mode
    ):
        self._clear_rows(3, 7)
        if not song or self._small_window():
            self.now_lyric = self.post_lyric = ""
            self.now_lyric_index = self.now_tlyric_index = 0
            return
        name, artist = song["song_name"], song["artist"]
        lyrics, tlyrics = song.get("lyric", []), song.get("tlyric", [])

        curses.noecho()
        _safe_curs_set(0)
        now_playing = max(0, now_playing)
        song_key = song.get("song_id", (name, artist))
        if song_key != getattr(self, "_lyric_song", None) or now_playing < getattr(
            self, "_lyric_position", 0
        ):
            self.now_lyric_index = 0
            self.now_tlyric_index = 0
            self.now_lyric = ""
            self.post_lyric = ""
        self._lyric_song, self._lyric_position = song_key, now_playing
        process = "["
        process_bar_width = max(0, self.content_width - 24)
        ratio = min(now_playing / total_length, 1) if total_length > 0 else 0
        for i in range(0, process_bar_width):
            progress = i < ratio * process_bar_width
            at_head = (i + 1) > ratio * process_bar_width
            if progress and at_head and playing_flag:
                process += ">"
            elif progress:
                process += "="
            else:
                process += " "
        process += "] "

        now = f"{int(now_playing) // 60:02}:{int(now_playing) % 60:02}"
        total = (
            f"{int(total_length) // 60:02}:{int(total_length) % 60:02}"
            if total_length > 0
            else "--:--"
        )
        process += f"({now}/{total})"

        if playing_mode == 0:
            process = "顺序播放 " + process
        elif playing_mode == 1:
            process = "顺序循环 " + process
        elif playing_mode == 2:
            process = "单曲循环 " + process
        elif playing_mode == 3:
            process = "随机播放 " + process
        elif playing_mode == 4:
            process = "随机循环 " + process
        else:
            pass
        self.addstr(3, self.startcol - 2, process, curses.color_pair(1))
        if not lyrics:
            self.now_lyric = "暂无歌词 ~>_<~ \n"
            self.post_lyric = ""
            if dbus is not None and self.config.get("osdlyrics"):
                self.now_playing = f"{name} - {artist}\n"
        else:
            self.update_lyrics(now_playing, lyrics, tlyrics)

        if dbus is not None and self.config.get("osdlyrics"):
            try:
                bus = dbus.SessionBus().get_object("org.musicbox.Bus", "/")
                # TODO 环境问题，没有试过桌面歌词，此处需要了解的人加个刷界面操作
                if self.now_lyric == "暂无歌词 ~>_<~ \n":
                    bus.refresh_lyrics(
                        self.now_playing, dbus_interface="local.musicbox.Lyrics"
                    )
                else:
                    bus.refresh_lyrics(
                        self.now_lyric, dbus_interface="local.musicbox.Lyrics"
                    )
            except Exception as e:
                log.error(e)
                pass
        # 根据索引计算双行歌词的显示，其中当前歌词颜色为红色，下一句歌词颜色为白色；
        # 当前歌词从下一句歌词刷新颜色变换，所以当前歌词和下一句歌词位置会交替
        if self.now_lyric_index % 2 == 0:
            self.addstr(
                4, max(self.startcol - 2, 0), str(self.now_lyric), curses.color_pair(3)
            )
            self.addstr(
                5, max(self.startcol + 1, 0), str(self.post_lyric), curses.A_DIM
            )
        else:
            self.addstr(
                4, max(self.startcol - 2, 0), str(self.post_lyric), curses.A_DIM
            )
            self.addstr(
                5, max(self.startcol + 1, 0), str(self.now_lyric), curses.color_pair(3)
            )
        self.screen.refresh()

    def build_loading(self):
        _safe_curs_set(0)
        self._clear_rows(7, 8)
        self.addstr(
            7, self.startcol, "享受高品质音乐，loading...", curses.color_pair(1)
        )
        self.screen.refresh()

    def build_submenu(self, data):
        pass

    # start is the called timestamp of this function
    def build_menu(self, datatype, title, datalist, offset, index, step, start):
        # keep playing info in line 1
        curses.noecho()
        _safe_curs_set(0)
        if self._small_window():
            self._show_small_window()
            return
        self.screen.move(7, 0)
        self.screen.clrtobot()
        step = min(step, self.screen.getmaxyx()[0] - 10)
        self.addstr(7, self.startcol, title, curses.color_pair(1))

        if len(datalist) == 0:
            self.addstr(8, self.startcol, "这里什么都没有 -，-")
            return self.screen.refresh()

        if datatype == "main":
            for i in range(offset, min(len(datalist), offset + step)):
                if i == index:
                    self.addstr(
                        i - offset + 9,
                        self.indented_startcol,
                        "-> " + str(i) + ". " + datalist[i]["entry_name"],
                        curses.color_pair(2),
                    )
                else:
                    self.addstr(
                        i - offset + 9,
                        self.startcol,
                        str(i) + ". " + datalist[i]["entry_name"],
                    )

        elif datatype == "songs" or datatype == "djprograms" or datatype == "fmsongs":
            iter_range = min(len(datalist), offset + step)
            for i in range(offset, iter_range):
                if isinstance(datalist[i], str):
                    raise ValueError(datalist)
                # this item is focus
                if i == index:
                    self.addstr(i - offset + 9, 0, " " * self.startcol)
                    lead = "-> " + str(i) + ". "
                    self.addstr(
                        i - offset + 9,
                        self.indented_startcol,
                        lead,
                        curses.color_pair(2),
                    )
                    name = "{}{}{}  < {} >".format(
                        datalist[i]["song_name"],
                        self.space,
                        datalist[i]["artist"],
                        datalist[i]["album_name"],
                    )

                    # the length decides whether to scroll
                    if truelen(name) < self.content_width:
                        self.addstr(
                            i - offset + 9,
                            self.indented_startcol + len(lead),
                            name,
                            curses.color_pair(2),
                        )
                    else:
                        name = scrollstring(name + "  ", start)
                        self.addstr(
                            i - offset + 9,
                            self.indented_startcol + len(lead),
                            truelen_cut(
                                str(name), self.content_width - len(str(i)) - 2
                            ),
                            curses.color_pair(2),
                        )
                else:
                    self.addstr(i - offset + 9, 0, " " * self.startcol)
                    self.addstr(
                        i - offset + 9,
                        self.startcol,
                        truelen_cut(
                            "{}. {}{}{}  < {} >".format(
                                i,
                                datalist[i]["song_name"],
                                self.space,
                                datalist[i]["artist"],
                                datalist[i]["album_name"],
                            ),
                            self.content_width,
                        ),
                    )

            self.addstr(iter_range - offset + 9, 0, " " * self.x)

        elif datatype == "comments":
            # 被选中的评论在最下方显示全部字符，其余评论仅显示一行
            for i in range(offset, min(len(datalist), offset + step)):
                maxlength = min(
                    self.content_width, truelen(datalist[i]["comment_content"])
                )
                if i == index:
                    self.addstr(
                        i - offset + 9,
                        self.indented_startcol,
                        truelen_cut(
                            "-> "
                            + str(i)
                            + ". "
                            + datalist[i]["comment_content"].splitlines()[0],
                            self.content_width + len("-> " + str(i)),
                        ),
                        curses.color_pair(2),
                    )
                    self.addstr(
                        step + 10,
                        self.indented_startcol,
                        "-> "
                        + str(i)
                        + ". "
                        + datalist[i]["comment_content"].split(":", 1)[0]
                        + ":",
                        curses.color_pair(2),
                    )
                    body_col = self.startcol + len(str(i)) + 2
                    body = datalist[i]["comment_content"].split(":", 1)[1].lstrip()
                    body = break_str(body, 0, min(maxlength, self.x - body_col - 1))
                    for row, line in enumerate(body.splitlines(), step + 12):
                        self.addstr(row, body_col, line, curses.color_pair(2))
                else:
                    self.addstr(
                        i - offset + 9,
                        self.startcol,
                        truelen_cut(
                            str(i)
                            + ". "
                            + datalist[i]["comment_content"].splitlines()[0],
                            self.content_width,
                        ),
                    )

        elif datatype == "artists":
            for i in range(offset, min(len(datalist), offset + step)):
                if i == index:
                    self.addstr(
                        i - offset + 9,
                        self.indented_startcol,
                        "-> "
                        + str(i)
                        + ". "
                        + datalist[i]["artists_name"]
                        + self.space
                        + str(datalist[i]["alias"]),
                        curses.color_pair(2),
                    )
                else:
                    self.addstr(
                        i - offset + 9,
                        self.startcol,
                        str(i)
                        + ". "
                        + datalist[i]["artists_name"]
                        + self.space
                        + datalist[i]["alias"],
                    )

        elif datatype == "artist_info":
            for i in range(offset, min(len(datalist), offset + step)):
                if i == index:
                    self.addstr(
                        i - offset + 9,
                        self.indented_startcol,
                        "-> " + str(i) + ". " + datalist[i]["item"],
                        curses.color_pair(2),
                    )
                else:
                    self.addstr(
                        i - offset + 9,
                        self.startcol,
                        str(i) + ". " + datalist[i]["item"],
                    )

        elif datatype == "albums":
            for i in range(offset, min(len(datalist), offset + step)):
                if i == index:
                    self.addstr(
                        i - offset + 9,
                        self.indented_startcol,
                        "-> "
                        + str(i)
                        + ". "
                        + datalist[i]["albums_name"]
                        + self.space
                        + datalist[i]["artists_name"],
                        curses.color_pair(2),
                    )
                else:
                    self.addstr(
                        i - offset + 9,
                        self.startcol,
                        str(i)
                        + ". "
                        + datalist[i]["albums_name"]
                        + self.space
                        + datalist[i]["artists_name"],
                    )

        elif datatype == "recommend_lists":
            for i in range(offset, min(len(datalist), offset + step)):
                if i == index:
                    self.addstr(
                        i - offset + 9,
                        self.indented_startcol,
                        "-> " + str(i) + ". " + datalist[i]["title"],
                        curses.color_pair(2),
                    )
                else:
                    self.addstr(
                        i - offset + 9,
                        self.startcol,
                        str(i) + ". " + datalist[i]["title"],
                    )

        elif datatype in ("top_playlists", "playlists"):
            for i in range(offset, min(len(datalist), offset + step)):
                if i == index:
                    self.addstr(
                        i - offset + 9,
                        self.indented_startcol,
                        "-> "
                        + str(i)
                        + ". "
                        + datalist[i]["playlist_name"]
                        + self.space
                        + datalist[i]["creator_name"],
                        curses.color_pair(2),
                    )
                else:
                    self.addstr(
                        i - offset + 9,
                        self.startcol,
                        str(i)
                        + ". "
                        + datalist[i]["playlist_name"]
                        + self.space
                        + datalist[i]["creator_name"],
                    )

        elif datatype in ("toplists", "playlist_classes", "playlist_class_detail"):
            for i in range(offset, min(len(datalist), offset + step)):
                if i == index:
                    self.addstr(
                        i - offset + 9,
                        self.indented_startcol,
                        "-> " + str(i) + ". " + datalist[i],
                        curses.color_pair(2),
                    )
                else:
                    self.addstr(
                        i - offset + 9, self.startcol, str(i) + ". " + datalist[i]
                    )

        elif datatype == "djRadios":
            for i in range(offset, min(len(datalist), offset + step)):
                if i == index:
                    self.addstr(
                        i - offset + 8,
                        self.indented_startcol,
                        "-> " + str(i) + ". " + datalist[i]["name"],
                        curses.color_pair(2),
                    )
                else:
                    self.addstr(
                        i - offset + 8,
                        self.startcol,
                        str(i) + ". " + datalist[i]["name"],
                    )

        elif datatype == "search":
            self.screen.move(6, 1)
            self.screen.clrtobot()
            self.screen.timeout(-1)
            self.addstr(8, self.startcol, "选择搜索类型:", curses.color_pair(1))
            for i in range(offset, min(len(datalist), offset + step)):
                if i == index:
                    self.addstr(
                        i - offset + 10,
                        self.indented_startcol,
                        "-> " + str(i) + "." + datalist[i - 1],
                        curses.color_pair(2),
                    )
                else:
                    self.addstr(
                        i - offset + 10, self.startcol, str(i) + "." + datalist[i - 1]
                    )
            self.screen.timeout(100)

        elif datatype == "help":
            for i in range(offset, min(len(datalist), offset + step)):
                if i == index:
                    self.addstr(
                        i - offset + 9,
                        self.indented_startcol,
                        "-> {}. '{}{}   {}".format(
                            i,
                            (datalist[i][0] + "'").ljust(11),
                            datalist[i][1].ljust(16),
                            datalist[i][2],
                        ),
                        curses.color_pair(2),
                    )
                else:
                    self.addstr(
                        i - offset + 9,
                        self.startcol,
                        "{}. '{}{}   {}".format(
                            i,
                            (datalist[i][0] + "'").ljust(11),
                            datalist[i][1].ljust(16),
                            datalist[i][2],
                        ),
                    )

            self.addstr(
                20,
                self.startcol,
                "NetEase-MusicBox 基于Python，所有版权音乐来源于网易，本地不做任何保存",
            )
            self.addstr(
                21,
                self.startcol,
                "按 [G] 到 Github 了解更多信息，帮助改进，或者Star表示支持~~",
            )
            self.addstr(22, self.startcol, "Build with love to music by omi")

        self.screen.refresh()

    def build_login_qr(self, url):
        """渲染扫码登录二维码，返回状态提示所在行号。"""
        import io

        import qrcode

        if self._small_window():
            self._show_small_window()
            return 0

        max_rows, max_columns = self.screen.getmaxyx()
        qr_start_row = 6

        qr = None
        lines = []
        status_row = qr_start_row
        for border in (4, 2, 1, 0):
            candidate = qrcode.QRCode(border=border)
            candidate.add_data(url)
            candidate.make(fit=True)
            buf = io.StringIO()
            candidate.print_ascii(out=buf, invert=True)
            candidate_lines = buf.getvalue().splitlines()
            qr_height = len(candidate_lines)
            candidate_status_row = qr_start_row + qr_height + 1
            if candidate_status_row < max_rows and all(
                self.startcol + truelen(line) < max_columns for line in candidate_lines
            ):
                qr = candidate
                lines = candidate_lines
                status_row = candidate_status_row
                break
        if qr is None:
            self._clear_rows(3, max_rows)
            self.addstr(4, 0, "窗口太小，无法完整显示二维码，请放大窗口")
            self.screen.refresh()
            return min(6, max_rows - 1)

        _safe_curs_set(0)
        curses.noecho()
        self.screen.move(4, 1)
        self.screen.clrtobot()
        self.addstr(
            4,
            self.startcol,
            "请使用网易云音乐 App 扫描二维码登录（按 [Q] 取消）",
            curses.color_pair(1),
        )
        row = qr_start_row
        for line in lines:
            if row >= max_rows:
                break
            self.addstr(row, self.startcol, line)
            row += 1
        status_row = min(status_row, max_rows - 1)
        self.addstr(status_row, self.startcol, "等待扫码...", curses.color_pair(2))
        self.screen.refresh()
        return status_row

    def update_login_qr_status(self, row, text):
        max_rows, _ = self.screen.getmaxyx()
        row = min(max(row, 0), max_rows - 1)
        with contextlib.suppress(curses.error):
            self.screen.move(row, 1)
            self.screen.clrtoeol()
        self.addstr(row, self.startcol, text, curses.color_pair(2))
        self.screen.refresh()

    def build_login_error(self):
        if self._small_window():
            self._show_small_window()
            return 27
        _safe_curs_set(0)
        self.screen.move(4, 1)
        self.screen.timeout(-1)  # disable the screen timeout
        self.screen.clrtobot()
        self.addstr(
            8, self.startcol, "艾玛，登录信息好像不对呢 (O_O)#", curses.color_pair(1)
        )
        self.addstr(10, self.startcol, "[1] 再试一次")
        self.addstr(11, self.startcol, "[2] 稍后再试")
        self.addstr(14, self.startcol, "请键入对应数字:", curses.color_pair(2))
        self.screen.refresh()
        x = self._read_key()
        self.screen.timeout(100)  # restore the screen timeout
        return x

    def build_search_error(self):
        if self._small_window():
            self._show_small_window()
            return 27
        _safe_curs_set(0)
        self.screen.move(4, 1)
        self.screen.timeout(-1)
        self.screen.clrtobot()
        self.addstr(8, self.startcol, "是不支持的搜索类型呢...", curses.color_pair(3))
        self.addstr(
            9,
            self.startcol,
            "（在做了，在做了，按任意键关掉这个提示）",
            curses.color_pair(3),
        )
        self.screen.refresh()
        x = self._read_key()
        self.screen.timeout(100)
        return x

    def _apply_input_backspace(self, y, x, chars):
        if not chars:
            return
        chars.pop()
        line = "".join(chars)
        self.screen.move(y, x)
        self.screen.clrtoeol()
        if line:
            self.addstr(y, x, line)
        self.screen.move(y, x + truelen(line))

    def _read_line_input(self, y, x, max_len=60):
        """Read a line with UTF-8 aware backspace (get_wch), fallback to getstr."""
        rows, columns = self.screen.getmaxyx()
        if not 0 <= y < rows or not 0 <= x < columns - 1:
            self._show_small_window()
            return ""
        if not hasattr(self.screen, "get_wch"):
            raw = self.screen.getstr(y, x, max_len)
            return decode_terminal_input(raw)

        _safe_curs_set(1)
        chars = []
        self.screen.move(y, x)
        self.screen.keypad(True)
        self.screen.timeout(100)

        while True:
            if getattr(self, "should_exit", lambda: False)():
                return ""
            try:
                ch = self.screen.get_wch()
            except curses.error:
                ch = self.screen.getch()

            rows, columns = self.screen.getmaxyx()
            if not 0 <= y < rows or x + truelen("".join(chars)) >= columns:
                return ""

            if _is_enter_key(ch):
                break
            if _is_backspace_key(ch):
                self._apply_input_backspace(y, x, chars)
                continue
            if _is_escape_key(ch):
                chars.clear()
                self.screen.move(y, x)
                self.screen.clrtoeol()
                break
            if isinstance(ch, str):
                if len(ch) != 1 or ord(ch) < 32:
                    continue
                _, columns = self.screen.getmaxyx()
                if (
                    len(chars) >= max_len
                    or truelen("".join(chars) + ch) > columns - x - 1
                ):
                    continue
                chars.append(ch)
                self.screen.move(y, x)
                self.screen.clrtoeol()
                self.addstr(y, x, "".join(chars))
                self.screen.move(y, x + truelen("".join(chars)))

        return "".join(chars)

    def build_timing(self):
        if self._small_window():
            self._show_small_window()
            return ""
        _safe_curs_set(0)
        self.screen.move(6, 1)
        self.screen.clrtobot()
        self.screen.timeout(-1)
        self.addstr(8, self.startcol, "输入定时时间(min):", curses.color_pair(1))
        self.addstr(
            11,
            self.startcol,
            "ps:定时时间为整数，输入0代表取消定时退出",
            curses.color_pair(1),
        )
        self.screen.timeout(-1)  # disable the screen timeout
        curses.noecho()
        timing_time = self._read_line_input(8, self.startcol + 19, 60)
        self.screen.timeout(100)  # restore the screen timeout
        return timing_time

    def get_param(self, prompt_string):
        if self._small_window():
            self._show_small_window()
            return ""
        # keep playing info in line 1
        curses.noecho()
        self.screen.move(4, 1)
        self.screen.clrtobot()
        self.addstr(5, self.startcol, prompt_string, curses.color_pair(1))
        self.screen.refresh()
        try:
            return self._read_line_input(10, self.startcol, 60).strip()
        finally:
            _safe_curs_set(0)

    def update_size(self):
        _safe_curs_set(0)
        # get terminal size
        y, x = self.screen.getmaxyx()
        if (x, y) == (self.x, self.y):  # no need to resize
            return
        self.x, self.y = x, y

        # update intendations
        self.update_margin()
        self.update_space()
        self.screen.clear()
        self.screen.refresh()

    def update_space(self):
        if self.x > 140:
            self.space = "   -   "
        elif self.x > 80:
            self.space = "  -  "
        else:
            self.space = " - "
        self.screen.refresh()
