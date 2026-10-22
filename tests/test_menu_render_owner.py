import threading
from typing import cast

from NEMbox import menu as module
from NEMbox.menu import Menu
from tests.test_ui_rendering import CellScreen


def test_menu_input_and_player_draw_to_same_screen(monkeypatch):
    monkeypatch.setattr(module.C, "initscr", CellScreen)
    monkeypatch.setattr(module.C, "newpad", lambda *_: CellScreen(1, 8))
    for name in [
        "start_color",
        "use_default_colors",
        "init_pair",
        "noecho",
        "curs_set",
    ]:
        monkeypatch.setattr(module.C, name, lambda *_: None)
    monkeypatch.setattr(module.C, "color_pair", lambda _: 0)
    monkeypatch.setattr(module.signal, "signal", lambda *_: None)
    menu = Menu()
    menu.build_menu_processbar()
    assert "网易云音乐" in cast(CellScreen, menu.screen).visible()
    menu.player.ui.screen.rows = 30
    menu.player.update_size()
    assert menu.ui.y == 30


def test_worker_song_change_does_not_mutate_active_page():
    menu = Menu.__new__(Menu)
    menu.at_playing_list = True
    menu._pending_song_changed = threading.Event()
    menu.title = "搜索输入"
    menu.show_playing_song = lambda: setattr(menu, "title", "新歌列表")
    worker = threading.Thread(target=menu.song_changed_callback)
    worker.start()
    worker.join()
    assert menu.title == "搜索输入"


def test_resize_keeps_selected_item_visible_with_configured_page_length(monkeypatch):
    monkeypatch.setattr(module.C, "initscr", CellScreen)
    monkeypatch.setattr(module.C, "newpad", lambda *_: CellScreen(1, 8))
    for name in [
        "start_color",
        "use_default_colors",
        "init_pair",
        "noecho",
        "curs_set",
    ]:
        monkeypatch.setattr(module.C, name, lambda *_: None)
    monkeypatch.setattr(module.C, "color_pair", lambda _: 0)
    monkeypatch.setattr(module.signal, "signal", lambda *_: None)
    menu = Menu()
    menu.index = 9
    menu.screen = menu.ui.screen = CellScreen(14, 40)
    menu.build_menu_processbar()
    assert "-> 9." in cast(CellScreen, menu.screen).visible()
    assert not cast(CellScreen, menu.screen).invalid_writes
    menu.screen = menu.ui.screen = CellScreen()
    menu.build_menu_processbar()
    assert menu.index == 9
    assert "-> 9." in cast(CellScreen, menu.screen).visible()


def test_stopped_menu_does_not_show_old_progress_or_lyrics(monkeypatch):
    monkeypatch.setattr(module.C, "initscr", CellScreen)
    monkeypatch.setattr(module.C, "newpad", lambda *_: CellScreen(1, 8))
    for name in [
        "start_color",
        "use_default_colors",
        "init_pair",
        "noecho",
        "curs_set",
    ]:
        monkeypatch.setattr(module.C, name, lambda *_: None)
    monkeypatch.setattr(module.C, "color_pair", lambda _: 0)
    monkeypatch.setattr(module.signal, "signal", lambda *_: None)
    menu = Menu()
    original = menu.storage.database
    try:
        menu.storage.database = {
            **original,
            "songs": {},
            "player_info": {**original["player_info"]},
        }
        menu.player.new_player_list(
            "songs",
            "local",
            [
                {
                    "song_id": 999,
                    "song_name": "已停止测试",
                    "artist": "",
                    "album_name": "",
                    "quality": "MP3",
                    "lyric": ["[00:00.00]旧歌词", "[00:20.00]后句"],
                }
            ],
            0,
        )
        menu.player.info["idx"] = 0
        menu.player.playing_flag = True
        menu.player.playback_state = "playing"
        menu.player.process_location, menu.player.process_length = 10, 30
        menu.build_menu_processbar()
        menu.player.stop()
        menu.build_menu_processbar()
        assert "旧歌词" not in cast(CellScreen, menu.screen).visible()
        assert "已停止" in cast(CellScreen, menu.screen).visible()
        assert not cast(CellScreen, menu.screen).line(3)
    finally:
        menu.storage.database = original
