from imagegen.gemini import GeminiAdapter
from imagegen.envelope import GenRequest
from image_pipeline.helpers import gemini_body, http_failure, make_png


def req(**kw):
    return GenRequest(prompt="brand poster minimal", **kw)


def test_payload_shape_and_auth(monkeypatch, net):
    monkeypatch.setenv("GEMINI_API_KEY", "gk")
    net["queue"].append((gemini_body(), None))
    r = GeminiAdapter().generate(req(aspect="square"))
    call = net["calls"][0]
    assert call["url"].endswith("/models/gemini-3.1-flash-image:generateContent")
    assert call["headers"]["x-goog-api-key"] == "gk"
    cfg = call["payload"]["generationConfig"]
    assert cfg["responseModalities"] == ["IMAGE"] and cfg["imageConfig"]["aspectRatio"] == "1:1"
    assert r.success and r.model == "gemini-3.1-flash-image"


def test_google_api_key_alias(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "g2")
    assert GeminiAdapter().available(req())


def test_delivered_ratio_mismatch_is_measured_and_noted(monkeypatch, net):
    """2026 caveat: endpoints may ignore imageConfig — we MEASURE, never trust."""
    monkeypatch.setenv("GEMINI_API_KEY", "gk")
    net["queue"].append((gemini_body(make_png(1024, 1024)), None))  # asked 16:9, got 1:1
    r = GeminiAdapter().generate(req(size=(1920, 1080)))
    assert (r.width, r.height) == (1024, 1024)
    assert r.size_requested == "1920x1080" and "deviates >5%" in r.note


def test_refs_inlined(monkeypatch, net, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "gk")
    ref = tmp_path / "mood.png"
    ref.write_bytes(make_png(24, 24))
    net["queue"].append((gemini_body(), None))
    r = GeminiAdapter().generate(req(references=(str(ref),)))
    parts = net["calls"][0]["payload"]["contents"][0]["parts"]
    assert parts[1]["inlineData"]["mimeType"] == "image/png"
    assert r.success


def test_auth_error_typed(monkeypatch, net):
    monkeypatch.setenv("GEMINI_API_KEY", "gk")
    net["queue"].append((None, http_failure("http", status=403, message="denied")))
    r = GeminiAdapter().generate(req())
    assert not r.success and r.error_type == "auth_error"


def test_no_candidates_empty_response(monkeypatch, net):
    monkeypatch.setenv("GEMINI_API_KEY", "gk")
    net["queue"].append(({"candidates": [{"content": {"parts": [{"text": "sorry"}]}}]}, None))
    r = GeminiAdapter().generate(req())
    assert not r.success and r.error_type == "empty_response"
