"""http.py transport: failure taxonomy mirrors _common.post_json; multipart; download."""

from __future__ import annotations

import io
import json
import socket
import urllib.error
import urllib.request

import pytest

from imagegen import http
from image_pipeline.helpers import PNG_1PX


class FakeResp(io.BytesIO):
    status = 200
    headers = {}
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_http_error_with_json_error_message(monkeypatch):
    body = json.dumps({"error": {"message": "quota exceeded"}}).encode()
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {},
                                     io.BytesIO(body))
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    got, failure = http.request_json("POST", "https://x.test/y", headers={}, payload={})
    assert got is None
    assert failure.kind == "http" and failure.status == 429
    assert failure.error_type == "api_error"
    assert "quota exceeded" in failure.error


def test_timeout_maps_to_timeout(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise socket.timeout("read timed out")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    _, failure = http.request_json("GET", "https://x.test", headers={}, timeout=(5, 30))
    assert failure.kind == "timeout" and failure.error_type == "timeout"


def test_connection_error_maps(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("name not resolved")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    _, failure = http.request_json("GET", "https://x.invalid", headers={})
    assert failure.kind == "connection" and failure.error_type == "connection_error"


def test_invalid_json_maps(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=None: FakeResp(b"<html>not json</html>"))
    got, failure = http.request_json("GET", "https://x.test", headers={})
    assert got is None and failure.error_type == "invalid_response"


def test_success_parses_json_and_sends_headers(monkeypatch):
    seen = {}
    def fake_urlopen(req, timeout=None):
        seen["hdrs"] = dict(req.headers)
        seen["body"] = req.data
        return FakeResp(b'{"ok": true}')
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    got, failure = http.request_json("POST", "https://x.test", headers={"Authorization": "Bearer k"},
                                     payload={"a": 1})
    assert failure is None and got == {"ok": True}
    assert seen["hdrs"]["Authorization"] == "Bearer k"
    assert json.loads(seen["body"]) == {"a": 1}


def test_download_writes_and_caps(tmp_path, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=None: FakeResp(PNG_1PX))
    dest = tmp_path / "sub" / "img.png"
    http.download("https://x.test/img.png", dest)
    assert dest.read_bytes() == PNG_1PX
    with pytest.raises(IOError):
        http.download("https://x.test/img.png", tmp_path / "cap.png", max_bytes=8)


def test_multipart_roundtrip():
    body, ctype = http.encode_multipart({"model": "gpt-image-2"},
                                        [("image[]", "a.png", PNG_1PX, "image/png")])
    assert "multipart/form-data; boundary=" in ctype
    blob = body.decode("latin-1")
    assert 'name="model"' in blob and "gpt-image-2" in blob
    assert 'name="image[]"; filename="a.png"' in blob and "image/png" in blob
