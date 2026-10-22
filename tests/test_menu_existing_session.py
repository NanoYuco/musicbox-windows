from types import SimpleNamespace

import pytest

from NEMbox.menu import Menu
from NEMbox.storage import Storage


@pytest.mark.parametrize("channel", [4, 5, 7, 8])
def test_valid_cookie_session_does_not_request_qr_when_local_summary_is_empty(channel):
    # The real user's cookie works, but their persisted user summary is empty.
    storage = Storage()
    original = storage.database
    storage.database = {
        **original,
        "user": {"username": "", "password": "", "user_id": "", "nickname": ""},
    }
    menu = Menu.__new__(Menu)
    menu.storage = storage
    menu.title = "网易云音乐"
    menu.api = SimpleNamespace(
        get_account_info=lambda: {
            "code": 200,
            "account": {"id": 42},
            "profile": {"nickname": "既有登录"},
        },
        user_playlist=lambda uid: [{"playlist_id": 1, "owner": uid}],
        user_cloud=lambda: [],
        recommend_playlist=lambda: [],
        recommend_resource=lambda: [],
        dig_info=lambda data, _kind: data,
    )

    def qr_forbidden():
        raise AssertionError("valid existing cookie session was sent back to QR login")

    menu.login = qr_forbidden
    try:
        menu.choice_channel(channel)
        assert menu.userid == 42
        assert "既有登录" in menu.username
        if channel == 4:
            assert menu.datalist == [{"playlist_id": 1, "owner": 42}]
        else:
            assert menu.datalist == []
    finally:
        storage.database = original
