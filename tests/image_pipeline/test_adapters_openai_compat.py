from imagegen.openai_compat import OpenAICompatAdapter
from imagegen.envelope import GenRequest
from image_pipeline.helpers import http_failure, make_png, openai_b64_body


def req(**kw):
    return GenRequest(prompt="isometric city block", **kw)


def test_tier_model_maps_to_quality(monkeypatch, net):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    net["queue"].append((openai_b64_body(), None))
    r = OpenAICompatAdapter().generate(req(model="gpt-image-2-high", aspect="square"))
    call = net["calls"][0]
    assert call["url"] == "https://api.openai.com/v1/images/generations"
    assert call["payload"]["model"] == "gpt-image-2" and call["payload"]["quality"] == "high"
    assert call["payload"]["size"] == "1024x1024"
    assert "response_format" not in call["payload"]  # gpt-image-2 rejects it
    assert r.success and r.model == "gpt-image-2-high" and r.image.endswith(".png")


def test_size_snaps_to_fixed_three(monkeypatch, net):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    net["queue"].append((openai_b64_body(), None))
    r = OpenAICompatAdapter().generate(req(size=(1920, 1080)))
    assert net["calls"][0]["payload"]["size"] == "1536x1024"
    assert r.aspect_ratio == "landscape" and "snapped" in r.note
    assert r.size_requested == "1920x1080"


def test_refs_use_edits_multipart(monkeypatch, net, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    ref = tmp_path / "logo.png"
    ref.write_bytes(make_png(64, 64))
    net["queue"].append((openai_b64_body(), None))
    r = OpenAICompatAdapter().generate(req(references=(str(ref),)))
    call = net["calls"][0]
    assert call["url"] == "https://api.openai.com/v1/images/edits"
    assert call["payload"] is None and b"image[]" in call["raw_body"]
    assert r.success


def test_base_url_makes_it_the_generic_compat_gateway(monkeypatch, net):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    net["queue"].append((openai_b64_body(), None))
    r = OpenAICompatAdapter().generate(req(base_url="https://gw.example/v1/"))
    assert net["calls"][0]["url"] == "https://gw.example/v1/images/generations"
    assert r.route == "standalone"


def test_custom_instance_named_by_provider(monkeypatch, net):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-c")
    net["queue"].append((openai_b64_body(), None))
    a = OpenAICompatAdapter(instance_name="acme-img")
    r = a.generate(req(api_key="k", base_url="https://acme.test/v1"))
    assert r.provider == "acme-img" and r.credential_source == "cli"


def test_endpoint_selects_chat_surface(monkeypatch, net):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    from image_pipeline.helpers import chat_images_body
    net["queue"].append((chat_images_body(), None))
    r = OpenAICompatAdapter().generate(req(endpoint="/chat/completions"))
    call = net["calls"][0]
    assert call["url"].endswith("/chat/completions")
    assert call["payload"]["messages"] and "modalities" not in call["payload"]
    assert r.success  # choices[].message.images extracted + downloaded


def test_missing_key_fails_cleanly():
    r = OpenAICompatAdapter().generate(req())
    assert not r.success and r.error_type == "missing_api_key"


def test_auth_error_passthrough(monkeypatch, net):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-bad")
    net["queue"].append((None, http_failure("http", status=401, message="bad key")))
    r = OpenAICompatAdapter().generate(req())
    assert not r.success and r.error_type == "api_error" and "401" in r.error
