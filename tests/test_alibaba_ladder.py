"""Credential ladder: which plan makes the provider available, in which order."""

from __future__ import annotations

import pytest

from conftest import fake_runtime
from hermes_plugin_image_gen_ext.alibaba import AlibabaImageGenProvider

TP_INTL = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
TP_CN = "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
PAYG_INTL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


@pytest.fixture
def resolver(monkeypatch):
    """Patch Hermes' runtime resolver; records the profile ids probed, in order."""
    calls = []
    box = {"plans": {}}  # profile -> runtime dict OR exception

    def fake(requested=None, **kwargs):
        assert requested, "resolver must be called with the plan's profile"
        calls.append(requested)
        outcome = box["plans"].get(requested)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is None:
            raise RuntimeError(f"no credentials found for provider '{requested}'")
        return outcome

    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", fake)
    return type("R", (), {"calls": calls, "plans": box["plans"]})()


def make_provider():
    return AlibabaImageGenProvider()


def test_first_plan_with_key_wins_and_probing_stops(resolver):
    resolver.plans["alibaba-token-plan"] = fake_runtime("sk-tp", TP_INTL, provider="alibaba-token-plan")
    assert make_provider().is_available() is True
    assert resolver.calls == ["alibaba-token-plan"]


def test_unavailable_when_no_plan_resolves(resolver):
    assert make_provider().is_available() is False
    # Every *eligible* plan probed once, in preference order (CN PAYG is gated on its
    # base var, see test_alibaba_cn_is_skipped_without_its_base_url) — and nothing else
    # can fail: no network.
    assert resolver.calls == ["alibaba-token-plan", "alibaba-token-plan-cn", "alibaba"]


def test_skips_plan_whose_runtime_points_at_the_wrong_host(resolver):
    # Guards against a resolver fallback handing us e.g. an OpenRouter runtime: a key whose
    # base_url host does not match the plan's own endpoint (or its *_BASE_URL override) is unusable.
    resolver.plans["alibaba-token-plan"] = fake_runtime(
        "sk-wrong", "https://openrouter.ai/api/v1", provider="alibaba-token-plan")
    resolver.plans["alibaba-token-plan-cn"] = fake_runtime("sk-tp-cn", TP_CN, provider="alibaba-token-plan-cn")
    assert make_provider().is_available() is True
    assert resolver.calls == ["alibaba-token-plan", "alibaba-token-plan-cn"]


def test_base_env_override_is_accepted_as_matching_host(resolver, monkeypatch):
    monkeypatch.setenv("ALIBABA_TOKEN_PLAN_BASE_URL", "https://my-proxy.example/compatible-mode/v1")
    resolver.plans["alibaba-token-plan"] = fake_runtime(
        "sk-tp", "https://my-proxy.example/compatible-mode/v1", provider="alibaba-token-plan")
    assert make_provider().is_available() is True
    assert resolver.calls == ["alibaba-token-plan"]


def test_payg_intl_plan_reaches_after_token_plans(resolver):
    resolver.plans["alibaba"] = fake_runtime("sk-dash", PAYG_INTL, provider="alibaba")
    assert make_provider().is_available() is True
    assert resolver.calls == ["alibaba-token-plan", "alibaba-token-plan-cn", "alibaba"]


def test_alibaba_cn_is_skipped_without_its_base_url(resolver):
    # Same DASHSCOPE key as intl — without DASHSCOPE_CN_BASE_URL the CN host is a guaranteed 401.
    resolver.plans["alibaba"] = fake_runtime("sk-dash", PAYG_INTL, provider="alibaba")
    # intl PAYG resolves, so the ladder stops before CN anyway; probe further by removing intl:
    resolver.plans.clear()
    assert make_provider().is_available() is False
    assert "alibaba-cn" not in resolver.calls


def test_plan_pin_via_env_restricts_ladder(resolver, monkeypatch):
    monkeypatch.setenv("ALIBABA_IMAGE_PLAN", "alibaba")
    resolver.plans["alibaba"] = fake_runtime("sk-dash", PAYG_INTL, provider="alibaba")
    assert make_provider().is_available() is True
    assert resolver.calls == ["alibaba"]


def test_plan_pin_overrides_alibaba_cn_base_gate(resolver, monkeypatch):
    monkeypatch.setenv("ALIBABA_IMAGE_PLAN", "alibaba-cn")
    resolver.plans["alibaba-cn"] = fake_runtime(
        "sk-dash", "https://dashscope.aliyuncs.com/compatible-mode/v1", provider="alibaba-cn")
    assert make_provider().is_available() is True
    assert resolver.calls == ["alibaba-cn"]


def test_unknown_plan_pin_matches_nothing(resolver, monkeypatch):
    monkeypatch.setenv("ALIBABA_IMAGE_PLAN", "alibaba-coding-plan")  # out of scope by design
    assert make_provider().is_available() is False
    assert resolver.calls == []
    result = make_provider().generate("x")
    assert result["success"] is False and result["error_type"] == "missing_api_key"
