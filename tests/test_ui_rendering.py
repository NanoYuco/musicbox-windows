"""Visible-cell regressions; the screen boundary rejects wrapping/invalid writes."""

import curses
import unicodedata
from types import SimpleNamespace

import pytest

from NEMbox import ui as module
from NEMbox.scrollstring import scrollstring, truelen, truelen_cut
from NEMbox.ui import Ui, break_substr


class CellScreen:
    def __init__(self, rows=32, columns=120, keys=()):
        self.rows, self.columns = rows, columns
        self.cells = [[" "] * columns for _ in range(rows)]
        self.cursor = (0, 0)
        self.invalid_writes = []
        self.byte_writes = []
        self.keys = iter(keys)

    def getmaxyx(self):
        return self.rows, self.columns

    def getyx(self):
        return self.cursor

    def move(self, y, x):
        assert 0 <= y < self.rows and 0 <= x < self.columns, (y, x)
        self.cursor = y, x

    def clrtoeol(self):
        y, x = self.cursor
        self.cells[y][x:] = [" "] * (self.columns - x)

    def clrtobot(self):
        y, _ = self.cursor
        self.clrtoeol()
        for row in range(y + 1, self.rows):
            self.cells[row] = [" "] * self.columns

    def erase(self):
        self.cursor = 0, 0
        self.clrtobot()

    clear = erase

    def addstr(self, *args):
        if isinstance(args[0], int):
            y, x, text = args[:3]
        else:
            y, x = self.cursor
            text = args[0]
        if isinstance(text, bytes):
            self.byte_writes.append(text)
            text = text.decode("utf-8")
        for ch in text:
            if ch == "\n":
                self.invalid_writes.append((y, x, "implicit newline"))
                y, x = y + 1, 0
                continue
            width = (
                0
                if unicodedata.combining(ch)
                else (2 if unicodedata.east_asian_width(ch) in "WF" else 1)
            )
            if not (0 <= y < self.rows and x >= 0 and x + width < self.columns):
                self.invalid_writes.append((y, x, ch))
                raise curses.error("would wrap or leave the screen")
            if width:
                self.cells[y][x] = ch
                if width == 2:
                    self.cells[y][x + 1] = ""
            elif x:
                self.cells[y][x - 1] += ch
            x += width
        self.cursor = y, x

    def get_wch(self):
        return next(self.keys)

    def getch(self):
        return -1

    def refresh(self):
        pass

    def keypad(self, _value):
        pass

    def timeout(self, _value):
        pass

    def nodelay(self, _value):
        pass

    def line(self, row):
        return "".join(self.cells[row]).rstrip()

    def visible(self):
        return "\n".join(self.line(row) for row in range(self.rows))


@pytest.fixture
def view(monkeypatch):
    monkeypatch.setattr(module.curses, "noecho", lambda: None)
    monkeypatch.setattr(module.curses, "curs_set", lambda _: None)
    monkeypatch.setattr(module.curses, "color_pair", lambda _: 0)
    obj = Ui.__new__(Ui)
    obj.screen = CellScreen()
    obj.config = SimpleNamespace(
        get=lambda key: {
            "left_margin_ratio": 5,
            "right_margin_ratio": 5,
            "osdlyrics": False,
        }.get(key)
    )
    obj.x, obj.y = 120, 32
    obj.update_margin()
    obj.update_space()
    obj.now_lyric = obj.post_lyric = ""
    obj.now_lyric_index = obj.now_tlyric_index = 0
    return obj


def test_login_page_is_gone_after_return_to_empty_menu(view):
    # Missing clearing of rows 4/6 must leave the real QR/prompt visible here.
    view.build_login_qr("https://example.invalid/test")
    view.build_process_bar({}, 0, 0, False, 0)
    view.build_menu("playlists", "我的歌单", [], 0, 0, 10, 0)
    assert not any(view.screen.line(row) for row in range(3, 7))
    assert "等待扫码" not in view.screen.visible()
    assert "这里什么都没有" in view.screen.visible()


