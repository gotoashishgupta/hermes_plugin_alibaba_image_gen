"""Reference images: content parts, clamping, refusal on unreadable inputs."""

from __future__ import annotations

import base64

from conftest import PNG_BYTES, token_plan_body
from hermes_plugin_image_gen_ext.alibaba import AlibabaImageGenProvider


def make_provider():
    return AlibabaImageGenProvider()


def png(tmp_path, name="ref.png"):
    p = tmp_path / name
    p.write_bytes(PNG_BYTES)
    return str(p)


def parts_of(call):
    return call["payload"]["messages"][0]["content"]


def test_local_reference_becomes_data_uri_part(tmp_path, tp_creds, posted, saved):
    posted["queue"] = [(token_plan_body(), None)]
    make_provider().generate("match this", reference_image_urls=[png(tmp_path)])
    parts = parts_of(posted["calls"][0])
    assert parts[0] == {"type": "text", "text": "match this"}
    assert parts[1]["type"] == "image"
    assert parts[1]["image"].startswith("data:image/png;base64,")
    assert base64.b64decode(parts[1]["image"].split(",", 1)[1]) == PNG_BYTES


def test_http_reference_passes_through(tp_creds, posted, saved):
    posted["queue"] = [(token_plan_body(), None)]
    make_provider().generate("x", reference_image_urls=["https://brand.example/look.jpg"])
    assert parts_of(posted["calls"][0])[1] == {
        "type": "image", "image": "https://brand.example/look.jpg"}


def test_image_url_primary_comes_first(tmp_path, tp_creds, posted, saved):
    posted["queue"] = [(token_plan_body(), None), (token_plan_body(), None)]
    make_provider().generate("x", image_url=png(tmp_path, "primary.png"),
                             reference_image_urls=["https://e/second.png"])
    parts = parts_of(posted["calls"][0])
    assert parts[1]["image"].startswith("data:") and parts[2]["image"] == "https://e/second.png"
    # Editing path is reported through modality.
    assert make_provider().generate("x", image_url="https://e/a.png")["modality"] == "image"


def test_more_than_four_references_are_clamped(tp_creds, posted, saved):
    posted["queue"] = [(token_plan_body(), None)] * 10
    refs = [f"https://e/{i}.png" for i in range(7)]
    make_provider().generate("x", reference_image_urls=refs)
    assert len(parts_of(posted["calls"][0])) == 1 + 4  # text + max_reference_images


def test_unreadable_reference_never_silently_bills_text_only(tmp_path, tp_creds, posted, saved):
    posted["queue"] = [(token_plan_body(), None)]
    result = make_provider().generate("x", reference_image_urls=["/nonexistent/look.png"])
    assert result["success"] is False
    assert result["error_type"] == "io_error"
    assert posted["calls"] == []  # a dropped reference would fake brand fidelity — refuse instead


def test_mixed_refs_keep_readable_and_note_the_skip(tp_creds, posted, saved):
    posted["queue"] = [(token_plan_body(), None)]
    result = make_provider().generate(
        "x", reference_image_urls=["https://e/ok.png", "/nonexistent/look.png"])
    assert result["success"] is True
    assert len(parts_of(posted["calls"][0])) == 2  # text + the one readable ref
    assert "skipped" in result["note"]


def test_ref_present_notes_that_size_is_ignored(tp_creds, posted, saved):
    posted["queue"] = [(token_plan_body(), None)]
    result = make_provider().generate("x", image_url="https://e/a.png", aspect_ratio="square")
    assert "ignores size" in result["note"]


# --- inline size capping (a 2048² PNG reference 413'd at the gateway: >~11MB data URI) ---

def big_png(tmp_path):
    """A genuinely large PNG: dense per-pixel noise is incompressible."""
    from PIL import Image

    img = Image.effect_noise((3000, 3000), 100).convert("RGB")
    p = tmp_path / "big.png"
    img.save(p)
    assert p.stat().st_size > 3 * 1024 * 1024
    return str(p)


def test_large_local_reference_is_recompressed_for_inline(tmp_path, tp_creds, posted, saved):
    posted["queue"] = [(token_plan_body(), None)]
    make_provider().generate("x", reference_image_urls=[big_png(tmp_path)])
    image = parts_of(posted["calls"][0])[1]["image"]
    assert image.startswith("data:image/jpeg;base64,")
    assert len(image) < 4 * 1024 * 1024 * 2  # well under the body that 413'd (~11MB)


def test_undecodable_large_file_falls_back_to_raw_inline(tmp_path, tp_creds, posted, saved):
    junk = tmp_path / "junk.png"
    junk.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * (4 * 1024 * 1024))  # undecodable, > cap
    posted["queue"] = [(token_plan_body(), None)]
    make_provider().generate("x", reference_image_urls=[str(junk)])
    image = parts_of(posted["calls"][0])[1]["image"]
    assert image.startswith("data:image/png;base64,")  # passed through; API error stays loud
