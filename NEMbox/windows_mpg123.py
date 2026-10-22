"""Native mpg123's --fifo command channel; responses still use stdout."""

import subprocess
import time


def output_parameters(executable, parameters):
    """Select the installed WinMM module only when no output was specified."""
    parameters = list(parameters)
    output_flags = {
        "-o",
        "--output",
        "-a",
        "--audiodevice",
        "-s",
        "--stdout",
        "-O",
        "--outfile",
        "-w",
        "--wav",
        "--au",
        "--cdr",
    }
    if any(
        p in output_flags or p.startswith(("--output=", "--audiodevice=", "-o"))
        for p in parameters
    ):
        return parameters
    try:
        result = subprocess.run(
            [executable, "--list-modules"],
            capture_output=True,
            timeout=1,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return parameters
    if any(
        line.split()[:2] == [b"win32", b"output"]
        for line in (result.stdout + result.stderr).splitlines()
    ):
        parameters += ["-o", "win32"]
    return parameters


def open_commands(process, path, timeout=5.0):
    deadline = time.monotonic() + timeout
    while process.poll() is None and time.monotonic() < deadline:
        try:
            return open(path, "wb", buffering=0)
        except OSError as exc:
            if (
                not isinstance(exc, FileNotFoundError)
                and getattr(exc, "winerror", None) != 231
            ):
                raise
            time.sleep(0.02)
    raise TimeoutError("mpg123 command pipe did not become ready")