@pytest.mark.parametrize(
    "kind,item",
    [
        ("main", {"entry_name": "LONG" * 50}),
        ("songs", {"song_name": "LONG" * 50, "artist": "中文", "album_name": "专辑"}),
        (
            "djprograms",
            {"song_name": "LONG" * 50, "artist": "中文", "album_name": "专辑"},
        ),
        ("fmsongs", {"song_name": "LONG" * 50, "artist": "中文", "album_name": "专辑"}),
        ("artists", {"artists_name": "LONG" * 50, "alias": "别名"}),
        ("artist_info", {"item": "LONG" * 50}),
        ("albums", {"albums_name": "LONG" * 50, "artists_name": "歌手"}),
        ("recommend_lists", {"title": "LONG" * 50}),
        ("playlists", {"playlist_name": "LONG" * 50, "creator_name": "创建者"}),
        ("top_playlists", {"playlist_name": "LONG" * 50, "creator_name": "创建者"}),
        ("toplists", "LONG" * 50),
        ("playlist_classes", "LONG" * 50),
        ("playlist_class_detail", "LONG" * 50),
        ("djRadios", {"name": "LONG" * 50}),
        ("comments", {"comment_content": "读者: " + "LONG中文" * 50}),
        ("help", ["KEY", "LONG" * 50, "LONG" * 50]),
    ],
)
def test_all_lists_clip_without_wrap_and_erase_old_rows(view, kind, item):
    # Removing the common drawing boundary lets a real list line wrap again.
    view.build_menu(kind, "标题中文" * 60, [item, item], 0, 0, 5, 0)
    assert not view.screen.invalid_writes
    view.build_menu("main", "短", [{"entry_name": "短内容"}], 0, 0, 5, 0)
    assert "LONG" not in view.screen.visible()
    assert "-> 0. 短内容" in view.screen.line(9)
    assert not view.screen.line(10)


def test_coordinates_use_unicode_and_never_write_last_cell(view):
    view.addstr(0, 117, "中A尾")
    assert view.screen.line(0).endswith("中")
    assert not view.screen.invalid_writes
    assert not view.screen.byte_writes


@pytest.mark.parametrize(
    "text,width,cut",
    [("é·♪中文", 7, "é·♪中"), ("e\u0301中", 3, "e\u0301中"), ("中文", 4, "中")],
)
def test_width_and_clipping_keep_combining_text(text, width, cut):
    # Treating every non-ASCII codepoint as two columns corrupts these boundaries.
    assert truelen(text) == width
    limit = 5 if text == "é·♪中文" else (3 if text == "e\u0301中" else 3)
    assert truelen_cut(text, limit) == cut


def test_empty_scrollstring_has_empty_display():
    assert str(scrollstring("", 0)) == ""


def test_paused_progress_is_visible_and_stopped_region_is_clean(view):
    song = {"song_id": 1, "song_name": "歌曲", "artist": "歌手", "lyric": []}
    view.build_process_bar(song, 12, 30, True, 0)
    view.build_process_bar(song, 12, 30, False, 0)
    assert "00:12" in view.screen.line(3)
    view.build_process_bar({}, 0, 0, False, 0)
    assert not any(view.screen.line(row) for row in range(3, 7))
    assert view.now_lyric == view.post_lyric == ""


def test_resize_uses_screen_size_and_recovers_from_tiny_window(view, monkeypatch):
    # Reading the shell's fallback size instead of the curses screen is incorrect.
    monkeypatch.setattr(module, "get_terminal_size", lambda: (120, 32), raising=False)
    monkeypatch.setattr(module.curses, "resizeterm", lambda *_: None)
    view.screen = CellScreen(8, 24)
    view.update_size()
    assert (view.x, view.y) == (24, 8)
    view.build_menu("main", "菜单", [{"entry_name": "项目"}], 0, 0, 10, 0)
    assert "窗口太小" in view.screen.visible()
    assert not view.screen.invalid_writes
    view.screen = CellScreen()
    view.update_size()
    view.build_menu("main", "菜单", [{"entry_name": "项目"}], 0, 0, 10, 0)
    assert "窗口太小" not in view.screen.visible()
    assert "-> 0. 项目" in view.screen.visible()


def test_long_input_backspace_and_escape_do_not_wrap(view):
    view.screen.keys = iter([*"中文输入" * 12, "\x7f", "\x1b"])
    assert view._read_line_input(10, 100, 60) == ""
    assert not view.screen.invalid_writes
    assert view.screen.line(10) == ""


