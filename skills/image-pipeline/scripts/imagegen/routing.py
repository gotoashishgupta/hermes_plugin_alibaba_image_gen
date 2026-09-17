"""Routing ladder — decides WHO generates, never HOW (backends own that).

    explicit --api-key/--base-url/--endpoint + --provider ......... "direct"
    hermes repo present & drivable (mode 1/2, same code path) ..... registry backends
      configured via hermes ladder → credential_source "hermes-ladder"
      reachable plugin, keys from env only → "env" + note, adapters join --fallback
    no hermes / unloadable deps (mode 3) ........................ standalone adapters
Any interpreter failure on the Hermes side degrades to standalone with a stderr
warning — the script never hard-fails because Hermes is absent.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import List, Optional

from .envelope import GenRequest, UsageError
from . import hermes_mode

PREFERRED = ("openai-codex", "openrouter", "openai", "gemini", "alibaba", "fal")

_NO_REEXEC_ENV = "IMAGEGEN_NO_REEXEC"


# ---------------------------------------------------------------- adapters ---

def adapter_classes() -> dict:
    """Standalone adapters keyed by provider name (late import keeps module import cheap)."""
    from .alibaba import AlibabaAdapter
    from .openai_compat import OpenAICompatAdapter
    from .openrouter import OpenRouterAdapter
    from .fal import FalAdapter
    from .gemini import GeminiAdapter
    classes = {}
    for cls in (OpenRouterAdapter, OpenAICompatAdapter, GeminiAdapter, AlibabaAdapter, FalAdapter):
        inst = cls()
        classes[inst.name] = cls
    return classes


def new_adapter(name: str):
    cls = adapter_classes().get(name)
    return cls() if cls else None


def standalone_backends(req: GenRequest) -> list:
    """Every env-credentialed adapter (plus a CLI-pinned one when --provider names it)."""
    out = []
    for cls in adapter_classes().values():
        adapter = cls()
        if adapter.available(req):
            out.append(adapter)
    return out


def preferred_order(names) -> list:
    def key(n):
        return PREFERRED.index(n) if n in PREFERRED else len(PREFERRED) + 1
    return sorted(names, key=lambda n: (key(n), n))


# ------------------------------------------------------------ hermes modes ---

def hermes_status(req_mode: str) -> dict:
    """{reachable, drivable, repo, venv, note} — cheap, no side effects beyond env load."""
    repo = hermes_mode.hermes_repo()
    info = {"reachable": repo is not None, "repo": str(repo) if repo else None,
            "drivable": False, "venv": None, "note": ""}
    if repo is None:
        info["note"] = ("hermes repo not found (set HERMES_AGENT_REPO to the hermes-agent "
                        "checkout to enable Hermes providers)")
        return info
    info["venv"] = str(hermes_mode.venv_python(repo) or "")
    info["drivable"] = hermes_mode.hermes_importable(repo)
    if not info["drivable"]:
        info["note"] = "hermes deps not importable with THIS interpreter (re-exec or standalone)"
    return info


def registry_backends(req: GenRequest) -> dict:
    """name → HermesBackend, or {} when unloadable (warning on stderr, never abort)."""
    repo = hermes_mode.hermes_repo()
    if repo is None:
        return {}
    try:
        providers = hermes_mode.registry_providers(repo)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"warning: could not load Hermes registry providers: {exc}", file=sys.stderr)
        return {}
    return {name: hermes_mode.HermesBackend(name, prov, repo)
            for name, prov in providers.items()}


def hermes_configured_provider() -> Optional[str]:
    """The provider named in ~/.hermes/config.yaml image_gen: provider (text-scanned —
    config.yaml belongs to hermes, not to this script's deps)."""
    cfg = hermes_mode.DEFAULT_HERMES_HOME / "config.yaml"
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"^image_gen:\s*$((?:\n[ \t]+.+)*)", text, re.M)
    if not m:
        m2 = re.search(r"^image_gen:\s+provider:\s+(\S+)", text, re.M)
        return m2.group(1) if m2 else None
    m3 = re.search(r"^\s+provider:\s+(\S+)", m.group(1), re.M)
    return m3.group(1) if m3 else None


