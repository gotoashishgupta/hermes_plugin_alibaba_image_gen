"""The request/result envelope — one shape for every route (Hermes registry,
standalone adapters, direct credentials). Modes are indistinguishable to the
agent: same fields, same error taxonomy.

``to_payload()`` is the contract documented in docs/image-pipeline.md:
``success, provider, model, prompt, aspect, route, credential_source, image,
references`` + optional ``plan, notes[], seed, width, height, size_requested,
attempts[]`` + on failure ``error, error_type``. ``provider`` and ``model``
always name what ACTUALLY produced the image — the skill's core honesty rule —
and ``width``/``height`` are measured from the delivered file, never trusted
from provider metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

# Stable error_type strings — mirror the Hermes image_gen taxonomy exactly so a
# provider error reads the same whoever served the call.
ERROR_TYPES = frozenset({
    "invalid_input", "missing_api_key", "auth_error", "model_access", "api_error",
    "timeout", "connection_error", "invalid_response", "empty_response",
    "modality_unsupported", "io_error", "provider_exception",
})

ASPECTS = ("landscape", "square", "portrait")


class UsageError(Exception):
    """Bad invocation or no usable provider — CLI maps to exit code 2."""


def parse_size(value: str) -> Tuple[int, int]:
    """'1920x1080' → (1920, 1080). Raises ValueError (CLI → UsageError)."""
    s = (value or "").strip().lower().replace("×", "x")
    parts = s.split("x")
    if len(parts) != 2:
        raise ValueError(f"invalid --size {value!r}, expected WxH (e.g. 1920x1080)")
    try:
        w, h = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(f"invalid --size {value!r}, expected WxH with integers")
    if not (16 <= w <= 8192 and 16 <= h <= 8192):
        raise ValueError(f"--size {w}x{h} out of range (16..8192 per side)")
    return w, h


@dataclass(frozen=True)
class GenRequest:
    prompt: str
    aspect: str = "landscape"
    size: Optional[Tuple[int, int]] = None   # --size WxH; wins over aspect; adapters snap
    model: Optional[str] = None
    references: Tuple[str, ...] = ()
    api_key: Optional[str] = None            # CLI override for THIS call; never persisted
    base_url: Optional[str] = None
    endpoint: Optional[str] = None           # path override ("/chat/completions", "/images/generations", …)
    timeout: float = 300.0


@dataclass
class GenResult:
    success: bool
    provider: str = ""
    model: str = ""
    route: str = ""                # "hermes-registry" | "standalone" | "direct"
    credential_source: str = ""    # "hermes-ladder" | "env" | "cli"
    image: Optional[str] = None    # absolute local path
    prompt: str = ""
    references: Tuple[str, ...] = ()
    aspect: str = "landscape"
    width: Optional[int] = None    # measured from the produced file (images.py)
    height: Optional[int] = None
    plan: Optional[str] = None     # plan-aware providers (alibaba) report which login answered
    notes: list = field(default_factory=list)   # honest footnotes: snaps, skips, ladder trails
    seed: Optional[int] = None
    size_requested: Optional[str] = None        # original --size string ("1920x1080")
    attempts: list = field(default_factory=list)  # [{provider,model,error,error_type}] pre-winner
    error: Optional[str] = None
    error_type: Optional[str] = None

    def add_note(self, text: str) -> "GenResult":
        if text:
            self.notes.append(text)
        return self

    def to_payload(self) -> dict:
        payload: dict = {
            "success": self.success,
            "provider": self.provider,
            "model": self.model,
            "prompt": self.prompt,
            "aspect": self.aspect,
            "route": self.route,
            "credential_source": self.credential_source,
            "image": self.image,
            "references": list(self.references),
        }
        if self.plan is not None:
            payload["plan"] = self.plan
        if self.notes:
            payload["notes"] = self.notes
        for key in ("seed", "width", "height", "size_requested"):
            if getattr(self, key) is not None:
                payload[key] = getattr(self, key)
        if self.attempts:
            payload["attempts"] = self.attempts
        if not self.success:
            payload["error"] = self.error
            payload["error_type"] = self.error_type
        return payload
