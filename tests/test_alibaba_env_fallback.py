"""Outside a Hermes process (resolver not importable), keys come from the env."""

from __future__ import annotations

import sys

import pytest

from hermes_plugin_alibaba_image_gen import alibaba


@pytest.fixture
def no_resolver(monkeypatch):
    # `from hermes_cli.runtime_provider import ...` must raise ImportError.
    monkeypatch.setitem(sys.modules, "hermes_cli.runtime_provider", None)


def test_token_plan_intl_key_env_makes_available(no_resolver, monkeypatch):
    monkeypatch.setenv("ALIBABA_TOKEN_PLAN_API_KEY", " sk-tp ")
    assert alibaba.AlibabaImageGenProvider().is_available() is True


def test_no_keys_means_unavailable(no_resolver):
    assert alibaba.AlibabaImageGenProvider().is_available() is False


def test_shared_key_does_not_double_fire_on_cn_rung(no_resolver, monkeypatch):
    # The intl key is also the CN rung's compat fallback, but ladder order must pick the
    # *international* plan first: credentials land on the intl base URL.
    monkeypatch.setenv("ALIBABA_TOKEN_PLAN_API_KEY", "sk-tp")
    creds = alibaba._resolve_plan_credentials(alibaba._candidate_plans()[0])
    assert creds is not None
    key, base = creds
    assert key == "sk-tp"
    assert base.endswith("ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1")


def test_dashscope_key_falls_to_payg_intl(no_resolver, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-dash")
    plan = next(p for p in alibaba._candidate_plans() if p.profile == "alibaba")
    creds = alibaba._resolve_plan_credentials(plan)
    assert creds == ("sk-dash", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")


def test_cn_base_env_overrides_payg_cn_default(no_resolver, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-dash")
    monkeypatch.setenv("DASHSCOPE_CN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    plan = next(p for p in alibaba._candidate_plans() if p.profile == "alibaba-cn")
    creds = alibaba._resolve_plan_credentials(plan)
    assert creds and creds[1].endswith("dashscope.aliyuncs.com/compatible-mode/v1")


def test_env_only_cn_plan_prefers_its_own_key(no_resolver, monkeypatch):
    monkeypatch.setenv("ALIBABA_TOKEN_PLAN_CN_API_KEY", "sk-cn-first")
    monkeypatch.setenv("ALIBABA_TOKEN_PLAN_API_KEY", "sk-intl")
    plan = next(p for p in alibaba._candidate_plans() if p.profile == "alibaba-token-plan-cn")
    creds = alibaba._resolve_plan_credentials(plan)
    assert creds and creds[0] == "sk-cn-first"
