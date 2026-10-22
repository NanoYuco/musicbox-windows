"""Consumer-visible regressions for transient loading and raw cached lyrics."""

import copy
from types import SimpleNamespace

import pytest

from NEMbox.menu import Menu
from NEMbox.ui import Ui
from tests.test_ui_rendering import view as view

LOADING = "享受高品质音乐，loading..."
TITLES = [
    "网易云音乐 > Holinary 的歌单",
    "短",
    "网易云音乐 > Mixed中文ABC的更长歌单标题",
]
COLON_LYRICS = [
    "[00:11:61]初句",
    "[00:16:17]次句",
    "[00:21:46]第三句",
    "[02:32:52]倒数第二句",
    "[02:37:08]末句",
    "",
]
COLON_TRANSLATION = ["[by:fixture]", *[s.replace("句", "句译") for s in COLON_LYRICS]]
NORMAL_LYRICS = [
    "[00:00.120]若夏日终将完结",
    "[00:02.100]化开冬日残存的忧郁",
    "[00:04.770]我迎着秋风几缕",
    "[00:07.000]后句",
]


def playlist_menu(view, title, fetch):
    """Keep real Menu dispatch/back and Ui rendering; fix only remote data."""
    menu = Menu.__new__(Menu)
    menu.ui = view
    menu.datatype, menu.title = "playlists", title
    menu.datalist = [
        {"playlist_id": 900001, "playlist_name": "音乐", "creator_name": "fixture"},
        {"playlist_id": 900002, "playlist_name": "旧列表", "creator_name": "fixture"},
    ]
    menu.index = menu.offset = 0
    menu.step, menu.menu_starts = 10, 0
    menu.stack = [[menu.datatype, menu.title, menu.datalist, 0, 0]]

    def dig_info(songs, datatype):
        assert datatype == "songs"
        return songs

    menu.api = SimpleNamespace(playlist_songlist=fetch, dig_info=dig_info)
    return menu


def draw_menu(menu):
    menu.ui.build_menu(
        menu.datatype,
        menu.title,
        menu.datalist,
        menu.offset,
        menu.index,
        menu.step,
        menu.menu_starts,
    )


@pytest.mark.parametrize("title", TITLES)
def test_loading_is_clean_inside_playlist_fetch_and_navigation_recovers(view, title):
    # Omitting the row clear leaves the old title visible during the request.
    before = []

    def fetch(playlist_id):
        assert playlist_id == 900001
        assert view.screen.line(7).strip() == LOADING
        assert [row for i, row in enumerate(view.screen.cells) if i != 7] == before
        return [
            {"song_id": 1, "song_name": "新歌曲", "artist": "甲", "album_name": "乙"}
        ]

    menu = playlist_menu(view, title, fetch)
    view.addstr(1, 0, "播放状态保留区")
    draw_menu(menu)
    opened = copy.deepcopy(view.screen.cells)
    before[:] = [row for i, row in enumerate(opened) if i != 7]
    menu.enter_page_event()
    draw_menu(menu)
    assert view.screen.line(7).strip() == title + " > 音乐"
    assert "新歌曲" in view.screen.line(9)
    menu.back_page_event()
    draw_menu(menu)
    assert view.screen.cells == opened


def song(lyrics=None, translation=None, song_id=1):
    return {
        "song_id": song_id,
        "song_name": "样本",
        "artist": "fixture",
        "lyric": COLON_LYRICS.copy() if lyrics is None else lyrics,
        "tlyric": COLON_TRANSLATION.copy() if translation is None else translation,
    }


def assert_pair(view, current, following):
    assert view.now_lyric == current
    assert view.post_lyric == following
    assert sorted(view.screen.line(row).strip() for row in (4, 5)) == sorted(
        [current, following]
    )


