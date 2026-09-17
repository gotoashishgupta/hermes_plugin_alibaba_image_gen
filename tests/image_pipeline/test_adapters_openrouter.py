from imagegen.openrouter import OpenRouterAdapter
from imagegen.envelope import GenRequest
from image_pipeline.helpers import chat_images_body


def req(**kw):
    return GenRequest(prompt="watercolor harbor", **kw)


def test_default_model_uses_chat_surface(monkeypatch, net):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-k")
    net["queue"].append((chat_images_body(), None))
    r = OpenRouterAdapter().generate(req(aspect="landscape"))
    call = net["calls"][0]
    assert call["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert call["payload"]["modalities"] == ["image", "text"]
    assert call["payload"]["image_config"] == {"aspect_ratio": "16:9"}
    assert call["headers"]["Authorization"] == "Bearer or-k"
    assert r.success and net["downloads"]  # bare url materialized locally


def test_curated_model_uses_dedicated_image_api(monkeypatch, net, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-k")
    from image_pipeline.helpers import make_png
    ref = tmp_path / "style.png"
    ref.write_bytes(make_png(48, 48))
    net["queue"].append(({"data": [{"b64_json": "AAEC"}]}, None))
    r = OpenRouterAdapter().generate(req(
        model="google/gemini-3.1-flash-image", references=(str(ref),)))
    call = net["calls"][0]
    assert call["url"] == "https://openrouter.ai/api/v1/images/generations"
    assert call["payload"]["aspect_ratio"] == "16:9"
    assert call["payload"]["input_references"][0]["image_url"]["url"].startswith("data:")


def test_env_model_selection_respected(monkeypatch, net):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-k")
    monkeypatch.setenv("OPENROUTER_IMAGE_MODEL", "krea/krea-2-medium")
    net["queue"].append(({"data": [{"b64_json": "AAEC"}]}, None))
    r = OpenRouterAdapter().generate(req())
    assert net["calls"][0]["url"].endswith("/images/generations")
    assert r.model == "krea/krea-2-medium"


def test_size_snaps_to_ratio_enum(monkeypatch, net):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-k")
    net["queue"].append(({"data": [{"b64_json": "AAEC"}]}, None))
    r = OpenRouterAdapter().generate(
        req(model="openai/gpt-image-2", size=(1100, 1000)))
    assert net["calls"][0]["payload"]["aspect_ratio"] == "1:1"
    assert any("snapped" in n for n in r.notes)


def test_exact_169_size_no_snap_note(monkeypatch, net):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-k")
    net["queue"].append(({"data": [{"b64_json": "AAEC"}]}, None))
    r = OpenRouterAdapter().generate(
        req(model="openai/gpt-image-2", size=(1920, 1080)))
    assert net["calls"][0]["payload"]["aspect_ratio"] == "16:9"
