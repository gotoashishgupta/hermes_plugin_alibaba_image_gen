#!/usr/bin/env python3
"""Generate images via Hermes providers when available, else built-in standalone
adapters (alibaba / openai-compat / openrouter / fal / gemini) — or a fully
explicit custom endpoint via --provider/--api-key/--base-url/--endpoint.

Routing (see imagegen/routing.py + docs/image-pipeline.md):
  1. Hermes present AND configured for image generation → Hermes' providers
     (this script re-execs into the hermes-agent venv itself when needed).
  2. Hermes plugin code reachable but nothing configured → the same registry
     backends driven by provider env keys, reported as credential_source="env".
  3. No Hermes at all → standalone adapters using env credentials.

Usage:
  imagegen.py list [--json] [--mode auto|hermes|standalone]
  imagegen.py generate --prompt "..." [--provider NAME] [--model ID]
      [--aspect landscape|square|portrait] [--size WxH] [--out PATH] [--ref PATH]...
      [--api-key KEY] [--base-url URL] [--endpoint PATH] [--fallback]
      [--mode auto|hermes|standalone] [--timeout SECONDS] [--json]

Exit codes: 0 success, 1 generation failed, 2 bad usage / no usable provider.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from imagegen.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv))
