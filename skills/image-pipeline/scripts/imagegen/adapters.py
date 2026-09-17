"""``ProviderAdapter`` — the standalone twin of Hermes' ``ImageGenProvider`` contract,
plus helpers shared by every adapter: size snapping, tolerant response-image
extraction (ported verbatim in behavior from ``plugins/image_gen_alibaba/alibaba.py``
``_extract_image`` — see CHANGE NOTE there for the two implementations' drift contract),
and image materialization into a local cache.

Adapters MUST NOT raise out of ``generate`` — every plumbing bug surfaces as a
``GenResult(success=False, error_type=...)`` the agent can explain (same ABC
discipline as the Hermes providers).
"""

from __future__ import annotations

import abc
import base64
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .envelope import GenRequest, GenResult
from .images import read_image_size

# Semantic aspect → OpenAI-style size string, shared by every OpenAI-compatible
# surface (= _common.OPENAI_SIZES; gpt-image models accept ONLY these three).
OPENAI_SIZES: Dict[str, str] = {
    "landscape": "1536x1024", "square": "1024x1024", "portrait": "1024x1536",
}


def cache_dir() -> Path:
    """Where adapters materialize images. Override with IMAGEGEN_CACHE."""
    d = Path(os.environ.get("IMAGEGEN_CACHE") or (Path.home() / ".cache" / "image-pipeline"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_b64(b64: str, prefix: str, ext: str = "png") -> Path:
    import time
    path = cache_dir() / f"{prefix}-{int(time.time() * 1000) % 10**13}.{ext}"
    path.write_bytes(base64.b64decode(b64))
    return path


def ratio_of(pair) -> float:
    """(w, h) tuple or "W:H" string → float ratio."""
    if isinstance(pair, str):
        w, _, h = pair.partition(":")
        return float(w) / float(h)
    return pair[0] / pair[1]


def nearest_by_ratio(w: int, h: int, candidates: List[Tuple[str, float]]) -> str:
    """Pick the candidate key whose ratio is closest in log space (16:9 and 9:16 are
    equidistant from 1:1 but distinct — log distance keeps portrait/landscape honest)."""
    import math
    target = math.log(w / h)
    return min(candidates, key=lambda kv: abs(math.log(kv[1]) - target))[0]


def final_aspect(req: GenRequest) -> str:
    """Semantic aspect to report: the explicit one, or derived from --size shape."""
    if req.size:
        w, h = req.size
        if abs(w - h) / max(w, h) <= 0.15:
            return "square"
        return "landscape" if w >= h else "portrait"
    return req.aspect


def measure_or_meta(result: GenResult) -> GenResult:
    """Set width/height by MEASURING the file; add a note when a requested exact
    size deviates >5%. Never trusts provider metadata over pixels."""
    if result.image:
        try:
            dims = read_image_size(result.image)
        except Exception:  # noqa: BLE001
            dims = None
        if dims:
            result.width, result.height = dims
    if result.size_requested and result.width and result.height:
        try:
            rw, rh = (int(x) for x in result.size_requested.lower().split("x"))
            if (result.width, result.height) != (rw, rh):
                dev = abs(result.width / result.height - rw / rh) / (rw / rh)
                tail = "ratio deviates >5%" if dev > 0.05 else "snapped (ratio kept, pixels differ)"
                result.add_note(f"requested {rw}x{rh}, delivered "
                                f"{result.width}x{result.height}: {tail}")
        except (ValueError, AttributeError):
            pass
    return result


def extract_image(body: Any) -> Tuple[Optional[str], Optional[str]]:
    """First ``(url, b64)`` image across the shapes OpenAI-compat, chat-completions
    image servers, Token Plan, and gateway wrappers return. Port of alibaba.py's
    ``_extract_image`` (data[]/b64_json, output.choices, choices, message.content
    image parts, message.images). No image → (None, None)."""
    if not isinstance(body, dict):
        return None, None

    def _from_value(value: Any) -> Tuple[Optional[str], Optional[str]]:
        if isinstance(value, str) and value.strip():
            v = value.strip()
            if v.startswith("data:"):
                _, _, payload = v.partition(",")
                return None, payload
            return v, None
        return None, None

    for entry in body.get("data") or []:
        if not isinstance(entry, dict):
            continue
        url, b64 = _from_value(entry.get("url"))
        if url:
            return url, None
        if isinstance(entry.get("b64_json"), str) and entry["b64_json"].strip():
            return None, entry["b64_json"].strip()
    choices = (body.get("output") or {}).get("choices") if isinstance(body.get("output"), dict) else None
    if not choices:
        choices = body.get("choices")
    for choice in choices or []:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message") or {}
        parts = message.get("content") if isinstance(message, dict) else None
        if not isinstance(parts, str):
            for part in parts or []:
                if not (isinstance(part, dict) and part.get("type") == "image"):
                    continue
                url, b64 = _from_value(part.get("image"))
                if url or b64:
                    return url, b64
        for img in (message.get("images") if isinstance(message, dict) else None) or []:
            if isinstance(img, dict):
                image = (img.get("image_url") or {}).get("url") if isinstance(img.get("image_url"), dict) \
                    else img.get("image_url") or img.get("image") or img.get("url")
            else:
                image = img
            url, b64 = _from_value(image)
            if url or b64:
                return url, b64
    return None, None


class ProviderAdapter(abc.ABC):
    """One standalone provider. ``name`` matches the Hermes registry id where one
    exists, so switching routes never changes the agent's vocabulary."""

    name: str = ""
    display: str = ""
    priority: int = 100                      # lower = preferred in auto selection
    key_envs: Tuple[str, ...] = ()           # env scan for availability + `list` key_envs
    supports_endpoint: bool = False
    max_reference_images: int = 0

    def __init__(self, *, instance_name: Optional[str] = None):
        if instance_name:
            self.name = instance_name

    # -- availability ladder ------------------------------------------------
    def env_key(self) -> Optional[str]:
        """First non-blank declared env var's value."""
        for var in self.key_envs:
            value = (os.environ.get(var) or "").strip()
            if value:
                return value
        return None

    def available(self, req: Optional[GenRequest] = None) -> bool:
        if req and req.api_key:
            return True
        return bool(self.env_key())

    def key(self, req: Optional[GenRequest] = None) -> str:
        """Effective API key: CLI override (never persisted) else env ladder."""
        if req and req.api_key:
            return req.api_key.strip()
        return self.env_key() or ""

    def credential_hint(self) -> str:
        return f"{self.name} accepts: {', '.join(self.key_envs)}" if self.key_envs else self.name

    def default_model(self) -> Optional[str]:
        return None

    def models(self) -> List[str]:
        return []

    def supports_references(self, model: Optional[str] = None) -> bool:
        return self.max_reference_images > 0

    # -- generation ---------------------------------------------------------
    @abc.abstractmethod
    def generate(self, req: GenRequest) -> GenResult:
        ...

    def _fail(self, req: GenRequest, error: str, error_type: str) -> GenResult:
        return GenResult(
            success=False, provider=self.name,
            model=req.model or self.default_model() or "",
            prompt=req.prompt, references=req.references, aspect=final_aspect(req),
            route="direct" if req.api_key else "standalone",
            credential_source="cli" if req.api_key else "env",
            size_requested=f"{req.size[0]}x{req.size[1]}" if req.size else None,
            error=error, error_type=error_type)

    def _guard(self, req: GenRequest, fn) -> GenResult:
        """Never raise out of generate(): wrap everything (ABC discipline)."""
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            return self._fail(req, f"{type(exc).__name__}: {exc}", "provider_exception")
