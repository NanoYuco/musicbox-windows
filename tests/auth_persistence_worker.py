"""Fresh-process auth regression worker. All credentials here are fictional.

Only HTTPAdapter.send is replaced. Requests still prepares real requests, applies
response cookies, and uses the product's CachedSession and MozillaCookieJar.
"""

import contextlib
import copy
import io
import json
import os
import sys
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import requests
from Cryptodome.Cipher import AES
from requests.adapters import HTTPAdapter

from NEMbox.api import NetEase
from NEMbox.cli import _build_parser, dispatch
from NEMbox.config import Config
from NEMbox.const import Constant
from NEMbox.storage import Storage

TOKEN = "fictional-music-u"
CSRF = "fictional-csrf"
DEVICE = "12345678" + "A" * 44
COOKIE_TEXT = f"MUSIC_U={TOKEN}; __csrf={CSRF}; deviceId={DEVICE}"
ACCOUNT = {
    "code": 200,
    "account": {"id": 4242},
    "profile": {"nickname": "fictional-user"},
}
ANONYMOUS = {"code": 200, "account": None, "profile": None}
FUTURE = 2114380800  # 2037-01-01 UTC; explicit server fixture, not product policy.
PAST = 946684800  # 2000-01-01 UTC.
mode = sys.argv[1]
trace = []


def transport(_adapter, request, **_kwargs):
    path = urlparse(request.url).path
    sent = request.headers.get("Cookie", "")
    pairs = dict(part.strip().split("=", 1) for part in sent.split(";") if "=" in part)
    event = {
        "path": path,
        "cookie_names": sorted(pairs),
        "music_u": pairs.get("MUSIC_U"),
        "csrf": pairs.get("__csrf"),
    }
    headers = Message()
    if path == "/weapi/login/qrcode/client/login":
        data = {"code": 803, "cookie": COOKIE_TEXT}
        if mode == "qr-attributes":
            headers.add_header(
                "Set-Cookie",
                f"MUSIC_U={TOKEN}; Domain=.music.163.com; Path=/; Secure; Expires=Thu, 01 Jan 2037 00:00:00 GMT",
            )
        elif mode in ("qr-string-durable", "qr-string-expired"):
            expiry = (
                "Thu, 01 Jan 2037 00:00:00 GMT"
                if mode == "qr-string-durable"
                else "Sat, 01 Jan 2000 00:00:00 GMT"
            )
            data["cookie"] = (
                f"MUSIC_U={TOKEN}; Domain=.music.163.com; Path=/weapi; Secure; Expires={expiry}; __csrf={CSRF}; deviceId={DEVICE}"
            )
        elif mode == "qr-max-age-expired":
            data["cookie"] = (
                f"MUSIC_U={TOKEN}; Max-Age=0; Expires=Thu, 01 Jan 2037 00:00:00 GMT; __csrf={CSRF}; deviceId={DEVICE}"
            )
    elif path == "/weapi/login/qrcode/unikey":
        data = {"code": 200, "data": {"unikey": "fictional-key"}}
    elif path == "/weapi/register/anonimous":
        data = {"code": 200}
        headers.add_header(
            "Set-Cookie", "MUSIC_A=fictional-anonymous; Domain=.music.163.com; Path=/"
        )
    elif path in ("/weapi/nuser/account/get", "/weapi/w/nuser/account/get"):
        data = (
            ACCOUNT
            if pairs.get("MUSIC_U") == TOKEN
            and mode not in ("server-rejected", "qr-account-rejected")
            else ANONYMOUS
        )
        event["authenticated"] = data is ACCOUNT
    elif path == "/eapi/test/auth-persistence":
        # Decode the actual encrypted request, independently of the product helper.
        body = parse_qs(
            request.body.decode() if isinstance(request.body, bytes) else request.body
        )
        raw = AES.new(b"e82ckenh8dichen8", AES.MODE_ECB).decrypt(
            bytes.fromhex(body["params"][0])
        )
        plain = raw[: -raw[-1]].decode()
        event["payload_header"] = json.loads(plain.split("-36cd479b6b5-")[1])["header"]
        data = {"code": 200}
        expiry = (
            "Sat, 01 Jan 2000 00:00:00 GMT"
            if mode == "expire-unrelated"
            else "Thu, 01 Jan 2037 00:00:00 GMT"
        )
        headers.add_header(
            "Set-Cookie",
            f"NMTID=fictional-nmtid; Domain=.music.163.com; Path=/; Expires={expiry}",
        )
    elif path == "/eapi/logout":
        data = {"code": 200}
    else:
        raise AssertionError(f"unexpected external request: {path}")
    trace.append(event)
    response = requests.Response()
    response.status_code = 200
    response.url = request.url
    response.request = request
    response._content = json.dumps(data).encode()
    response.headers["Content-Type"] = "application/json"
    response.raw = SimpleNamespace(_original_response=SimpleNamespace(msg=headers))
    return response


HTTPAdapter.send = transport


