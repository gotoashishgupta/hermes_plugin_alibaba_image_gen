"""Shared setup for the image_pipeline suite (fixtures only; helpers live in
``image_pipeline/helpers.py`` — a second flat conftest would shadow
tests/conftest.py for the sibling suites, see __init__.py).

Proves the STANDALONE path with no Hermes anywhere: credential envs purged,
HERMES_* invisible, and the import-time DEFAULT_HERMES_* constants patched so a
developer's real ~/.hermes can never leak in. All network is faked per-adapter
module (adapters bind request_json/download into their own namespaces at import).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parents[1]
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

_ROOT = _TESTS.parent
_SCRIPTS = _ROOT / "skills" / "image-pipeline" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from image_pipeline.helpers import PNG_1PX  # noqa: E402

CREDENTIAL_ENVS = (
    "ALIBABA_TOKEN_PLAN_API_KEY", "ALIBABA_TOKEN_PLAN_CN_API_KEY", "ALIBABA_API_KEY",
    "ALIBABA_BASE_URL", "DASHSCOPE_API_KEY", "DASHSCOPE_CN_BASE_URL",
    "ALIBABA_TOKEN_PLAN_BASE_URL", "ALIBABA_TOKEN_PLAN_CN_BASE_URL",
    "ALIBABA_IMAGE_MODEL", "ALIBABA_IMAGE_PLAN", "ALIBABA_IMAGE_ENDPOINT",
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_IMAGE_MODEL",
    "OPENROUTER_API_KEY", "OPENROUTER_IMAGE_MODEL", "OPENROUTER_BASE_URL",
    "FAL_KEY", "FAL_IMAGE_MODEL", "GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_IMAGE_MODEL",
    "HERMES_AGENT_REPO", "HERMES_VENV_PY", "HERMES_HOME", "HG_IMAGE_NO_REEXEC",
    "HG_IMAGE_CACHE",
)


@pytest.fixture(autouse=True)
def clean_env(tmp_path, monkeypatch):
    """Credential-less, config-less, and Hermes-invisible: the standalone contract."""
    for var in CREDENTIAL_ENVS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HG_IMAGE_CACHE", str(tmp_path / "hg-cache"))
    from imagegen import hermes_mode
    monkeypatch.setattr(hermes_mode, "DEFAULT_HERMES_REPO", tmp_path / ".hermes" / "hermes-agent")
    monkeypatch.setattr(hermes_mode, "DEFAULT_HERMES_HOME", tmp_path / ".hermes")


@pytest.fixture
def net(monkeypatch):
    """Scriptable request_json + download for every adapter module.

    net["queue"]: list of (json_body, None) / (None, HttpFailure) popped per call.
    net["calls"]: {method,url,headers,payload,raw_body,label} per call.
    net["downloads"]: URLs passed to download(); files materialize as a 1px PNG.
    """
    from imagegen import alibaba, fal, gemini, openai_compat, openrouter
    box = {"queue": [], "calls": [], "downloads": []}

    def fake_request(method, url, *, headers, payload=None, raw_body=None,
                     content_type=None, timeout=None, label=""):
        box["calls"].append({"method": method, "url": url, "headers": dict(headers),
                             "payload": payload, "raw_body": raw_body, "label": label})
        if not box["queue"]:
            raise AssertionError(f"unscripted request: {method} {url}")
        return box["queue"].pop(0)

    def fake_download(url, dest, **kwargs):
        box["downloads"].append(url)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(PNG_1PX)
        return dest

    for mod in (alibaba, openai_compat, openrouter, fal, gemini):
        monkeypatch.setattr(mod, "request_json", fake_request)
        if hasattr(mod, "download"):  # gemini is b64-only; it never downloads
            monkeypatch.setattr(mod, "download", fake_download)
    return box
