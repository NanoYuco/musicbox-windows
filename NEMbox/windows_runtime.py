"""Windows per-user discovery and authentication for the loopback daemon.

Only imported on Windows. No secrets are sent on the wire or stored in plaintext.
"""

import ctypes
import hashlib
import hmac
import json
import msvcrt
import os
import secrets
from ctypes import wintypes


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.c_void_p)]


def _crypt(data: bytes, *, decrypt: bool = False) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(_Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_Blob),
    ]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.c_void_p))
    target = _Blob()
    # CRYPTPROTECT_UI_FORBIDDEN, deliberately without LOCAL_MACHINE.
    if not function(
        ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel32.LocalFree(target.data)


def write_endpoint(path: str, port: int) -> bytes:
    key = secrets.token_bytes(32)
    protected = _crypt(json.dumps({"port": port, "key": key.hex()}).encode())
    with open(path, "wb") as stream:
        stream.write(protected)
    return key


def read_endpoint(path: str) -> tuple[int, bytes]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    # Share deletion so a simultaneous stop can remove discovery on Windows.
    handle = kernel32.CreateFileW(path, 0x80000000, 7, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    with os.fdopen(fd, "rb") as stream:
        protected = stream.read()
    endpoint = json.loads(_crypt(protected, decrypt=True))
    return int(endpoint["port"]), bytes.fromhex(endpoint["key"])


def pack(body: dict, key: bytes, nonce: str) -> bytes:
    message = {"body": body, "nonce": nonce}
    signed = json.dumps(message, sort_keys=True, ensure_ascii=False).encode("utf-8")
    message["mac"] = hmac.new(key, signed, hashlib.sha256).hexdigest()
    return json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"


def unpack(message: dict, key: bytes, nonce: str | None = None) -> tuple[dict, str]:
    body, received_nonce, mac = message["body"], message["nonce"], message["mac"]
    if (
        not isinstance(body, dict)
        or not isinstance(received_nonce, str)
        or not isinstance(mac, str)
    ):
        raise ValueError("invalid Windows daemon message")
    if nonce is not None and received_nonce != nonce:
        raise ValueError("unexpected Windows daemon response")
    signed = json.dumps(
        {"body": body, "nonce": received_nonce}, sort_keys=True, ensure_ascii=False
    ).encode("utf-8")
    expected = hmac.new(key, signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        raise ValueError("unauthenticated Windows daemon message")
    return body, received_nonce
