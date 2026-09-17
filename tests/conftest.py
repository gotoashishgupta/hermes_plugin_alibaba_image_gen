"""Shared test setup: Hermes-repo import path, credential-env hygiene, resolver fakes."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Our package imports `agent.*` / `plugins.image_gen._common` / `hermes_cli.*` — all provided
# by the hermes-agent checkout (editable-installed in its venv, or added to sys.path here so
# the tests also run from any other interpreter).
_REPO = Path(os.environ.get("HERMES_AGENT_REPO", str(Path.home() / ".hermes" / "hermes-agent")))
if (_REPO / "agent" / "image_gen_provider.py").is_file() and str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_ROOT = Path(__file__).resolve().parents[1]
_PLUGIN = _ROOT / "plugins" / "image_gen_alibaba"
if str(_PLUGIN) not in sys.path:
    sys.path.insert(0, str(_PLUGIN))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

CREDENTIAL_ENVS = (
    "ALIBABA_TOKEN_PLAN_API_KEY",
    "ALIBABA_TOKEN_PLAN_CN_API_KEY",
    "ALIBABA_API_KEY",
    "ALIBABA_BASE_URL",
    "DASHSCOPE_API_KEY",
    "ALIBABA_IMAGE_MODEL",
    "ALIBABA_IMAGE_PLAN",
    "ALIBABA_IMAGE_ENDPOINT",
)


@pytest.fixture(autouse=True)
def clean_env(tmp_path, monkeypatch):
    """No inherited keys/HERMES_HOME: every test starts credential-less and config-less."""
    for var in CREDENTIAL_ENVS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))


def fake_runtime(api_key: str, base_url: str, provider: str = "x", source: str = "test"):
    """Shape of a resolved runtime dict (hermes_cli.runtime_provider._runtime)."""
    return {
        "provider": provider,
        "api_mode": "chat_completions",
        "base_url": base_url,
        "api_key": api_key,
        "source": source,
    }


TP_INTL = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"

PNG_BYTES = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 16


def token_plan_body(image="https://dashscope-result.example/x.png", debug=True):
    """Verified Token Plan chat/completions response: image URL inside output.choices parts."""
    body = {
        "output": {"choices": [{"message": {"content": [
            {"type": "text", "text": "Here you go!"},
            {"type": "image", "image": image},
        ]}}]},
        "model": "wan2.7-image",
    }
    if debug:
        body["output"]["debug_info"] = [{"actual_seed": 1234, "output_W": 1280, "output_H": 720}]
    return body


@pytest.fixture
def tp_creds(monkeypatch):
    """Only the intl Token Plan resolves. Token Plan speaks chat/completions natively,
    so pin the endpoint to match (the plugin default is /images/generations)."""
    def fake(requested=None, **kwargs):
        if requested == "alibaba-token-plan":
            return fake_runtime("sk-tp", TP_INTL, provider="alibaba-token-plan")
        raise RuntimeError(f"no credentials for {requested}")

    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", fake)
    monkeypatch.setenv("ALIBABA_IMAGE_ENDPOINT", "/chat/completions")


@pytest.fixture
def resolver_plans(monkeypatch):
    """Scriptable resolver: plans dict maps profile -> runtime | exception; records probe order.

    Pins ALIBABA_IMAGE_ENDPOINT=/chat/completions so named-plan tests exercise the
    Token Plan native payload shape (the plugin default is /images/generations)."""
    calls = []
    box = {"calls": calls, "plans": {}}

    def fake(requested=None, **kwargs):
        calls.append(requested)
        outcome = box["plans"].get(requested)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is None:
            raise RuntimeError(f"no credentials for {requested}")
        return outcome

    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", fake)
    monkeypatch.setenv("ALIBABA_IMAGE_ENDPOINT", "/chat/completions")
    return box


@pytest.fixture
def posted(monkeypatch):
    """Capture post_json calls; each call pops the next queued (body, failure) pair."""
    import alibaba

    calls = []
    box = {"queue": [], "calls": calls}

    def fake_post(url, *, headers, payload, timeout, label, **kwargs):
        calls.append({"url": url, "headers": headers, "payload": payload, "label": label})
        return box["queue"].pop(0)

    monkeypatch.setattr(alibaba, "post_json", fake_post)
    return box


@pytest.fixture
def saved(monkeypatch):
    """Intercept image materialization; record how it was called."""
    import alibaba

    box = {}

    def fake_materialize(b64, url, **kwargs):
        box["b64"] = b64
        box["url"] = url
        box.update(kwargs)
        return "/tmp/cached-alibaba.png", None

    monkeypatch.setattr(alibaba, "materialize_image", fake_materialize)
    return box
