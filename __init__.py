from __future__ import annotations

from .alibaba import AlibabaImageGenProvider

__all__ = ["AlibabaImageGenProvider", "register"]


def register(ctx) -> None:
    """Plugin entry point — called once at load time by the Hermes plugin loader."""
    ctx.register_image_gen_provider(AlibabaImageGenProvider())
