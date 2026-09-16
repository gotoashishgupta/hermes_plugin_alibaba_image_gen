"""Hermes image_gen provider extensions.

Distributed as a pip package; Hermes discovers it through the
``hermes_agent.plugins`` entry-point group and calls :func:`register` once at
plugin load time.

Currently ships one backend: ``alibaba`` — a unified Alibaba image provider
that picks the plan (Token Plan intl/CN, DashScope PAYG intl/CN) and base URL
from whichever API key resolves. See ``alibaba.py``.
"""

from __future__ import annotations

from .alibaba import AlibabaImageGenProvider

__all__ = ["AlibabaImageGenProvider", "register"]


def register(ctx) -> None:
    """Plugin entry point — called once at load time by the Hermes plugin loader."""
    ctx.register_image_gen_provider(AlibabaImageGenProvider())
