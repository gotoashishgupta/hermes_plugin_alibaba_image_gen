"""Back-compat parity + the honesty rules the skill depends on:
payload keys, fallback truthfulness, attempts[], --out copy, text mode shape."""

from __future__ import annotations

import json
from pathlib import Path

from imagegen import cli
from image_pipeline.helpers import http_failure, openai_b64_body, PNG_1PX


def run(argv):
    return cli.main(["hg_image.py", *argv])


def test_generate_json_payload_legacy_keys(monkeypatch, net, capsys, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    net["queue"].append((openai_b64_body(), None))
    out = tmp_path / "attempt-1.png"
    rc = run(["generate", "--prompt", "a red fox", "--provider", "openai",
              "--mode", "standalone", "--out", str(out), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    for key in ("success", "provider", "model", "prompt", "aspect_ratio", "image",
                "references"):
        assert key in payload, key
    assert payload["provider"] == "openai" and payload["success"] is True
    assert payload["image"] == str(out.resolve()) and out.read_bytes() == PNG_1PX
    assert payload["route"] == "standalone" and payload["credential_source"] == "env"


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


def test_list_json_shape_back_compat_and_modes(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    rc = run(["list", "--mode", "standalone", "--json"])
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    assert set(doc) >= {"providers", "excluded", "modes"}
    row = next(r for r in doc["providers"] if r["provider"] == "openai")
    assert set(row) == {"provider", "kind", "available", "key_env", "default_model", "models"}
    assert row["available"] is True and row["key_env"] == "OPENAI_API_KEY"
    assert "hermes" in doc["modes"] and doc["modes"]["standalone_env_credentialed"] == ["openai"]


def test_legacy_base_url_shim_targets_token_plan(monkeypatch, net, capsys):
    monkeypatch.setenv("ALIBABA_TOKEN_PLAN_API_KEY", "tp")
    rc = run(["generate", "--prompt", "x", "--base-url", "https://tp.internal/v1",
              "--mode", "standalone", "--json"])
    import os
    assert os.environ.get("ALIBABA_TOKEN_PLAN_BASE_URL") == "https://tp.internal/v1"
    err = capsys.readouterr().err
    assert rc == 1 and "--base-url without --api-key" in err  # shim announced; no net scripted


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


def test_size_flag_reports_snap_trail(monkeypatch, net, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    net["queue"].append((openai_b64_body(), None))
    rc = run(["generate", "--prompt", "x", "--provider", "openai", "--size", "1920x1080",
              "--mode", "standalone", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["size_requested"] == "1920x1080"
    assert payload["aspect_ratio"] == "landscape" and "snapped" in payload["note"]
