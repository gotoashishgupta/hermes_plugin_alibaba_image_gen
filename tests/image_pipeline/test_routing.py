"""Routing ladder: preference order, explicit request, exclusions, and the
credential-source honesty split (hermes-ladder vs env vs cli)."""

from __future__ import annotations

import pytest

from imagegen import routing
from imagegen.envelope import GenRequest, UsageError
from image_pipeline.helpers import PNG_1PX  # noqa: F401 - ensures conftest import path


def req():
    return GenRequest(prompt="p")


def test_nothing_credentialed_raises_usage_error():
    with pytest.raises(UsageError) as e:
        routing.build_chain(req(), mode="standalone", requested=None)
    assert "no credentialed image provider" in str(e.value)


def test_preference_order(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "f")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("ALIBABA_TOKEN_PLAN_API_KEY", "a")
    chain = routing.build_chain(req(), mode="standalone", requested=None)
    names = [b.name for b in chain]
    assert names == ["openai", "alibaba", "fal"]  # PREFERRED order, gaps closed


def test_requested_first_then_fallback_order(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "f")
    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    chain = routing.build_chain(req(), mode="standalone", requested="fal")
    assert chain[0].name == "fal"
    assert [b.name for b in chain[1:]] == ["openrouter"]


def test_requested_not_credentialed_raises(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "f")
    with pytest.raises(UsageError):
        routing.build_chain(req(), mode="standalone", requested="gemini")


def test_hermes_mode_without_repo_raises(monkeypatch, tmp_path):
    with pytest.raises(UsageError) as e:
        routing.build_chain(req(), mode="hermes", requested=None)
    assert "--mode standalone" in str(e.value)


def test_credential_source_classification(monkeypatch, tmp_path):
    # config names the provider → hermes-ladder; env-only → env; cli key → cli
    class FakeBackend:
        name = "openai-codex"
        key_envs = ()  # OAuth: declares none
    cfg = tmp_path / ".hermes"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.yaml").write_text("image_gen:\n  provider: openai-codex\n")
    monkeypatch.setattr(routing.hermes_mode, "DEFAULT_HERMES_HOME", cfg)
    assert routing.credential_source_for(FakeBackend(), req()) == "hermes-ladder"

    class FakeTP:
        name = "alibaba"
        key_envs = ("ALIBABA_TOKEN_PLAN_API_KEY",)
    monkeypatch.setenv("ALIBABA_TOKEN_PLAN_API_KEY", "tp")
    assert routing.credential_source_for(FakeTP(), req()) == "env"
    assert routing.credential_source_for(FakeTP(), GenRequest(prompt="p", api_key="k")) == "cli"


def test_reexec_guard_skips_standalone_and_pinned():
    # no repo anywhere: must be a silent no-op, never exec
    routing.maybe_reexec_into_venv(["imagegen.py", "list"], mode="standalone", has_api_key=False)
    routing.maybe_reexec_into_venv(["imagegen.py", "list"], mode="auto", has_api_key=True)


def test_reexec_executes_once_with_fake_venv(monkeypatch, tmp_path):
    fake = tmp_path / "py"
    fake.write_text("#!/bin/sh\nexit 42\n")
    fake.chmod(0o755)
    repo = tmp_path / "hermes-agent"
    (repo / "agent").mkdir(parents=True)
    (repo / "agent" / "image_gen_registry.py").write_text("")
    monkeypatch.setattr(routing.hermes_mode, "hermes_repo", lambda: repo)
    monkeypatch.setattr(routing.hermes_mode, "hermes_importable", lambda r: False)
    monkeypatch.setattr(routing.hermes_mode, "venv_python", lambda r: fake)
    import os
    monkeypatch.setattr(os, "execve", lambda *a, **k: (_ for _ in ()).throw(OSError("denied")))
    # execve raising → warning + continue (never die)
    routing.maybe_reexec_into_venv([str(tmp_path / "imagegen.py"), "list"],
                                   mode="auto", has_api_key=False)
