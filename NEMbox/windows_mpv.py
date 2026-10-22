"""mpv's raw, newline-delimited JSON over an overlapped Windows byte pipe."""

import _winapi
import json
import time
from typing import cast


def _complete(operation, deadline):
    remaining = max(0, int((deadline - time.monotonic()) * 1000))
    if _winapi.WaitForSingleObject(operation.event, remaining) == _winapi.WAIT_TIMEOUT:
        operation.cancel()
        operation.GetOverlappedResult(True)
        raise TimeoutError("mpv pipe timed out")
    count, error = operation.GetOverlappedResult(True)
    if error:
        raise OSError(error, "mpv pipe I/O failed")
    return count


def command(path, arguments, timeout=0.2):
    deadline = time.monotonic() + timeout
    _winapi.WaitNamedPipe(path, max(1, int(timeout * 1000)))
    handle = _winapi.CreateFile(
        path,
        _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
        0,
        0,
        _winapi.OPEN_EXISTING,
        _winapi.FILE_FLAG_OVERLAPPED,
        0,
    )
    try:
        payload = (
            json.dumps({"command": arguments, "request_id": 1}).encode("utf-8") + b"\n"
        )
        operation, _ = _winapi.WriteFile(handle, payload, overlapped=True)
        if _complete(operation, deadline) != len(payload):
            raise OSError("incomplete mpv pipe write")
        data = b""
        while True:
            operation, _ = _winapi.ReadFile(handle, 4096, overlapped=True)
            if not _complete(operation, deadline):
                raise OSError("mpv pipe closed")
            data += cast(bytes, operation.getbuffer())
            while b"\n" in data:
                line, data = data.split(b"\n", 1)
                response = json.loads(line)
                if response.get("request_id") == 1:
                    return response
    finally:
        _winapi.CloseHandle(handle)
