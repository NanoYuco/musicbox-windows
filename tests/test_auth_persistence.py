"""Real subprocess/file regressions; no user state or external network is used."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

WORKER = Path(__file__).with_name("auth_persistence_worker.py")


def isolated_env(root, *, default=False):
    env = os.environ.copy()
    for key, part in {
        "XDG_CONFIG_HOME": "config",
        "XDG_DATA_HOME": "data",
        "XDG_CACHE_HOME": "cache",
        "XDG_RUNTIME_DIR": "runtime",
        "TEMP": "temp",
        "TMP": "temp",
        "TMPDIR": "temp",
    }.items():
        directory = root / part
        directory.mkdir(parents=True, exist_ok=True)
        env[key] = str(directory)
    home = root / "home"
    home.mkdir(exist_ok=True)
    env.update(
        USERPROFILE=str(home),
        HOME=str(home),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONUTF8="1",
    )
    if default:
        for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
            env.pop(key, None)
    return env


def run_worker(root, env, mode):
    result = subprocess.run(
        [sys.executable, str(WORKER), mode],
        env=env,
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    (root / f"{mode}-result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def eapi(result):
    return next(
        event
        for event in result["trace"]
        if event["path"] == "/eapi/test/auth-persistence"
    )


@pytest.mark.parametrize("default", [False, True], ids=["xdg", "default"])
def test_qr_credentials_survive_three_processes_and_nmtid_resave(tmp_path, default):
    # A load-only fix fails here: B re-saves discarded sessions, C loses MUSIC_U.
    env = isolated_env(tmp_path, default=default)
    run_worker(tmp_path, env, "seed-state")
    paths = (
        tmp_path / "home/.netease-musicbox"
        if default
        else tmp_path / "config/netease-musicbox"
    )
    config_before = (paths / "config.json").read_bytes()
    a = run_worker(tmp_path, env, "A")
    b = run_worker(tmp_path, env, "B")
    c = run_worker(tmp_path, env, "probe")
    assert len({a["pid"], b["pid"], c["pid"]}) == 3
    assert a["login"] == {
        "status": "success",
        "code": 803,
        "user_id": 4242,
        "nickname": "fictional-user",
    }
    for process in (a, b, c):
        assert process["status"]["logged_in"] is True, process
        assert process["status"]["user_id"] == 4242
        assert process["tui_allowed"] is True
        assert process["login_attempts"] == []
        assert process["non_user_data_unchanged"] is True
    for process in (b, c):
        request = eapi(process)
        assert request["music_u"] == "fictional-music-u"
        assert request["csrf"] == "fictional-csrf"
        assert request["payload_header"]["MUSIC_U"] == "fictional-music-u"
        assert request["payload_header"]["deviceId"] == "12345678" + "A" * 44
        assert all(
            cookie["expires"] is None
            for cookie in process["cookies"]
            if cookie["name"] in ("MUSIC_U", "__csrf", "deviceId")
        )
    assert "NMTID" in eapi(c)["cookie_names"]
    assert "NMTID" not in eapi(b)["cookie_names"]
    assert "NMTID" in {cookie["name"] for cookie in b["cookies_after_save"]}
    assert (paths / "config.json").read_bytes() == config_before
    cache = paths / "cached" if default else tmp_path / "cache/netease-musicbox/cached"
    assert (cache / "keep-cache.bin").read_bytes() == b"keep cached audio"
    assert not (
        tmp_path / ("config/netease-musicbox" if default else "home/.netease-musicbox")
    ).exists()


@pytest.mark.parametrize(
    "kind, logged_in",
    [("legacy", True), ("durable", True), ("expired", False), ("device-only", False)],
)
def test_existing_cookie_files_restore_only_valid_credentials(
    tmp_path, kind, logged_in
):
    # Catches dropped legacy sessions, discarded attributes, and revived expiry.
    env = isolated_env(tmp_path)
    run_worker(tmp_path, env, "seed-state")
    run_worker(tmp_path, env, "seed-" + kind)
    loaded = run_worker(tmp_path, env, "probe")
    again = run_worker(tmp_path, env, "probe")
    for process in (loaded, again):
        assert process["status"]["logged_in"] is logged_in
        assert process["tui_allowed"] is logged_in
        assert process["non_user_data_unchanged"] is True
        assert eapi(process)["music_u"] == ("fictional-music-u" if logged_in else None)
        if logged_in:
            cookie = next(c for c in process["cookies"] if c["name"] == "MUSIC_U")
            assert (
                cookie["domain"],
                cookie["path"],
                cookie["secure"],
                cookie["expires"],
            ) == (
                ".music.163.com",
                "/",
                True,
                2114380800 if kind == "durable" else None,
            )


@pytest.mark.parametrize("save_mode", ["save-qr-key", "save-anonymous"])
def test_other_product_save_paths_preserve_reloaded_login(tmp_path, save_mode):
    # Catches an unconverted save/load call in QR-key or anonymous registration.
    env = isolated_env(tmp_path)
    run_worker(tmp_path, env, "A")
    run_worker(tmp_path, env, save_mode)
    assert run_worker(tmp_path, env, "probe")["status"]["logged_in"] is True


def test_logout_cannot_restore_old_login_in_next_process(tmp_path):
    # Catches persisted credentials surviving an explicit logout.
    env = isolated_env(tmp_path)
    run_worker(tmp_path, env, "seed-state")
    run_worker(tmp_path, env, "A")
    assert run_worker(tmp_path, env, "logout")["status"]["logged_in"] is False
    again = run_worker(tmp_path, env, "probe")
    assert again["status"]["logged_in"] is False
    assert eapi(again)["music_u"] is None
    assert again["non_user_data_unchanged"] is True


def test_qr_cookie_string_preserves_http_cookie_attributes(tmp_path):
    # Catches JSON cookie application overriding real HTTP expiration/security.
    env = isolated_env(tmp_path)
    a = run_worker(tmp_path, env, "qr-attributes")
    process = run_worker(tmp_path, env, "probe")
    for result in (a, process):
        credentials = [c for c in result["cookies"] if c["name"] == "MUSIC_U"]
        assert credentials == [
            {
                "name": "MUSIC_U",
                "domain": ".music.163.com",
                "path": "/",
                "secure": True,
                "expires": 2114380800,
                "discard": False,
            }
        ]


@pytest.mark.parametrize(
    "kind, expiry, logged_in",
    [("durable", 2114380800, True), ("expired", 946684800, False)],
)
def test_qr_cookie_string_honors_explicit_lifetime(tmp_path, kind, expiry, logged_in):
    # Catches explicit server expiry/domain/path being replaced by a session.
    env = isolated_env(tmp_path)
    first = run_worker(tmp_path, env, "qr-string-" + kind)
    cookie = next(c for c in first["cookies"] if c["name"] == "MUSIC_U")
    assert (cookie["domain"], cookie["path"], cookie["secure"], cookie["expires"]) == (
        ".music.163.com",
        "/weapi",
        True,
        expiry,
    )
    assert run_worker(tmp_path, env, "probe")["status"]["logged_in"] is logged_in


def test_eapi_does_not_send_cookies_that_expire_in_memory(tmp_path):
    # Requests filters expiration, but manually constructed EAPI cookies must too.
    env = isolated_env(tmp_path)
    run_worker(tmp_path, env, "seed-durable")
    request = eapi(run_worker(tmp_path, env, "expired-in-memory"))
    assert request["music_u"] is None
    assert request["csrf"] == ""
    assert "MUSIC_U" not in request["payload_header"]


def test_qr_max_age_zero_overrides_future_expires(tmp_path):
    # Catches reviving a cookie explicitly expired by Max-Age=0.
    env = isolated_env(tmp_path)
    first = run_worker(tmp_path, env, "qr-max-age-expired")
    assert first["login"]["error"]["type"] == "not_logged_in"
    assert run_worker(tmp_path, env, "probe")["status"]["logged_in"] is False


def test_cached_user_summary_does_not_authenticate_tui(tmp_path):
    # Catches _ensure_login returning True based only on database.username.
    env = isolated_env(tmp_path)
    run_worker(tmp_path, env, "seed-state")
    process = run_worker(tmp_path, env, "probe")
    assert process["status"]["logged_in"] is False
    assert process["tui_allowed"] is False
    assert process["login_attempts"] == ["qr-required"]


def test_server_rejection_is_not_overruled_by_cached_user(tmp_path):
    # Catches accepting cached summary after an explicit negative account result.
    env = isolated_env(tmp_path)
    run_worker(tmp_path, env, "A")
    process = run_worker(tmp_path, env, "server-rejected")
    assert process["status"]["logged_in"] is False
    assert process["tui_allowed"] is False


def test_tui_qr_success_requires_identified_account(tmp_path):
    # A successful QR poll alone must not bypass a failed account lookup.
    env = isolated_env(tmp_path)
    run_worker(tmp_path, env, "seed-state")
    process = run_worker(tmp_path, env, "qr-account-rejected")
    assert process["qr_accepted"] is False
    assert process["user_summary_preserved"] is True
    assert process["cli_qr"]["error"]["type"] == "not_logged_in"


def test_expired_device_cookie_does_not_clear_valid_account(tmp_path):
    # An expired device cookie must not invalidate an unrelated durable login.
    env = isolated_env(tmp_path)
    run_worker(tmp_path, env, "seed-durable")
    process = run_worker(tmp_path, env, "expire-unrelated")
    assert eapi(process)["music_u"] == "fictional-music-u"
