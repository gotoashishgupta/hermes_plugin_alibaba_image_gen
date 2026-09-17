"""The exit-code bug: the old docstring promised 2 for "no usable provider" but
SystemExit(str) emitted 1. All UsageError sites must now exit 2; generation
failures stay 1; success 0.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from imagegen import cli


def run(argv):
    return cli.main(["hg_image.py", *argv])


def test_no_credentialed_provider_exits_2():
    # clean_env purged every key; hermes invisible (conftest patches DEFAULT_HERMES_REPO)
    rc = run(["generate", "--prompt", "a red fox", "--mode", "standalone"])
    assert rc == 2


def test_mode_hermes_without_repo_exits_2():
    rc = run(["generate", "--prompt", "a red fox", "--mode", "hermes"])
    assert rc == 2


def test_api_key_without_provider_exits_2():
    rc = run(["generate", "--prompt", "x", "--api-key", "sk-test", "--mode", "standalone"])
    assert rc == 2


def test_bad_size_exits_2():
    rc = run(["generate", "--prompt", "x", "--size", "wide", "--mode", "standalone"])
    assert rc == 2


def test_missing_reference_exits_2():
    rc = run(["generate", "--prompt", "x", "--ref", "/nope/missing.png", "--mode", "standalone"])
    assert rc == 2


def test_unpinned_unknown_provider_exits_2():
    rc = run(["generate", "--prompt", "x", "--provider", "acme-gpt", "--api-key", "k",
              "--mode", "standalone"])
    assert rc == 2  # custom provider requires --base-url


def test_requested_provider_not_credentialed_exits_2(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "k")
    rc = run(["generate", "--prompt", "x", "--provider", "openai", "--mode", "standalone"])
    assert rc == 2


def test_generation_failure_exits_1_with_envelope(monkeypatch, net, capsys):
    monkeypatch.setenv("FAL_KEY", "fal-k")
    from image_pipeline.helpers import http_failure
    net["queue"].append((None, http_failure("connection", label="FAL")))
    rc = run(["generate", "--prompt", "a red fox", "--provider", "fal",
              "--mode", "standalone", "--json"])
    assert rc == 1
    captured = capsys.readouterr()
    # stderr carries the [fallback] line (old behavior) followed by the envelope JSON
    err = json.loads(captured.err[captured.err.index("{"):])
    assert err["success"] is False and err["provider"] == "fal"
    assert err["error_type"] == "connection_error"
