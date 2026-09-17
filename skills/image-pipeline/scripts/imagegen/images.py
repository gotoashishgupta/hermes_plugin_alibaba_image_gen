"""Read a raster's real (width, height) from file headers — stdlib only.

Adapters MEASURE every produced image with this instead of trusting provider
metadata (Gemini's ``imageConfig`` is known to be silently ignored by some
endpoints in 2026; fal/openai report what they intended, not always what they
sent). Returns ``None`` when the format is unrecognized — never guesses.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Optional, Tuple

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def read_image_size(source) -> Optional[Tuple[int, int]]:
    """``source``: path/str or bytes. → (width, height) or None."""
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    else:
        try:
            data = Path(source).read_bytes()
        except OSError:
            return None
    if data.startswith(PNG_SIG) and len(data) >= 24:
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        if len(data) >= 10:
            w, h = struct.unpack("<HH", data[6:10])
            return int(w), int(h)
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP" and len(data) >= 30:
        return _webp_size(data)
    if data[:2] == b"\xff\xd8":
        return _jpeg_size(data)
    return None


def _webp_size(data: bytes) -> Optional[Tuple[int, int]]:
    fourcc = data[12:16]
    if fourcc == b"VP8 ":
        dims = data[26:30]
        w = struct.unpack("<H", dims[0:2])[0] & 0x3FFF
        h = struct.unpack("<H", dims[2:4])[0] & 0x3FFF
        return int(w), int(h)
    if fourcc == b"VP8L":
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if fourcc == b"VP8X":
        w = int.from_bytes(data[24:27], "little") + 1
        h = int.from_bytes(data[27:30], "little") + 1
        return w, h
    return None


def _jpeg_size(data: bytes) -> Optional[Tuple[int, int]]:
    i, n = 2, len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker == 0xFF:
            i += 1
            continue
        seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
        # SOF0-SOF15 except DHT(C4)/JPGA(CC)
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return int(w), int(h)
        i += 2 + seg_len
    return None
