"""The custom (dedicated workspace / OpenAI-compat) rung: resolution, position, guards."""

from __future__ import annotations

import pytest

from conftest import TP_INTL, fake_runtime, token_plan_body
from hermes_plugin_alibaba_image_gen.alibaba import AlibabaImageGenProvider

WS = "https://ws-hb5vus2qhrcc9f96.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"


@pytest.fixture
def no_named_creds(monkeypatch):
    """No named plan resolves — the custom rung must stand on its own env/config chain."""
    def fake(requested=None, **kwargs):
        raise RuntimeError(f"no credentials for {requested}")

    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", fake)


@pytest.fixture
def named(monkeypatch):
    """Scriptable named-plan resolver (profile -> runtime | exception), recording probes."""
    box = {"calls": [], "plans": {}}

    def fake(requested=None, **kwargs):
        box["calls"].append(requested)
        outcome = box["plans"].get(requested)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is None:
            raise RuntimeError(f"no credentials for {requested}")
        return outcome

    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", fake)
    return box


@pytest.fixture
def cfg_tree(monkeypatch):
    box = {}

    def fake_load_config(*a, **k):
        return dict(box)

    monkeypatch.setattr("hermes_cli.config.load_config", fake_load_config)
    return box


def make_provider():
    return AlibabaImageGenProvider()


def _ws_pair(monkeypatch):
    monkeypatch.setenv("ALIBABA_API_KEY", "sk-ws")
    monkeypatch.setenv("ALIBABA_BASE_URL", WS)


# --- tier 1: env pair ---------------------------------------------------------

def test_env_pair_alone_makes_provider_available(no_named_creds, monkeypatch):
    monkeypatch.setenv("ALIBABA_API_KEY", "sk-ws")
    monkeypatch.setenv("ALIBABA_BASE_URL", WS)
    assert make_provider().is_available() is True


def test_default_endpoint_is_images_generations(no_named_creds, posted, saved, monkeypatch):
    _ws_pair(monkeypatch)
    posted["queue"] = [({"data": [{"url": "https://img/o.png"}]}, None)]
    result = make_provider().generate("a fox", aspect_ratio="landscape")
    assert result["success"] is True and result["plan"] == "custom"
    assert len(posted["calls"]) == 1
    assert posted["calls"][0]["url"] == f"{WS}/images/generations"
    assert posted["calls"][0]["payload"]["prompt"] == "a fox"
    assert posted["calls"][0]["payload"]["n"] == 1
    assert posted["calls"][0]["payload"]["size"] == "1536x1024"


def test_half_set_pair_is_ignored(no_named_creds, monkeypatch):
    monkeypatch.setenv("ALIBABA_API_KEY", "sk-ws")           # base missing
    assert make_provider().is_available() is False
    monkeypatch.delenv("ALIBABA_API_KEY")
    monkeypatch.setenv("ALIBABA_BASE_URL", WS)               # key missing
    assert make_provider().is_available() is False


def test_pin_custom_without_pair_reports_missing_key(no_named_creds, monkeypatch):
    monkeypatch.setenv("ALIBABA_IMAGE_PLAN", "custom")
    result = make_provider().generate("a fox")
    assert result["success"] is False and result["error_type"] == "missing_api_key"


# --- tier 2/3: config.yaml ----------------------------------------------------

def test_config_scoped_creds_work(no_named_creds, posted, saved, cfg_tree, monkeypatch):
    cfg_tree["image_gen"] = {"alibaba": {"api_key": "sk-cfg", "base_url": WS}}
    monkeypatch.setenv("ALIBABA_IMAGE_ENDPOINT", "/chat/completions")
    posted["queue"] = [(token_plan_body(), None)]
    result = make_provider().generate("a fox")
    assert result["success"] is True and result["plan"] == "custom"
    assert posted["calls"][0]["url"] == f"{WS}/chat/completions"
    assert posted["calls"][0]["headers"]["Authorization"] == "Bearer sk-cfg"


def test_provider_ref_tier_reads_config_providers(no_named_creds, posted, saved, cfg_tree, monkeypatch):
    monkeypatch.setenv("WS1_KEY", "sk-from-env-var")
    cfg_tree["image_gen"] = {"alibaba": {"provider": "ws1"}}
    cfg_tree["providers"] = {"ws1": {"api": WS, "key_env": "WS1_KEY"}}
    monkeypatch.setenv("ALIBABA_IMAGE_ENDPOINT", "/chat/completions")
    posted["queue"] = [(token_plan_body(), None)]
    result = make_provider().generate("a fox")
    assert result["success"] is True and result["plan"] == "custom"
    assert posted["calls"][0]["headers"]["Authorization"] == "Bearer sk-from-env-var"