def test_raw_colon_cache_at_fourteen_seconds_displays_early_bilingual_lines(view):
    # A dot-only parser jumps to 02:37 and exposes both raw timestamp tags.
    cached = song()
    unchanged = copy.deepcopy(cached)
    view.build_process_bar(cached, 14, 180, True, 0)
    assert_pair(view, "初句译 || 初句", "次句译 || 次句")
    assert cached == unchanged


@pytest.mark.parametrize(
    "separator,fraction", [(":", "61"), (".", "61"), (".", "610"), (":", "610")]
)
@pytest.mark.parametrize(
    "position,current,following",
    [
        (11.10, "前句", "初句"),
        (11.12, "初句", "次句"),
        (14, "初句", "次句"),
        (157, "末句", ""),
    ],
)
def test_time_separators_and_precision_keep_hand_checked_boundaries(
    view, separator, fraction, position, current, following
):
    # Both delimiters mean fractional seconds, with the existing 0.5s lead.
    lyrics = [
        "[00:00.00]前句",
        f"[00:11{separator}{fraction}]初句",
        "[00:16.170]次句",
        f"[02:37{separator}08]末句",
    ]
    view.build_process_bar(song(lyrics, []), position, 180, True, 0)
    assert_pair(view, current, following)


def test_metadata_and_blank_lines_do_not_advance_timed_lyrics(view):
    # Non-time metadata and empty lines must not pretend to be due now.
    lyrics = ["[ar:fixture]", "", *COLON_LYRICS]
    translated = ["[by:fixture]", "", *COLON_TRANSLATION]
    view.build_process_bar(song(lyrics, translated), 14, 180, True, 0)
    assert_pair(view, "初句译 || 初句", "次句译 || 次句")


@pytest.mark.parametrize(
    "malformed", ["[00:16.]坏行", "[00:99.123]坏行", "[00:xx:10]坏行"]
)
def test_malformed_time_cannot_skip_valid_next_line_or_crash(view, malformed):
    lyrics = ["[00:11.61]初句", malformed, "[00:16.17]次句", "[02:37.08]末句", ""]
    view.build_process_bar(song(lyrics, []), 14, 180, True, 0)
    assert_pair(view, "初句", "次句")


def test_plain_untimed_lyrics_remain_readable(view):
    view.build_process_bar(
        song(["[by:fixture]", "无时间的首句", "无时间的次句", ""], []), 60, 180, True, 0
    )
    assert_pair(view, "无时间的首句", "无时间的次句")


def test_direct_ui_accepts_raw_cache_without_network_preprocessing():
    view = Ui.__new__(Ui)
    view.now_lyric_index = view.now_tlyric_index = 0
    view.now_lyric = view.post_lyric = ""
    view.update_lyrics(14, COLON_LYRICS, COLON_TRANSLATION)
    assert (view.now_lyric, view.post_lyric) == ("初句译 || 初句", "次句译 || 次句")


def test_last_line_pause_resume_rewind_switch_and_stop_clear_old_text(view):
    cached = song()
    view.build_process_bar(cached, 14, 180, True, 0)
    for playing in (False, False, True):
        view.build_process_bar(cached, 14, 180, playing, 0)
        assert_pair(view, "初句译 || 初句", "次句译 || 次句")
    for position in (157, 170):
        view.build_process_bar(cached, position, 180, True, 0)
        assert_pair(view, "末句译 || 末句", "")
    view.build_process_bar(cached, 14, 180, True, 0)
    assert_pair(view, "初句译 || 初句", "次句译 || 次句")
    view.build_process_bar(song(NORMAL_LYRICS, [], 2), 2, 174, True, 0)
    assert_pair(view, "化开冬日残存的忧郁", "我迎着秋风几缕")
    assert view.screen.line(4).strip() == "我迎着秋风几缕"
    assert view.screen.line(5).strip() == "化开冬日残存的忧郁"
    view.build_process_bar({}, 0, 0, False, 0)
    assert not any(view.screen.line(row) for row in range(3, 7))
