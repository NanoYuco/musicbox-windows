from types import SimpleNamespace

import pytest

from NEMbox import menu as module
from NEMbox.menu import Menu


class DeadlineTimer:
    active = []

    def __init__(self, interval, callback, args=()):
        self.callback, self.args = callback, args
        self.cancelled = False

    def start(self):
        self.active.append(self)

    def cancel(self):
        self.cancelled = True


@pytest.fixture
def menu(monkeypatch):
    monkeypatch.setattr(module, "notify", lambda *_: None)
    monkeypatch.setattr(module, "Timer", DeadlineTimer, raising=False)
    DeadlineTimer.active = []
    obj = Menu.__new__(Menu)
    obj.countdown_start, obj.countdown = 0, -1
    obj.is_in_countdown, obj.quit = False, False
    obj.timer = None
    obj.build_menu_processbar = lambda: None
    return obj


@pytest.mark.parametrize("text", ["", "abc", "-1", "１２", "²"])
def test_invalid_timer_input_does_not_crash_or_replace_existing_deadline(menu, text):
    menu.ui = SimpleNamespace(build_timing=lambda: "2")
    menu.time_key_event()
    original = menu.countdown_start, menu.countdown
    menu.ui.build_timing = lambda: text
    menu.time_key_event()
    assert (menu.countdown_start, menu.countdown) == original
    assert menu.is_in_countdown


def test_replacing_and_cancelling_timer_leaves_no_old_exit(menu):
    menu.ui = SimpleNamespace(build_timing=lambda: "1")
    menu.time_key_event()
    menu.ui.build_timing = lambda: "2"
    menu.time_key_event()
    assert len([t for t in DeadlineTimer.active if not t.cancelled]) <= 1
    menu.ui.build_timing = lambda: "0"
    menu.time_key_event()
    assert not any(not t.cancelled for t in DeadlineTimer.active)
    assert not menu.is_in_countdown


def test_deadline_never_calls_curses_stop_from_worker(menu):
    # Existing Timer(self.stop) ends curses outside the event loop.
    stopped = []
    menu.stop = lambda: stopped.append(True)
    menu.ui = SimpleNamespace(build_timing=lambda: "1")
    menu.time_key_event()
    for timer in DeadlineTimer.active:
        timer.callback(*timer.args)
    assert stopped == []


def test_replacement_ignores_old_deadline_and_expires_once(menu, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    menu.ui = SimpleNamespace(build_timing=lambda: "1")
    menu.time_key_event()
    now[0] = 10
    menu.ui.build_timing = lambda: "2"
    menu.time_key_event()
    for tick in [60, 61, 129.9]:
        now[0] = tick
        assert not menu._exit_requested()
    now[0] = 130
    assert menu._exit_requested()
    assert not menu.is_in_countdown


def test_cancellation_survives_original_deadline(menu, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    menu.ui = SimpleNamespace(build_timing=lambda: "1")
    menu.time_key_event()
    now[0] = 10
    menu.ui.build_timing = lambda: "0"
    menu.time_key_event()
    now[0] = 65
    assert not menu._exit_requested()