def test_env_pair_beats_config_scoped(no_named_creds, posted, saved, cfg_tree, monkeypatch):
    monkeypatch.setenv("ALIBABA_API_KEY", "sk-env")
    monkeypatch.setenv("ALIBABA_BASE_URL", WS)
    cfg_tree["image_gen"] = {"alibaba": {"api_key": "sk-cfg", "base_url": "https://stale.example/v1"}}
    monkeypatch.setenv("ALIBABA_IMAGE_ENDPOINT", "/chat/completions")
    posted["queue"] = [(token_plan_body(), None)]
    make_provider().generate("a fox")
    assert posted["calls"][0]["url"] == f"{WS}/chat/completions"


# --- explicit endpoint (ALIBABA_IMAGE_ENDPOINT / image_gen.alibaba.endpoint) ---

def test_explicit_chat_endpoint_posts_chat(no_named_creds, posted, saved, monkeypatch):
    _ws_pair(monkeypatch)
    monkeypatch.setenv("ALIBABA_IMAGE_ENDPOINT", "/chat/completions")
    posted["queue"] = [(token_plan_body(), None)]
    result = make_provider().generate("a fox")
    assert result["success"] is True
    assert len(posted["calls"]) == 1
    assert posted["calls"][0]["url"] == f"{WS}/chat/completions"
    assert posted["calls"][0]["payload"]["messages"][0]["content"][0]["text"] == "a fox"


def test_explicit_images_endpoint_posts_images_once(no_named_creds, posted, saved, monkeypatch):
    _ws_pair(monkeypatch)
    monkeypatch.setenv("ALIBABA_IMAGE_ENDPOINT", "/images/generations")
    posted["queue"] = [({"data": [{"url": "https://img/o.png"}]}, None)]
    result = make_provider().generate("a fox", aspect_ratio="landscape")
    assert result["success"] is True and result["plan"] == "custom"
    assert len(posted["calls"]) == 1
    assert posted["calls"][0]["url"] == f"{WS}/images/generations"
    assert posted["calls"][0]["payload"]["prompt"] == "a fox"
    assert posted["calls"][0]["payload"]["n"] == 1
    assert posted["calls"][0]["payload"]["size"] == "1536x1024"


def test_explicit_images_endpoint_no_retry_on_404(no_named_creds, posted, monkeypatch):
    from plugins.image_gen._common import HttpFailure
    _ws_pair(monkeypatch)
    monkeypatch.setenv("ALIBABA_IMAGE_ENDPOINT", "/images/generations")
    posted["queue"] = [(None, HttpFailure("http", "nf", "api_error", status=404, message="nf"))]
    result = make_provider().generate("a fox")
    assert result["success"] is False
    assert len(posted["calls"]) == 1


def test_explicit_custom_path_posts_once(no_named_creds, posted, saved, monkeypatch):
    _ws_pair(monkeypatch)
    monkeypatch.setenv("ALIBABA_IMAGE_ENDPOINT", "/v1/foo/bar")
    posted["queue"] = [({"data": [{"url": "https://img/o.png"}]}, None)]
    result = make_provider().generate("a fox")
    assert result["success"] is True
    assert len(posted["calls"]) == 1
    assert posted["calls"][0]["url"] == f"{WS}/v1/foo/bar"
    assert "messages" in posted["calls"][0]["payload"]


def test_explicit_endpoint_applies_to_named_plan(named, posted, saved, monkeypatch):
    named["plans"]["alibaba-token-plan"] = fake_runtime("sk-tp", TP_INTL, provider="alibaba-token-plan")
    monkeypatch.setenv("ALIBABA_IMAGE_ENDPOINT", "/images/generations")
    posted["queue"] = [({"data": [{"url": "https://img/o.png"}]}, None)]
    result = make_provider().generate("a fox")
    assert result["success"] is True and result["plan"] == "alibaba-token-plan"
    assert posted["calls"][0]["url"] == f"{TP_INTL}/images/generations"


def test_config_endpoint_when_env_clear(no_named_creds, posted, saved, cfg_tree, monkeypatch):
    _ws_pair(monkeypatch)
    cfg_tree["image_gen"] = {"alibaba": {"endpoint": "/chat/completions"}}
    posted["queue"] = [(token_plan_body(), None)]
    result = make_provider().generate("a fox")
    assert result["success"] is True
    assert posted["calls"][0]["url"] == f"{WS}/chat/completions"


