"""Argparse surface + command implementations. All exit-code mapping lives here:

    0 success · 1 generation attempted and failed (envelope JSON on stderr)
    2 bad usage / no usable provider (UsageError)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from .envelope import GenRequest, UsageError, parse_size
from . import hermes_mode, routing

_ALIBABA_ENDPOINT_ENV = "ALIBABA_IMAGE_ENDPOINT"
_ALIBABA_CHAT = "/chat/completions"
_TP_KEY_VARS = ("ALIBABA_TOKEN_PLAN_API_KEY", "ALIBABA_TOKEN_PLAN_CN_API_KEY")


def _default_alibaba_endpoint(explicit_override: bool) -> None:
    """Registry-path only: the hermes alibaba plugin 404s on /images/generations for
    Token Plan hosts, so point it at chat when a TP login is in play. The standalone
    alibaba adapter does the same internally. Runs AFTER ~/.hermes/.env is loaded."""
    if explicit_override:
        return
    if (os.environ.get(_ALIBABA_ENDPOINT_ENV) or "").strip():
        return
    if not any((os.environ.get(v) or "").strip() for v in _TP_KEY_VARS):
        return
    os.environ[_ALIBABA_ENDPOINT_ENV] = _ALIBABA_CHAT


def b_available(b, req) -> bool:
    try:
        return bool(b.available(req))
    except Exception:  # noqa: BLE001 - a lying provider is an unavailable provider
        return False


def _supports(b, model) -> bool:
    try:
        return bool(b.supports_references(model))
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------- list ---

def cmd_list(args) -> int:
    backends: dict = {}
    if args.mode != "standalone":
        backends.update(routing.registry_backends(GenRequest(prompt="")))
    if args.mode != "hermes":
        for name, cls in routing.adapter_classes().items():
            backends.setdefault(name, cls())
    req = GenRequest(prompt="")
    rows = []
    for name, b in sorted(backends.items()):
        avail = b_available(b, req)
        rows.append({
            "provider": name,
            "kind": getattr(b, "kind", "adapter"),
            "available": avail,
            "key_envs": [str(v) for v in (getattr(b, "key_envs", None) or ())],
            "default_model": b.default_model(),
            "models": b.models() if (args.models or avail) else [],
        })
    payload = {
        "providers": rows,
        "modes": {"hermes": routing.hermes_status(args.mode),
                  "standalone_env_credentialed": sorted(
                      r["provider"] for r in rows
                      if r["kind"] == "adapter" and r["available"])},
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    for row in rows:
        print(f"{'OK ' if row['available'] else '-- '}{row['provider']} [{row['kind']}] "
              f"keys={','.join(row['key_envs']) or '-'} default={row['default_model']}")
        for mid in row["models"]:
            print(f"      {mid}")
    h = payload["modes"]["hermes"]
    if args.mode != "standalone" and not h["reachable"]:
        print(f"\n(hermes: {h['note']})")
    elif args.mode != "standalone" and not h["drivable"]:
        print(f"\n(hermes repo {h['repo']} present; {h['note']})")
    return 0


# ---------------------------------------------------------------- generate ---

def _build_request(args) -> GenRequest:
    try:
        size = parse_size(args.size) if args.size else None
    except ValueError as exc:
        raise UsageError(f"error: {exc}")
    refs: List[str] = []
    for r in args.ref or []:
        p = Path(r).expanduser()
        if not p.is_file():
            raise UsageError(f"error: reference image not found: {p}")
        refs.append(str(p.resolve()))
    endpoint = args.endpoint
    if endpoint and not endpoint.startswith("/"):
        endpoint = f"/{endpoint}"
    return GenRequest(prompt=args.prompt, aspect=args.aspect or "landscape", size=size,
                      model=args.model, references=tuple(refs),
                      api_key=(args.api_key or None),
                      base_url=(args.base_url or None), endpoint=endpoint,
                      timeout=args.timeout or 300.0)


def cmd_generate(args) -> int:
    if args.api_key and not args.provider:
        raise UsageError("error: --api-key requires --provider to name the backend to override.")
    if (args.base_url or args.endpoint) and not args.provider:
        raise UsageError("error: --base-url/--endpoint require --provider (they target one "
                         "named backend; there is no global host override).")
    req = _build_request(args)

    registry = routing.registry_backends(req) if args.mode != "standalone" else {}
    adapter_classes_map = routing.adapter_classes()

    # Unknown provider + pinned creds => synthesize a custom OpenAI-compatible
    # backend (the explicit custom-endpoint bypass).
    custom: Optional[object] = None
    if args.provider and args.provider not in registry and args.provider not in adapter_classes_map:
        from .openai_compat import OpenAICompatAdapter
        if not (args.api_key or args.base_url):
            raise UsageError(f"error: custom provider '{args.provider}' needs --api-key and/or "
                             "--base-url (and --endpoint if not /images/generations).")
        if not args.base_url:
            raise UsageError(f"error: custom provider '{args.provider}' needs --base-url.")
        custom = OpenAICompatAdapter(instance_name=args.provider)

    # One-off --api-key into a REGISTRY backend: inject its declared env var
    # (Hermes prefers ~/.hermes/.env over the process env, so the var alone would
    # be shadowed) + pin kwargs on the call.
    if args.api_key and args.provider in registry:
        b = registry[args.provider]
        envs = b.key_envs
        var = envs[0] if envs else None
        if var:
            os.environ[var] = args.api_key
        elif not b.available(req):
            raise UsageError(f"error: '{args.provider}' declares no api-key env var to override.")

    _default_alibaba_endpoint(bool(args.api_key or args.base_url))

    if custom is not None:
        chain = [custom]
        if not b_available(custom, req):
            raise UsageError(f"error: '{args.provider}' could not be used: no key resolved.")
        candidates = chain
    else:
        chain = routing.build_chain(req, mode=args.mode, requested=args.provider)
        candidates = chain[:1]
    if args.fallback and custom is None:
        others = routing.build_chain(req, mode=args.mode, requested=None)
        seen = {getattr(b, "name", id(b)) for b in candidates}
        for b in others:
            # name-dedup: adapters are re-instantiated per build_chain call, so
            # identity comparison would happily queue the same provider twice.
            name = getattr(b, "name", id(b))
            if name not in seen:
                seen.add(name)
                candidates.append(b)

    # Refs must be UNDERSTOOD, not ignored: feeding one to a text-only model yields
    # a 400 at best and a silently-ignored reference billing for a text-to-image
    # render at worst. Narrow to reference-capable candidates; name the alternatives
    # if none — dropping --ref stays an explicit user choice, never automatic.
    if req.references:
        capable = [b for b in candidates
                   if _supports(b, args.model) and b_available(b, req)]
        if not capable:
            pool = ([custom] if custom is not None else
                    routing.build_chain(req, mode=args.mode, requested=None))
            able = sorted(getattr(b, "name", "?") for b in pool
                          if _supports(b, args.model) and b_available(b, req))
            raise UsageError(
                "error: no selected provider accepts reference images.\n"
                f"       reference-capable + credentialed: {able or '(none — configure one)'}\n"
                "       re-run with --provider <one of those>, or drop --ref to generate "
                "text-to-image.")
        candidates = capable

    attempts: List[dict] = []
    result = None
    for backend in candidates:
        sub_req = GenRequest(**{**req.__dict__})
        out = backend.generate(sub_req)
        if getattr(backend, "route", "") == "hermes-registry" and not out.credential_source:
            out.credential_source = routing.credential_source_for(backend, req)
        if out.success:
            result = out
            break
        attempts.append({"provider": out.provider or getattr(backend, "name", "?"),
                         "model": out.model or (args.model or getattr(backend, "default_model",
                                                                      lambda: None)()) or "",
                         "error": out.error, "error_type": out.error_type})
        print(f"[fallback] {getattr(backend, 'name', '?')} failed: {out.error}", file=sys.stderr)
        result = out  # keep the last failure for the terminal envelope

    if result is None or not result.success:
        fail = (result.to_payload() if result else
                {"success": False, "error": "no backend attempted",
                 "error_type": "provider_exception"})
        print(json.dumps(fail, indent=2), file=sys.stderr)
        return 1

    if not result.image:
        print(json.dumps({"success": False, "provider": result.provider,
                          "error": "provider reported success but returned no image",
                          "error_type": "empty_response"}, indent=2), file=sys.stderr)
        return 1

    final = Path(result.image)
    if args.out and str(final) != str(Path(args.out).expanduser()):
        dest = Path(args.out).expanduser()
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(final, dest)
        final = dest
    result.image = str(final.resolve())
    result.attempts = attempts
    payload = result.to_payload()
    print(json.dumps(payload, indent=2) if args.json else payload["image"])
    return 0


# -------------------------------------------------------------------- main ---

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="imagegen",
                                 description="Generate images via Hermes providers when "
                                             "available, else built-in standalone adapters.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    lp = sub.add_parser("list", help="list providers and their models")
    lp.add_argument("--json", action="store_true")
    lp.add_argument("--models", action="store_true", help="include models for unavailable providers")
    lp.add_argument("--mode", choices=["auto", "hermes", "standalone"], default="auto")
    lp.set_defaults(func=cmd_list)

    gp = sub.add_parser("generate", help="generate one image")
    gp.add_argument("--prompt", required=True)
    gp.add_argument("--provider", default=None,
                    help="openrouter | alibaba | openai | gemini | fal | openai-codex | "
                         "<custom name with --api-key/--base-url>")
    gp.add_argument("--model", default=None)
    gp.add_argument("--aspect", default="landscape", choices=["landscape", "square", "portrait"])
    gp.add_argument("--size", default=None, help="exact canvas WxH (e.g. 1920x1080); wins "
                                                 "over --aspect; adapters snap + report in notes")
    gp.add_argument("--out", default=None, help="where to write the image")
    gp.add_argument("--ref", action="append", default=[], help="reference image path (repeatable)")
    gp.add_argument("--api-key", default=None, help="override the provider's key for this call only")
    gp.add_argument("--base-url", default=None, help="--provider must be named")
    gp.add_argument("--endpoint", default=None,
                    help="path override: /chat/completions | /images/generations | custom; "
                         "requires --provider")
    gp.add_argument("--fallback", action="store_true",
                    help="on provider failure, retry through the other credentialed backends")
    gp.add_argument("--mode", choices=["auto", "hermes", "standalone"], default="auto",
                    help="hermes = registry only (fails without a checkout); standalone = "
                         "built-in adapters only")
    gp.add_argument("--timeout", type=float, default=None)
    gp.add_argument("--json", action="store_true")
    gp.set_defaults(func=cmd_generate)
    return ap


def main(argv: List[str]) -> int:
    # Load ~/.hermes/.env first so every route sees the same credential surface.
    try:
        hermes_mode.load_env()
    except Exception:  # noqa: BLE001 - an unreadable .env must not break standalone use
        pass
    args = build_parser().parse_args(argv[1:])
    mode = getattr(args, "mode", "auto")
    if mode != "standalone":
        routing.maybe_reexec_into_venv(argv, mode=mode, has_api_key=bool(getattr(args, "api_key", None)))
    try:
        return args.func(args)
    except UsageError as exc:
        print(str(exc), file=sys.stderr)
        return 2
