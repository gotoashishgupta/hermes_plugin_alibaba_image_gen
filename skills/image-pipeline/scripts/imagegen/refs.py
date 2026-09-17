"""Reference-image handling for standalone adapters: inline local files as base64
data URIs under a byte cap, recompressing with PIL *if available*.

Ported from ``plugins/image_gen_alibaba/alibaba.py`` (``_image_part`` /
``_recompress_for_inline``) with the same honesty rule: an unreadable reference
never gets silently dropped — the caller must not "fake brand fidelity with a
text-to-image render". When PIL is absent and a file is oversized, we inline raw
and let the API's 413 surface loudly rather than silently dropping the reference.
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Optional, Tuple

MAX_INLINE_IMAGE_BYTES = 3 * 1024 * 1024


def to_data_uri(src: str, *, cap: int = MAX_INLINE_IMAGE_BYTES) -> Tuple[Optional[str], Optional[str]]:
    """URLs/data URIs pass through; local files become data URIs (recompressed when
    oversized and PIL is present). → ``(uri, None)`` or ``(None, error_message)``."""
    s = str(src).strip()
    if s.startswith(("http://", "https://", "data:")):
        return s, None
    path = Path(s).expanduser()
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, f"reference image {s!r} could not be read: {exc}"
    mime = mimetypes.guess_type(s)[0] or "image/png"
    if len(data) > cap:
        shrunk = _recompress(data, cap)
        if shrunk is not None:
            data, mime = shrunk
    encoded = base64.b64encode(data).decode()
    return f"data:{mime};base64,{encoded}", None


def _recompress(data: bytes, cap: int) -> Optional[Tuple[bytes, str]]:
    """Downscale + JPEG re-encode until under ``cap``; None when PIL is missing or
    the bytes are undecodable (caller then inlines raw — loud failure > silent drop)."""
    try:
        from PIL import Image, ImageOps  # optional dependency, deliberately late import
        import io
    except ImportError:
        return None
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        quality = 85
        buf = io.BytesIO()
        for _ in range(4):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            if buf.tell() <= cap or quality <= 40:
                return buf.getvalue(), "image/jpeg"
            quality -= 15
            longest = max(img.size)
            scale = 2048 / longest if longest > 2048 else 0.75
            img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
        return buf.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001
        return None