def test_comment_wrapping_never_drops_boundary_character():
    assert break_substr("中文测试abc", 0, 4) == "中文\n测试\nabc"


def test_zero_margins_use_available_screen_width(view):
    view.config = SimpleNamespace(get=lambda _: 0)
    view.update_margin()
    view.build_menu(
        "songs",
        "标题",
        [{"song_name": "可见歌名", "artist": "歌手", "album_name": "专辑"}],
        0,
        0,
        10,
        0,
    )
    assert "可见歌名" in view.screen.visible()


def test_unknown_duration_and_song_change_do_not_show_stale_lyrics(view):
    first = {
        "song_id": 1,
        "song_name": "一",
        "artist": "甲",
        "lyric": ["[00:00.00]旧歌词", "[00:01.00]旧后句", "[00:20.00]末句"],
    }
    view.build_process_bar(first, 10, 30, True, 0)
    second = {
        "song_id": 2,
        "song_name": "二",
        "artist": "乙",
        "lyric": ["[00:00.00]新歌词", "[00:20.00]新后句"],
    }
    view.build_process_bar(second, 1, 0, True, 0)
    assert "--:--" in view.screen.line(3)
    assert "新歌词" in view.screen.visible()
    assert "旧" not in view.screen.visible()


def test_single_cell_storage_preserves_following_ascii_coordinates(view):
    class PdcScreen(CellScreen):
        def addstr(self, *args):
            y, x, text, *_attributes = args
            for char in text:
                self.cells[y][x] = char
                x += len(char.encode("utf-16-le")) // 2
            self.cursor = y, x

    view.screen = PdcScreen()
    view._single_cell_curses = True
    view.addstr(0, 0, "中文e\u0301ab")
    assert view.screen.getyx() == (0, 7)
    view.addstr("x")
    assert view.screen.cells[0][4:8] == ["é", "a", "b", "x"]
    # Actual CJK spacing belongs to test_windows_console_output, not this
    # storage-only double: continuation cells must not prescribe real spaces.


@pytest.mark.parametrize(
    "text,width,cut",
    [
        ("❤️x", 3, "❤️"),
        ("🎵x", 3, "🎵"),
        ("👩\u200d🎤x", 3, "👩\u200d🎤"),
        ("🇨🇳x", 3, "🇨🇳"),
    ],
)
def test_emoji_units_are_not_split_or_overcounted(text, width, cut):
    assert truelen(text) == width
    assert truelen_cut(text, 2) == cut


def test_pending_exit_interrupts_line_input_without_another_key(view):
    reads = []

    def idle():
        reads.append(True)
        assert len(reads) <= 2, "input blocked after exit was requested"
        return -1

    view.screen.get_wch = idle
    view.should_exit = lambda: len(reads) >= 2
    assert view._read_line_input(10, 20) == ""


@pytest.mark.parametrize(
    "page",
    [
        "build_timing",
        "get_param",
        "build_login_qr",
        "build_login_error",
        "build_search_error",
    ],
)
def test_temporary_pages_handle_window_too_small(view, page):
    view.screen = CellScreen(4, 12, ["\x1b"])
    args = ("test",) if page in {"get_param", "build_login_qr"} else ()
    getattr(view, page)(*args)
    assert "窗口太小" in view.screen.visible()
    assert not view.screen.invalid_writes


def test_qr_never_displays_only_a_cut_off_fragment(view):
    view.screen = CellScreen(14, 40)
    view.x, view.y = 40, 14
    view.update_margin()
    view.build_login_qr("https://example.invalid/test?" + "x" * 80)
    assert "无法完整显示二维码" in view.screen.visible()
    assert "▄" not in view.screen.visible()
    assert "█" not in view.screen.visible()


@pytest.mark.parametrize("page", ["build_login_error", "build_search_error"])
def test_exit_request_closes_error_page_without_waiting_for_key(view, page):
    view.should_exit = lambda: True

    def wait_forbidden():
        raise AssertionError("error page waited for a key after exit request")

    view.screen.getch = wait_forbidden
    assert getattr(view, page)() == 27
