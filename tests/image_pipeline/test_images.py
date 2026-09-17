"""Header-dimension reader: the truth the adapters report as width/height."""

from __future__ import annotations

import struct

from imagegen.images import read_image_size
from image_pipeline.helpers import make_png


def test_png():
    assert read_image_size(make_png(1920, 1080)) == (1920, 1080)


def test_png_path(tmp_path):
    p = tmp_path / "x.png"
    p.write_bytes(make_png(640, 480))
    assert read_image_size(str(p)) == (640, 480)


def test_gif():
    data = b"GIF89a" + struct.pack("<HH", 12, 34) + b"\x00" * 10
    assert read_image_size(data) == (12, 34)


def test_jpeg_sof0():
    w, h = 1600, 900
    seg = (b"\xff\xc0" + struct.pack(">HBBBBB", 17, 8, h >> 8, h & 0xFF, w >> 8, w & 0xFF)
           + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01")
    data = b"\xff\xd8" + seg + b"\xff\xd9"
    assert read_image_size(data) == (w, h)


def test_webp_vp8x():
    dims = (1024).to_bytes(3, "little") + (768).to_bytes(3, "little")
    data = b"RIFF\x00\x00\x00\x00WEBPVP8X\x0a\x00\x00\x00" + b"\x00" * 4 + dims + b"\x00" * 6
    assert read_image_size(data) == (1025, 769)


def test_unknown_returns_none():
    assert read_image_size(b"not-an-image-at-all") is None
    assert read_image_size("/nonexistent/file.png") is None
