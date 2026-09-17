"""generate(): token-plan happy path, payload shape, response parsing, hardening."""

from __future__ import annotations

import pytest

from conftest import TP_INTL, token_plan_body
import alibaba
from alibaba import AlibabaImageGenProvider


def generate(prompt="a red fox", **kwargs):
    return AlibabaImageGenProvider().generate(prompt, **kwargs)


def test_happy_path_returns_saved_image_and_reports_plan(tp_creds, posted, saved):
    posted["queue"] = [(token_plan_body(), None)]
    result = generate(prompt="a red fox")
    assert result["success"] is True
    assert result["image"] == "/tmp/cached-alibaba.png"
    assert result["provider"] == "alibaba"
    assert result["model"] == "wan2.7-image"
    assert result["prompt"] == "a red fox"
    assert result["aspect_ratio"] == "landscape"
    assert result["modality"] == "text"
    assert result["plan"] == "alibaba-token-plan"
    assert result["plans_tried"] == ["alibaba-token-plan"]


def test_payload_shape_and_auth_header(tp_creds, posted, saved):
    posted["queue"] = [(token_plan_body(), None)]
    generate(prompt="a red fox")
    call = posted["calls"][0]
    assert call["url"] == f"{TP_INTL}/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-tp"
    assert call["payload"]["model"] == "wan2.7-image"
    assert call["payload"]["size"] == "1280*720"
    parts = call["payload"]["messages"][0]["content"]
    assert parts == [{"type": "text", "text": "a red fox"}]


@pytest.mark.parametrize("aspect,size", [
    ("landscape", "1280*720"),
    ("square", "1024*1024"),
    ("portrait", "720*1280"),
    ("nonsense", "1280*720"),  # invalid aspect resolves to landscape
])
def test_aspect_maps_to_size(tp_creds, posted, saved, aspect, size):
    posted["queue"] = [(token_plan_body(), None)]
    result = generate(aspect_ratio=aspect)
    assert posted["calls"][0]["payload"]["size"] == size
    assert result["aspect_ratio"] == ("landscape" if aspect == "nonsense" else aspect)


def test_debug_info_surfaces_seed_and_dimensions(tp_creds, posted, saved):
    posted["queue"] = [(token_plan_body(), None)]
    result = generate()
    assert result["seed"] == 1234
    assert result["width"] == 1280
    assert result["height"] == 720


def test_absent_debug_info_yields_no_seed_keys(tp_creds, posted, saved):
    posted["queue"] = [(token_plan_body(debug=False), None)]
    result = generate()
    assert result["success"] is True
    assert "seed" not in result and "width" not in result


def test_data_uri_image_materializes_as_base64(tp_creds, posted, saved):
    posted["queue"] = [(token_plan_body(image="data:image/png;base64,QUJD"), None)]
    result = generate()
    assert result["success"] is True
    assert saved["b64"] == "QUJD" and saved["url"] is None


def test_standard_openai_shape_body_also_parses(tp_creds, posted, saved):
    body = {"choices": [{"message": {"content": [
        {"type": "image", "image": "https://example/y.png"}]}}]}
    posted["queue"] = [(body, None)]
    result = generate()
    assert result["success"] is True
    assert saved["url"] == "https://example/y.png"


def test_materialize_prefix_is_per_plan(tp_creds, posted, saved):
    posted["queue"] = [(token_plan_body(), None)]
    generate()
    assert saved["prefix"] == "alibaba_alibaba-token-plan"


def test_no_image_anywhere_fails_loudly_with_excerpt(tp_creds, posted, saved):
    posted["queue"] = [({"output": {"choices": [{"message": {"content": "sorry, no"}}]}}, None)]
    result = generate()
    assert result["success"] is False
    assert result["error_type"] == "empty_response"
    assert "sorry" in result["error"]


def test_empty_prompt_is_invalid_input_without_network(tp_creds, posted, saved):
    result = generate(prompt="   ")
    assert result["success"] is False
    assert result["error_type"] == "invalid_input"
    assert posted["calls"] == []


def test_generate_never_raises_on_broken_plumbing(tp_creds, posted, saved, monkeypatch):
    def explode(*a, **k):
        raise KeyError("boom")

    monkeypatch.setattr(alibaba, "post_json", explode)
    result = generate()
    assert result["success"] is False
    assert "boom" in result["error"]
