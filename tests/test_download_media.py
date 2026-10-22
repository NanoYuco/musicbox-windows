"""An HTTP success containing HTML must never become a successful MP3 download."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from NEMbox.cli import CliContext, cmd_download


@pytest.fixture
def non_audio_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html>media unavailable</html>")

        def log_message(self, format: str, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/song.mp3"
    finally:
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()


@pytest.mark.parametrize("existing", [False, True])
def test_html_response_is_not_saved_as_audio(
    non_audio_server, tmp_path, capsys, existing
):
    # Removing the response media check would falsely succeed and overwrite audio.
    target = tmp_path / "歌手-样本.mp3"
    if existing:
        target.write_bytes(b"previous valid audio")
    api = SimpleNamespace(
        dig_info=lambda _ids, _kind: [
            {
                "song_id": 123,
                "song_name": "样本",
                "artist": "歌手",
                "mp3_url": non_audio_server,
            }
        ]
    )
    args = SimpleNamespace(
        artist=None, album=None, songs=[123], playlist=None, path=str(tmp_path)
    )

    result = cmd_download(api, CliContext(json_mode=True), args)

    assert result == 1
    assert json.loads(capsys.readouterr().err)["error"]["type"] == "download_failed"
    if existing:
        assert target.read_bytes() == b"previous valid audio"
    else:
        assert not target.exists()
