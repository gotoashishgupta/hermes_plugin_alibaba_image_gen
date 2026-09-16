"""Cross-plan resilience: which failures advance the login ladder, which stop."""

from __future__ import annotations

import pytest

from conftest import TP_INTL, fake_runtime, token_plan_body
from hermes_plugin_image_gen_ext.alibaba import AlibabaImageGenProvider
from plugins.image_gen._common import HttpFailure

TP_CN = "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
PAYG_INTL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


def http_failure(status, message):
    return HttpFailure("http", f"Alibaba failed ({status}): {message}", "api_error",
                       status=status, message=message)


def make_provider():
    return AlibabaImageGenProvider()


def two_token_plans(resolver_plans):
    resolver_plans["plans"] = {
        "alibaba-token-plan": fake_runtime("sk-tp", TP_INTL, provider="alibaba-token-plan"),
        "alibaba-token-plan-cn": fake_runtime("sk-cn", TP_CN, provider="alibaba-token-plan-cn"),
    }


@pytest.mark.parametrize("failure", [
    http_failure(401, "Invalid API-key provided."),
    http_failure(403, "Account frozen."),
    http_failure(429, "Free allocated quota exceeded."),
    HttpFailure("timeout", "timed out (300s)", "timeout"),
    HttpFailure("connection", "conn reset", "connection_error"),
])
def test_transparent_plan_failures_advance(resolver_plans, posted, saved, failure):
    two_token_plans(resolver_plans)
    posted["queue"] = [(None, failure), (token_plan_body(), None)]
    result = make_provider().generate("x")
    assert result["success"] is True
    assert result["plan"] == "alibaba-token-plan-cn"
    assert result["plans_tried"] == ["alibaba-token-plan", "alibaba-token-plan-cn"]
    assert posted["calls"][1]["url"] == f"{TP_CN}/chat/completions"


def test_client_error_stops_immediately_without_switching_plan(resolver_plans, posted, saved):
    two_token_plans(resolver_plans)
    posted["queue"] = [(None, http_failure(400, "parameter 'size' is invalid"))]
    result = make_provider().generate("x")
    assert result["success"] is False
    assert len(posted["calls"]) == 1  # a bad payload fails on every plan — no silent retry


def test_verified_plan_model_error_is_plain_failure(resolver_plans, posted, saved):
    two_token_plans(resolver_plans)
    posted["queue"] = [(None, http_failure(400, "The model wan2.99-image does not exist"))]
    result = make_provider().generate("x", model="wan2.99-image")
    assert result["success"] is False
    assert len(posted["calls"]) == 1


def test_unverified_payg_model_error_advances_and_hints(resolver_plans, posted, saved, monkeypatch):
    monkeypatch.setenv("ALIBABA_IMAGE_PLAN", "alibaba")
    resolver_plans["plans"]["alibaba"] = fake_runtime("sk-dash", PAYG_INTL, provider="alibaba")
    posted["queue"] = [(None, http_failure(404, "model not found"))]
    result = make_provider().generate("x")
    assert result["success"] is False
    assert "/models" in result["error"]  # tells the operator how to check PAYG catalog


def test_all_plans_fail_names_every_one(resolver_plans, posted, saved):
    two_token_plans(resolver_plans)
    posted["queue"] = [
        (None, http_failure(401, "bad key tp")),
        (None, http_failure(401, "bad key cn")),
    ]
    result = make_provider().generate("x")
    assert result["success"] is False
    for profile in ("alibaba-token-plan", "alibaba-token-plan-cn"):
        assert profile in result["error"]


def test_explicit_api_key_skips_the_credential_ladder(resolver_plans, posted, saved, monkeypatch):
    def boom(**kwargs):
        raise AssertionError("resolver must not be consulted when a key is pinned")

    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", boom)
    posted["queue"] = [(token_plan_body(), None)]
    result = make_provider().generate("x", api_key="sk-user", base_url=TP_CN)
    assert result["success"] is True
    assert posted["calls"][0]["url"] == f"{TP_CN}/chat/completions"
    assert posted["calls"][0]["headers"]["Authorization"] == "Bearer sk-user"
    assert result["plan"] == "explicit"


def test_pinned_key_defaults_to_token_plan_intl_base(resolver_plans, posted, saved, monkeypatch):
    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider",
                        lambda **kw: (_ for _ in ()).throw(AssertionError()))
    posted["queue"] = [(token_plan_body(), None)]
    make_provider().generate("x", api_key="sk-user")
    assert posted["calls"][0]["url"] == f"{TP_INTL}/chat/completions"