def cli(*arguments, expected_code=0):
    output = io.StringIO()
    error = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
        code = dispatch(api, _build_parser().parse_args(arguments))
    assert code == expected_code, output.getvalue() + error.getvalue()
    payload = json.loads(output.getvalue() or error.getvalue())
    return payload.get("data", payload)


def metadata(api):
    return [
        {
            "name": c.name,
            "domain": c.domain,
            "path": c.path,
            "secure": c.secure,
            "expires": c.expires,
            "discard": c.discard,
        }
        for c in api.cookie_jar
    ]


def seed_file(kind):
    # A hand-written existing Netscape file, not a hand-repaired product jar.
    expiry = {"legacy": "", "durable": str(FUTURE), "expired": str(PAST)}.get(kind, "")
    rows = [
        f".music.163.com\tTRUE\t/\tTRUE\t{expiry}\tMUSIC_U\t{TOKEN}",
        f".music.163.com\tTRUE\t/\tFALSE\t\t__csrf\t{CSRF}",
        f".music.163.com\tTRUE\t/\tFALSE\t\tdeviceId\t{DEVICE}",
    ]
    if kind == "device-only":
        rows = [f".music.163.com\tTRUE\t/\tFALSE\t{FUTURE}\tNMTID\tfictional-nmtid"]
    Path(Constant.cookie_path).write_text(
        "# Netscape HTTP Cookie File\n" + "\n".join(rows) + "\n", encoding="utf-8"
    )


storage = Storage()
storage.load()
if mode == "seed-state":
    storage.database["user"] = {
        "username": "cached-user",
        "password": "",
        "user_id": 99,
        "nickname": "cached-name",
    }
    storage.database["collections"] = [
        {"collection_name": "keep-collection", "collection_songs": [7]}
    ]
    storage.database["songs"] = {
        "7": {"song_id": 7, "song_name": "keep-song", "quality": "HIRES FLAC"}
    }
    storage.database["player_info"]["playing_volume"] = 37
    storage.save()
    config = Config()
    config.config["music_quality"]["value"] = "hires"
    config.config["player_backend"]["value"] = "mpv"
    config.config["mpv_parameters"]["value"] = ["--no-config", "--ao=null"]
    config.save_config_file()
    (Path(Constant.download_dir) / "keep-cache.bin").write_bytes(b"keep cached audio")

if mode.startswith("seed-") and mode != "seed-state":
    seed_file(mode.removeprefix("seed-"))

from NEMbox.menu import Menu

result = {
    "pid": os.getpid(),
    "mode": mode,
    "paths": {
        "cookie": Constant.cookie_path,
        "config": Constant.config_path,
        "storage": Constant.storage_path,
    },
}
before = copy.deepcopy(storage.database)
api = NetEase()
if mode in (
    "A",
    "qr-attributes",
    "qr-string-durable",
    "qr-string-expired",
    "qr-max-age-expired",
):
    result["login"] = cli(
        "auth",
        "login",
        "--check",
        "fictional-key",
        "--json",
        expected_code=3 if mode in ("qr-string-expired", "qr-max-age-expired") else 0,
    )
elif mode == "logout":
    result["logout"] = cli("auth", "logout", "--yes", "--json")

result["cookies"] = metadata(api)
result["status"] = cli("auth", "status", "--json")
menu = Menu.__new__(Menu)
menu.api = api
menu.storage = storage
login_attempts = []
menu.login = lambda: login_attempts.append("qr-required") or False
result["tui_allowed"] = menu._ensure_login()
result["login_attempts"] = login_attempts
result["tui_user_id"] = menu.userid
if mode == "qr-account-rejected":
    result["qr_accepted"] = menu._on_qr_login_success()
    result["user_summary_preserved"] = storage.database["user"] == before["user"]
    result["cli_qr"] = cli(
        "auth", "login", "--check", "fictional-key", "--json", expected_code=3
    )

if mode in ("B", "probe", "expired-in-memory", "expired-weapi", "expire-unrelated") or (
    mode.startswith("seed-") and mode != "seed-state"
):
    if mode in ("expired-in-memory", "expired-weapi"):
        for cookie in api.cookie_jar:
            if cookie.name in ("MUSIC_U", "__csrf"):
                cookie.expires = PAST
        if mode == "expired-weapi":
            api.get_account_info()
    if mode == "expire-unrelated":
        api.eapi_request("/api/test/auth-persistence")
        api.login_qr_key()
        api = NetEase()
        result["after_expiry_cookies"] = metadata(api)
    result["eapi"] = api.eapi_request("/api/test/auth-persistence")
    result["cookies_after_save"] = metadata(api)
elif mode == "save-qr-key":
    result["key"] = api.login_qr_key()
elif mode == "save-anonymous":
    result["anonymous"] = api.register_anonimous()

result["non_user_data_unchanged"] = {
    k: v for k, v in storage.database.items() if k != "user"
} == {k: v for k, v in before.items() if k != "user"}
result["trace"] = trace
api.session.close()
print(json.dumps(result, ensure_ascii=False))
