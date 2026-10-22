"""Capture existing physical output; observation never calls refresh."""

import curses
import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from NEMbox.ui import Ui
from tests.test_ui_loading_lyrics import (
    NORMAL_LYRICS,
    TITLES,
    draw_menu,
    playlist_menu,
    song,
)
from tests.windows_console_worker import Console, source_hashes
from tests.windows_startup_worker import prepare_console


def collect(result):
    console = Console()
    view = None
    try:
        prepare_console(console)
        view = Ui()
        console.close()
        console = Console()
        result["size"] = [view.y, view.x]
        result["adapter"] = type(view.screen).__name__
        rows, columns = view.y, view.x

        def snapshot():
            return [console.row(row, columns) for row in range(rows)]

        result["loading"] = []
        for title in TITLES:
            stages = {"title": title}

            def fetch(playlist_id, stages=stages):
                assert playlist_id == 900001
                stages["during"] = snapshot()
                return [
                    {
                        "song_id": 1,
                        "song_name": "新歌曲",
                        "artist": "甲",
                        "album_name": "乙",
                    }
                ]

            menu = playlist_menu(view, title, fetch)
            view.build_playinfo("保留曲", "歌手", "专辑", "MP3", 0, pause=True)
            draw_menu(menu)
            stages["before"] = snapshot()
            menu.enter_page_event()
            draw_menu(menu)
            stages["ready"] = snapshot()
            menu.back_page_event()
            draw_menu(menu)
            stages["back"] = snapshot()
            result["loading"].append(stages)

        result["lyrics"] = []
        cached = song()
        for label, current_song, position, total, playing in [
            ("colon", cached, 14, 180, True),
            ("paused", cached, 14, 180, False),
            ("paused-again", cached, 14, 180, False),
            ("resumed", cached, 14, 180, True),
            ("last", cached, 157, 180, True),
            ("last-again", cached, 170, 180, True),
            ("rewound", cached, 14, 180, True),
            ("normal-dot", song(NORMAL_LYRICS, [], 2), 2, 174, True),
        ]:
            view.build_process_bar(current_song, position, total, playing, 0)
            result["lyrics"].append(
                {
                    "label": label,
                    "current": view.now_lyric,
                    "next": view.post_lyric,
                    "position": position,
                    "rows": snapshot(),
                }
            )
        view.build_process_bar({}, 0, 0, False, 0)
        # build_process_bar intentionally leaves refresh to its menu caller on stop.
        draw_menu(menu)
        result["stopped"] = snapshot()
    finally:
        if view is not None:
            curses.endwin()
        console.close()


def main():
    result = {
        "hashes_before": source_hashes(),
        "environment": {
            k: os.environ.get(k) for k in ("WT_SESSION", "TERM", "ConEmuANSI")
        },
        "kind": "owned Windows console cells; no desktop screenshot",
    }
    try:
        collect(result)
    except BaseException:
        result["error"] = traceback.format_exc()
    finally:
        result["hashes_after"] = source_hashes()
        Path(sys.argv[1]).write_text(
            json.dumps(result, ensure_ascii=True, indent=2), encoding="utf-8"
        )
    return 1 if "error" in result else 0


if __name__ == "__main__":
    sys.exit(main())
