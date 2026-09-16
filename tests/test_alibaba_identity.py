"""Provider identity: registration contract + picker metadata."""

from __future__ import annotations

from hermes_plugin_alibaba_image_gen.alibaba import AlibabaImageGenProvider


def make_provider():
    return AlibabaImageGenProvider()


def test_provider_name_is_alibaba():
    # image_gen.provider: alibaba  /  hg_image --provider alibaba  must match.
    assert make_provider().name == "alibaba"


def test_display_name_is_unified_label():
    assert make_provider().display_name == "Alibaba (unified)"


def test_capabilities_advertise_reference_images():
    # The dynamic tool schema + hg_image's --ref narrowing read this.
    caps = make_provider().capabilities()
    assert caps["modalities"] == ["text", "image"]
    assert caps["max_reference_images"] == 4


def test_setup_schema_prompts_token_plan_key():
    # `hermes tools` prompts every env var declared here; hg_image gates availability on them.
    schema = make_provider().get_setup_schema()
    assert schema["name"] == "Alibaba (unified)"
    keys = [entry["key"] for entry in schema["env_vars"]]
    assert keys == ["ALIBABA_TOKEN_PLAN_API_KEY"]
    # The tag must tell PAYG users their DASHSCOPE_API_KEY works too.
    assert "DASHSCOPE_API_KEY" in schema["tag"]
