"""CLI contract: payload shape, fallback truthfulness, attempts[], --out copy,
list shape, --base-url/--endpoint provider-pinning rule."""

from __future__ import annotations

import json

from imagegen import cli
from image_pipeline.helpers import http_failure, openai_b64_body, PNG_1PX


def run(argv):
    return cli.main(["imagegen.py", *argv])


def test_generate_json_payload_contract(monkeypatch, net, capsys, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    net["queue"].append((openai_b64_body(), None))
    out = tmp_path / "attempt-1.png"
    rc = run(["generate", "--prompt", "a red fox", "--provider", "openai",
              "--mode", "standalone", "--out", str(out), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is True and payload["provider"] == "openai"
    assert payload["prompt"] == "a red fox" and payload["aspect"] == "landscape"
    assert payload["references"] == [] and payload["route"] == "standalone"
    assert payload["credential_source"] == "env"
    assert payload["image"] == str(out.resolve()) and out.read_bytes() == PNG_1PX
    assert payload["width"] == 1 and payload["height"] == 1  # measured


def test_text_mode_prints_only_the_path(monkeypatch, net, capsys):
    monkeypatch.setenv("FAL_KEY", "fal-k")
    monkeypatch.setattr("imagegen.fal.time.sleep", lambda s: None)
    from image_pipeline.helpers import fal_queue_submit, fal_result
    net["queue"] = [(fal_queue_submit(), None), ({"status": "COMPLETED"}, None),
                    (fal_result(), None)]
    rc = run(["generate", "--prompt", "x", "--provider", "fal", "--mode", "standalone"])
    assert rc == 0
    printed = capsys.readouterr().out.strip()
    assert printed.endswith(".png") and "\n" not in printed


def test_fallback_payload_names_the_actual_producer(monkeypatch, net, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    monkeypatch.setenv("FAL_KEY", "fal-k")
    monkeypatch.setattr("imagegen.fal.time.sleep", lambda s: None)
    from image_pipeline.helpers import fal_queue_submit, fal_result
    net["queue"] = [
        (None, http_failure("http", status=503, message="upstream down")),   # openai fails
        (fal_queue_submit(), None), ({"status": "COMPLETED"}, None),           # fal wins
        (fal_result(), None),
    ]
    rc = run(["generate", "--prompt", "x", "--mode", "standalone", "--fallback", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["provider"] == "fal"                      # THE truth rule
    assert payload["attempts"][0]["provider"] == "openai"    # what failed first
    assert payload["attempts"][0]["error_type"] == "api_error"


def test_no_fallback_single_attempt(monkeypatch, net, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    monkeypatch.setenv("FAL_KEY", "fal-k")
    net["queue"].append((None, http_failure("connection")))
    rc = run(["generate", "--prompt", "x", "--mode", "standalone", "--json"])
    assert rc == 1
    assert len(net["calls"]) == 1  # no silent extra spend


def test_list_json_shape(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    rc = run(["list", "--mode", "standalone", "--json"])
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    assert set(doc) == {"providers", "modes"}
    row = next(r for r in doc["providers"] if r["provider"] == "openai")
    assert set(row) == {"provider", "kind", "available", "key_envs", "default_model", "models"}
    assert row["available"] is True and row["key_envs"] == ["OPENAI_API_KEY"]
    assert "hermes" in doc["modes"] and doc["modes"]["standalone_env_credentialed"] == ["openai"]


def test_bare_base_url_requires_provider(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    rc = run(["generate", "--prompt", "x", "--base-url", "https://gw.test/v1",
              "--mode", "standalone", "--json"])
    assert rc == 2
    assert "require --provider" in capsys.readouterr().err


def test_bare_endpoint_requires_provider(capsys):
    rc = run(["generate", "--prompt", "x", "--endpoint", "/chat/completions",
              "--mode", "standalone", "--json"])
    assert rc == 2


def test_endpoint_flag_reaches_named_provider(monkeypatch, net, capsys):
    monkeypatch.setenv("ALIBABA_TOKEN_PLAN_API_KEY", "tp")
    net["queue"].append(({"data": [{"url": "https://x/y.png"}]}, None))
    rc = run(["generate", "--prompt", "x", "--provider", "alibaba",
              "--endpoint", "/images/generations", "--mode", "standalone", "--json"])
    assert rc == 0
    assert net["calls"][0]["url"] == \
        "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/images/generations"
    payload = json.loads(capsys.readouterr().out)
    assert payload["provider"] == "alibaba" and payload["plan"] == "alibaba-token-plan"


def test_base_url_pins_named_provider(monkeypatch, net):
    """--provider openai --base-url U: the URL targets THAT backend, no env rewriting."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    net["queue"].append((openai_b64_body(), None))
    rc = run(["generate", "--prompt", "x", "--provider", "openai",
              "--base-url", "https://gw.internal/v1", "--mode", "standalone", "--json"])
    assert rc == 0
    assert net["calls"][0]["url"] == "https://gw.internal/v1/images/generations"
    import os
    assert "GW.INTERNAL" not in os.environ.get("ALIBABA_TOKEN_PLAN_BASE_URL", "")
    assert os.environ.get("ALIBABA_TOKEN_PLAN_BASE_URL") is None


def test_size_flag_reports_snap_trail(monkeypatch, net, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    net["queue"].append((openai_b64_body(), None))
    rc = run(["generate", "--prompt", "x", "--provider", "openai", "--size", "1920x1080",
              "--mode", "standalone", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["size_requested"] == "1920x1080"
    assert payload["aspect"] == "landscape"
    assert any("snapped" in n for n in payload["notes"])
