"""hermes-image-pipeline — registers the bundled SKILL.md as a plugin skill.

Loadable in Hermes as ``hermes-image-pipeline:image-pipeline`` (explicit loads,
like every plugin skill). The driver script lives at ``scripts/imagegen.py``
next to this file and is a byte-identical copy of the generic skill's scripts
(enforced by tests/image_pipeline/test_leaf_parity.py — leaves install
standalone, so no import sharing is possible).
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_SKILL_MD = _ROOT / "SKILL.md"
_SKILL_NAME = "image-pipeline"

__all__ = ["register"]


def _frontmatter_description(md: Path) -> str:
    """One-pass scan for the description field (avoid a yaml dep at import time)."""
    try:
        text = md.read_text(encoding="utf-8")
    except OSError:
        return ""
    if not text.lstrip("﻿").startswith("---"):
        return ""
    body = text.lstrip("﻿").split("---", 2)
    for line in (body[1].splitlines() if len(body) >= 3 else []):
        if line.startswith("description:"):
            return line.split(":", 1)[1].strip()
    return ""


def register(ctx) -> None:
    """Plugin entry point — called once at load time by the Hermes plugin loader."""
    ctx.register_skill(_SKILL_NAME, _SKILL_MD,
                       description=_frontmatter_description(_SKILL_MD))