def credential_source_for(backend, req: GenRequest) -> str:
    """Why does this Hermes backend hold credentials? Hermes' ladder (auth pool /
    config / OAuth) says 'hermes-ladder'; plain provider env vars say 'env' — the
    mode-1 vs mode-2 distinction the skill promises to report honestly."""
    if req.api_key:
        return "cli"
    configured = hermes_configured_provider()
    if configured and backend.name == configured:
        return "hermes-ladder"
    declared = list(getattr(backend, "key_envs", None) or [])
    if declared and any((os.environ.get(v) or "").strip() for v in declared):
        return "env"
    return "hermes-ladder"  # OAuth (no declared key env) or ladder-resolved


# ------------------------------------------------------------------- chain ---

def build_chain(req: GenRequest, *, mode: str, requested: Optional[str]) -> List[object]:
    """Every usable backend, primary first. Raises UsageError (exit 2) when nothing qualifies."""
    backends: dict = {}
    if mode == "hermes":
        if hermes_mode.hermes_repo() is None:
            raise UsageError(
                "error: --mode hermes requires a hermes-agent checkout "
                "(HERMES_AGENT_REPO or ~/.hermes/hermes-agent). Re-run with --mode standalone "
                "to use the built-in provider adapters instead.")
        backends = registry_backends(req)
    elif mode == "standalone":
        for adapter in standalone_backends(req):
            backends[adapter.name] = adapter
    else:  # auto
        backends = registry_backends(req)
        for adapter in standalone_backends(req):
            backends.setdefault(adapter.name, adapter)

    avail = {n: b for n, b in backends.items() if b.available(req)}
    if not avail:
        hint = ("Configure a provider (hermes tools -> Image Generation when Hermes is your "
                "agent), or export one of: " +
                ", ".join(sorted({v for a in adapter_classes().values()
                                  for v in a().key_envs})) +
                ", or pass --provider X --api-key <key> [--base-url URL].")
        raise UsageError(
            "error: no credentialed image provider found.\n"
            f"       registered: {sorted(backends)}\n       {hint}")
    if requested:
        if requested not in avail:
            raise UsageError(f"error: '{requested}' is not credentialed. Available: {sorted(avail)}")
        primary = [avail.pop(requested)]
        rest_order = preferred_order(avail.keys())
        return primary + [avail[n] for n in rest_order]
    names = preferred_order(avail.keys())
    return [avail[n] for n in names]


# ------------------------------------------------------------------ re-exec ---

def maybe_reexec_into_venv(argv: List[str], *, mode: str, has_api_key: bool) -> None:
    """If Hermes is reachable but THIS interpreter can't drive it, re-run the same
    command under the Hermes venv (one hop, loop-guarded by IMAGEGEN_NO_REEXEC).
    Never blocks standalone use: probe/exec failure just continues where we are."""
    if mode == "standalone" or has_api_key:
        return
    if os.environ.get(_NO_REEXEC_ENV):
        return
    repo = hermes_mode.hermes_repo()
    if repo is None:
        return
    if hermes_mode.hermes_importable(repo):
        return
    venv = hermes_mode.venv_python(repo)
    if venv is None:
        print("warning: Hermes repo found but no venv python — using standalone adapters "
              "with this interpreter.", file=sys.stderr)
        return
    script = str(Path(argv[0]).resolve())
    env = dict(os.environ, **{_NO_REEXEC_ENV: "1"})
    try:
        os.execve(str(venv), [str(venv), script, *argv[1:]], env)
    except OSError as exc:  # exec failed → degrade, don't die
        print(f"warning: could not re-exec into Hermes venv ({exc}); continuing standalone.",
              file=sys.stderr)