def test_env_beats_config_endpoint(no_named_creds, posted, saved, cfg_tree, monkeypatch):
    _ws_pair(monkeypatch)
    monkeypatch.setenv("ALIBABA_IMAGE_ENDPOINT", "/chat/completions")
    cfg_tree["image_gen"] = {"alibaba": {"endpoint": "/images/generations"}}
    posted["queue"] = [(token_plan_body(), None)]
    result = make_provider().generate("a fox")
    assert result["success"] is True
    assert posted["calls"][0]["url"] == f"{WS}/chat/completions"


def test_invalid_endpoint_falls_through_to_default(no_named_creds, posted, saved, monkeypatch):
    _ws_pair(monkeypatch)
    monkeypatch.setenv("ALIBABA_IMAGE_ENDPOINT", "not-a-path")  # no leading /
    posted["queue"] = [({"data": [{"url": "https://img/o.png"}]}, None)]
    result = make_provider().generate("a fox")
    assert result["success"] is True
    assert posted["calls"][0]["url"] == f"{WS}/images/generations"


def test_images_endpoint_with_refs_returns_modality_unsupported(no_named_creds, posted, monkeypatch):
    _ws_pair(monkeypatch)
    result = make_provider().generate("a fox", image_url="https://e/ref.png")
    assert result["success"] is False
    assert result["error_type"] == "modality_unsupported"
    assert len(posted["calls"]) == 0


# --- extra response shapes ------------------------------------------------------

def test_message_images_array_shape_parses(no_named_creds, posted, saved, monkeypatch):
    _ws_pair(monkeypatch)
    monkeypatch.setenv("ALIBABA_IMAGE_ENDPOINT", "/chat/completions")
    body = {"choices": [{"message": {"images": [{"image_url": {"url": "https://img/m.png"}}]}}]}
    posted["queue"] = [(body, None)]
    result = make_provider().generate("a fox")
    assert result["success"] is True
    assert saved["url"] == "https://img/m.png"


def test_images_b64_json_shape(no_named_creds, posted, saved, monkeypatch):
    _ws_pair(monkeypatch)
    posted["queue"] = [({"data": [{"b64_json": "QUJD"}]}, None)]
    result = make_provider().generate("a fox")
    assert result["success"] is True
    assert saved["b64"] == "QUJD"


def test_setup_schema_keeps_picker_env_vars_single():
    schema = make_provider().get_setup_schema()
    assert [e["key"] for e in schema["env_vars"]] == ["ALIBABA_TOKEN_PLAN_API_KEY"]
    assert "ALIBABA_API_KEY" in [e["key"] for e in schema["optional_env_vars"]]


# --- ladder position: custom is LAST ------------------------------------------

def test_named_plan_tried_first_when_both_configured(named, posted, saved, monkeypatch):
    named["plans"]["alibaba-token-plan"] = fake_runtime("sk-tp", TP_INTL, provider="alibaba-token-plan")
    monkeypatch.setenv("ALIBABA_API_KEY", "sk-ws")
    monkeypatch.setenv("ALIBABA_BASE_URL", WS)
    monkeypatch.setenv("ALIBABA_IMAGE_ENDPOINT", "/chat/completions")
    posted["queue"] = [(token_plan_body(), None)]
    result = make_provider().generate("a fox")
    assert result["plan"] == "alibaba-token-plan"
    assert posted["calls"][0]["url"] == f"{TP_INTL}/chat/completions"


def test_named_failure_advances_to_custom_last(named, posted, saved, monkeypatch):
    from conftest import TP_INTL as _TP
    from plugins.image_gen._common import HttpFailure
    named["plans"]["alibaba-token-plan"] = fake_runtime("sk-tp", _TP, provider="alibaba-token-plan")
    monkeypatch.setenv("ALIBABA_API_KEY", "sk-ws")
    monkeypatch.setenv("ALIBABA_BASE_URL", WS)
    monkeypatch.setenv("ALIBABA_IMAGE_ENDPOINT", "/chat/completions")
    fail401 = HttpFailure("http", "bad key", "api_error", status=401, message="bad key")
    posted["queue"] = [(None, fail401), (token_plan_body(), None)]
    result = make_provider().generate("a fox")
    assert result["success"] is True
    assert result["plan"] == "custom"
    assert result["plans_tried"] == ["alibaba-token-plan", "custom"]
    assert posted["calls"][1]["url"] == f"{WS}/chat/completions"
