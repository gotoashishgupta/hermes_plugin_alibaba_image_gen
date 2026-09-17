"""Envelope: parse_size, back-compatible payload keys, measure-or-meta trail."""

from __future__ import annotations

from pathlib import Path

import pytest

from imagegen.envelope import GenRequest, GenResult, parse_size
from imagegen.adapters import final_aspect, measure_or_meta
from image_pipeline.helpers import make_png, PNG_1PX


def test_parse_size_forms():
    assert parse_size("1920x1080") == (1920, 1080)
    assert parse_size(" 1920×1080 ") == (1920, 1080)
    for bad in ("wide", "1920", "x", "12x", "0x0", "99999x99999"):
        with pytest.raises(ValueError):
            parse_size(bad)


def test_payload_keeps_every_legacy_key():
    r = GenResult(success=True, provider="fal", model="m", image="/tmp/x.png",
                  aspect_ratio="landscape", plan=None, note="n", seed=3,
                  width=2, height=1)
    payload = r.to_payload(prompt="p", references=["/tmp/ref.png"])
    for key in ("success", "provider", "model", "prompt", "aspect_ratio", "image",
                "references", "note", "seed", "width", "height"):
        assert key in payload, key
    assert payload["prompt"] == "p" and payload["references"] == ["/tmp/ref.png"]
    assert payload["route"] == "" and "plan" not in payload  # None keys omitted (old shape)


def test_final_aspect_from_size():
    assert final_aspect(GenRequest(prompt="", size=(1920, 1080))) == "landscape"
    assert final_aspect(GenRequest(prompt="", size=(1000, 1020))) == "square"
    assert final_aspect(GenRequest(prompt="", size=(700, 1400))) == "portrait"
    assert final_aspect(GenRequest(prompt="", aspect="square")) == "square"


def test_measure_overrides_and_notes(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(make_png(1024, 1024))
    r = GenResult(success=True, provider="x", image=str(p),
                  size_requested="1920x1080")
    out = measure_or_meta(r)
    assert (out.width, out.height) == (1024, 1024)  # measured, not requested
    assert out.note and "deviates >5%" in out.note


def test_measure_exact_match_no_deviation_note(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(make_png(512, 512))
    r = GenResult(success=True, provider="x", image=str(p), size_requested="512x512")
    out = measure_or_meta(r)
    assert out.note is None
    assert (out.width, out.height) == (512, 512)
