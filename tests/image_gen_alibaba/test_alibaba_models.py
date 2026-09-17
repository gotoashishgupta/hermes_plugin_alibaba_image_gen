"""Model catalog and the model-resolution precedence chain."""

from __future__ import annotations

import pytest

from alibaba import AlibabaImageGenProvider


def make_provider():
    return AlibabaImageGenProvider()


@pytest.fixture
def cfg(monkeypatch):
    """Patch what load_image_gen_config() sees."""
    box = {"image_gen": {}}

    def fake_load_config(*a, **k):
        return dict(box)

    monkeypatch.setattr("hermes_cli.config.load_config", fake_load_config)
    return box["image_gen"]


def test_catalog_lists_both_wan_models():
    ids = [row["id"] for row in make_provider().list_models()]
    assert ids == ["wan2.7-image", "wan2.7-image-pro"]


def test_default_model_is_wan_image():
    assert make_provider().default_model() == "wan2.7-image"


def test_explicit_kwarg_wins(cfg, monkeypatch):
    monkeypatch.setenv("ALIBABA_IMAGE_MODEL", "wan2.7-image-pro")
    cfg["model"] = "wan2.7-image-pro"
    assert make_provider()._resolve_model("wan2.7-image") == "wan2.7-image"


def test_env_beats_config(cfg, monkeypatch):
    monkeypatch.setenv("ALIBABA_IMAGE_MODEL", "wan2.7-image-pro")
    cfg["alibaba"] = {"model": "wan2.7-image"}
    assert make_provider()._resolve_model(None) == "wan2.7-image-pro"


def test_scoped_config_beats_top_level(cfg):
    cfg["model"] = "wan2.7-image-pro"
    cfg["alibaba"] = {"model": "wan2.7-image"}
    assert make_provider()._resolve_model(None) == "wan2.7-image"


def test_top_level_config_used_when_scoped_absent(cfg):
    cfg["model"] = "wan2.7-image-pro"
    assert make_provider()._resolve_model(None) == "wan2.7-image-pro"


def test_default_when_nothing_configured(cfg):
    assert make_provider()._resolve_model(None) == "wan2.7-image"


def test_unknown_explicit_model_passes_through(cfg, monkeypatch):
    # PAYG endpoints may serve model ids beyond the verified wan catalog — never drop a
    # model the caller explicitly asked for.
    assert make_provider()._resolve_model("qwen-image-2.0") == "qwen-image-2.0"


def test_unknown_env_model_passes_through(cfg, monkeypatch):
    monkeypatch.setenv("ALIBABA_IMAGE_MODEL", "some-future-wan")
    assert make_provider()._resolve_model(None) == "some-future-wan"
