"""Standalone Alibaba adapter — the Hermes-free port of
``plugins/image_gen_alibaba/alibaba.py`` (this monorepo).

CHANGE NOTE (drift contract, mirrored in the plugin's module docstring):
this file must keep the same ladder, payloads, failure classification and
response-shape tolerance as the plugin. The plugin suite
``tests/image_gen_alibaba/`` is the behavioral reference; ``tests/image_pipeline/
test_adapters_alibaba.py`` reuses its response fixtures. When you change one,
check the other and note it in docs/image-gen-alibaba.md.

Kept from the plugin: plan ladder + preference order, ``_should_advance`` failure
classification (401/403/429/5xx/timeouts advance between logins; model-not-found
advances only on unverified plans), ``extract_image`` (5 response shapes), chat
endpoint defaulting for Token Plan keys, 3 MB inline ref cap with optional-PIL
recompress (via imagegen.refs), payload inference from endpoint path.

Dropped (Hermes-only): ``resolve_runtime_provider`` credential pool, config.yaml
tiers, ``get_secret`` secret scoping. Replaced by: process env + ``~/.hermes/.env``
(which routing loads before adapters are consulted, so keys bootstrapped for
Hermes work here too).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .adapters import (OPENAI_SIZES, ProviderAdapter, extract_image, final_aspect,
                       measure_or_meta, save_b64)
from .envelope import GenRequest, GenResult
from .http import download, request_json
from .refs import to_data_uri

_SIZES = {"landscape": "1280*720", "square": "1024*1024", "portrait": "720*1280"}
MAX_REFERENCES = 4
MODEL_ENV_VAR = "ALIBABA_IMAGE_MODEL"
DEFAULT_MODEL = "wan2.7-image"
CHAT_ENDPOINT = "/chat/completions"
IMAGES_ENDPOINT = "/images/generations"

_TOKEN_INTL = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
_TOKEN_CN = "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
_PAYG_INTL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
_PAYG_CN = "https://dashscope.aliyuncs.com/compatible-mode/v1"

WAN_IMAGE_MODELS = {
    "wan2.7-image": {"display": "Wan 2.7 Image", "default": True},
    "wan2.7-image-pro": {"display": "Wan 2.7 Image Pro"},
}


@dataclass(frozen=True)
class Plan:
    profile: str
    key_envs: Tuple[str, ...]
    base_default: str
    base_env: str = ""
    verified_models: bool = False
    requires_base_env: bool = False


PLANS: Tuple[Plan, ...] = (
    Plan("alibaba-token-plan", ("ALIBABA_TOKEN_PLAN_API_KEY",), _TOKEN_INTL,
         "ALIBABA_TOKEN_PLAN_BASE_URL", verified_models=True),
    Plan("alibaba-token-plan-cn", ("ALIBABA_TOKEN_PLAN_CN_API_KEY", "ALIBABA_TOKEN_PLAN_API_KEY"),
         _TOKEN_CN, "ALIBABA_TOKEN_PLAN_CN_BASE_URL", verified_models=True),
    Plan("alibaba", ("DASHSCOPE_API_KEY",), _PAYG_INTL),
    Plan("alibaba-cn", ("DASHSCOPE_API_KEY",), _PAYG_CN, "DASHSCOPE_CN_BASE_URL",
         requires_base_env=True),
)


def _hostname(url: str) -> str:
    return (urlparse(url).netloc or url).lower()


def _custom_creds() -> Optional[Tuple[str, str]]:
    key = (os.environ.get("ALIBABA_API_KEY") or "").strip()
    base = (os.environ.get("ALIBABA_BASE_URL") or "").strip()
    if key and base:
        return key, base.rstrip("/")
    return None


def _candidate_plans() -> Tuple[Plan, ...]:
    custom = _custom_creds()
    all_plans = PLANS + ((Plan("custom", ("ALIBABA_API_KEY",), custom[1], "ALIBABA_BASE_URL"),)
                         if custom else ())
    pin = (os.environ.get("ALIBABA_IMAGE_PLAN") or "").strip().lower()
    if pin:
        return tuple(p for p in all_plans if p.profile == pin)
    return tuple(p for p in all_plans
                 if not p.requires_base_env or (os.environ.get(p.base_env) or "").strip())


def _plan_hosts_ok(plan: Plan, base_url: str) -> bool:
    expected = {_hostname(plan.base_default)}
    override = (os.environ.get(plan.base_env) or "").strip() if plan.base_env else ""
    if override:
        expected.add(_hostname(override))
    return _hostname(base_url) in expected


def _resolve_plan_credentials(plan: Plan) -> Optional[Tuple[str, str]]:
    if plan.profile == "custom":
        return _custom_creds()
    base_url = ((os.environ.get(plan.base_env) or "").strip() or plan.base_default).rstrip("/")
    for var in plan.key_envs:
        key = (os.environ.get(var) or "").strip()
        if key:
            return key, base_url
    return None


def _is_model_not_found(status: int, message: str) -> bool:
    if status != 404:
        msg = (message or "").lower()
        return "model" in msg and any(h in msg for h in ("not found", "not exist", "does not exist", "unsupported"))
    return True


def _should_advance(plan: Plan, status: int, kind: str, message: str) -> bool:
    if kind in ("timeout", "connection"):
        return True
    if kind == "http":
        if _is_model_not_found(status, message):
            return not plan.verified_models
        return status in (401, 403, 429) or status >= 500
    return False


def _token_plan_in_env() -> bool:
    return any((os.environ.get(v) or "").strip()
               for v in ("ALIBABA_TOKEN_PLAN_API_KEY", "ALIBABA_TOKEN_PLAN_CN_API_KEY"))


class AlibabaAdapter(ProviderAdapter):
    name = "alibaba"
    display = "Alibaba (unified: Token Plan intl/CN, DashScope PAYG, custom)"
    priority = 40
    key_envs = ("ALIBABA_TOKEN_PLAN_API_KEY", "ALIBABA_TOKEN_PLAN_CN_API_KEY",
                "DASHSCOPE_API_KEY", "ALIBABA_API_KEY")
    supports_endpoint = True
    max_reference_images = MAX_REFERENCES

    def default_model(self) -> Optional[str]:
        return (os.environ.get(MODEL_ENV_VAR) or "").strip() or DEFAULT_MODEL

    def models(self) -> List[str]:
        return list(WAN_IMAGE_MODELS)

    def available(self, req: Optional[GenRequest] = None) -> bool:
        # Usable iff some plan resolves a key (the custom tier needs the key AND its
        # base pair; alibaba-cn needs its CN base env) — _plan_attempts is the single
        # source of truth, so availability can never lie about what generate() finds.
        return bool(self._plan_attempts(req or GenRequest(prompt="")))

    def _resolve_endpoint(self, req: GenRequest) -> str:
        """explicit --endpoint → ALIBABA_IMAGE_ENDPOINT env → chat for Token Plan keys
        (their hosts 404 /images/generations) → /images/generations."""
        if req.endpoint:
            return req.endpoint if req.endpoint.startswith("/") else f"/{req.endpoint}"
        env_val = (os.environ.get("ALIBABA_IMAGE_ENDPOINT") or "").strip()
        if env_val.startswith("/"):
            return env_val
        if req.api_key or req.base_url:
            return IMAGES_ENDPOINT  # caller pinned the surface; do not guess for them
        if _token_plan_in_env():
            return CHAT_ENDPOINT
        return IMAGES_ENDPOINT

    def _plan_attempts(self, req: GenRequest) -> List[Tuple[Plan, Tuple[str, str]]]:
        if req.api_key:
            base = (req.base_url or _TOKEN_INTL).strip().rstrip("/")
            return [(Plan("explicit", (), base, verified_models=True), (req.api_key.strip(), base))]
        out = []
        for plan in _candidate_plans():
            creds = _resolve_plan_credentials(plan)
            if creds:
                out.append((plan, creds))
        return out

    def generate(self, req: GenRequest) -> GenResult:
        return self._guard(req, lambda: self._generate(req))

    def _generate(self, req: GenRequest) -> GenResult:
        prompt = (req.prompt or "").strip()
        if not prompt:
            return self._fail(req, "Prompt is required and must be a non-empty string", "invalid_input")
        model = (req.model or self.default_model()).strip()
        aspect = final_aspect(req)

        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        notes: List[str] = []
        refs = list(req.references)[:MAX_REFERENCES]
        if len(req.references) > MAX_REFERENCES:
            notes.append(f"{len(req.references) - MAX_REFERENCES} reference(s) dropped "
                         f"(provider cap {MAX_REFERENCES})")
        skipped = 0
        for src in refs:
            uri, err = to_data_uri(src)
            if uri is None:
                skipped += 1
            else:
                content.append({"type": "image", "image": uri})
        if refs and len(content) == 1:
            return self._fail(req, f"all {len(refs)} reference image(s) could not be read", "io_error")
        if skipped:
            notes.append(f"{skipped} reference image(s) skipped (unreadable)")
        if len(content) > 1:
            notes.append("wan ignores size when reference images are supplied")

        attempts = self._plan_attempts(req)
        if not attempts:
            return self._fail(
                req, "No usable Alibaba credentials: set one of ALIBABA_TOKEN_PLAN_API_KEY / "
                     "ALIBABA_TOKEN_PLAN_CN_API_KEY / DASHSCOPE_API_KEY (or ALIBABA_API_KEY + "
                     "ALIBABA_BASE_URL, or pass --api-key/--base-url)", "missing_api_key")

        size_used, size_note = self._resolve_size(req, aspect)
        endpoint = self._resolve_endpoint(req)
        is_images = "images/generations" in endpoint
        tried: List[str] = []
        last: Optional[GenResult] = None
        for plan, (api_key, base_url) in attempts:
            if is_images and len(content) > 1:
                return self._fail(
                    req, "reference images are not supported on the /images/generations surface "
                         "(use --endpoint /chat/completions, or omit references)",
                    "modality_unsupported")
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = (
                {"model": model, "prompt": prompt, "n": 1,
                 "size": size_used or OPENAI_SIZES.get(aspect, OPENAI_SIZES["square"])}
                if is_images else
                {"model": model, "messages": [{"role": "user", "content": content}],
                 "size": size_used or _SIZES.get(aspect, _SIZES["landscape"])}
            )
            body, failure = request_json(
                "POST", f"{base_url}{endpoint}", headers=headers, payload=payload,
                timeout=(20.0, req.timeout), label=f"Alibaba {plan.profile}")
            tried.append(plan.profile)
            if failure is None:
                result = self._success(body, req, plan=plan, model=model, aspect=aspect,
                                       notes=notes, tried=tried, size_note=size_note)
                return result
            last = self._fail(req, failure.error, failure.error_type)
            if not _should_advance(plan, failure.status, failure.kind, failure.message):
                return last
        if last is not None:
            last.error = (f"all Alibaba logins failed — tried {', '.join(tried)}; "
                          f"last: {last.error}")
        return last or self._fail(req, "no attempts made", "provider_exception")

    def _resolve_size(self, req: GenRequest, aspect: str):
        """--size WxH: Token-Plan wan chat surface takes 'W*H'; nearest verified ratio
        is honored with the note trail; images surface snaps to OPENAI_SIZES."""
        if not req.size:
            return None, None
        w, h = req.size
        return f"{w}*{h}", f"exact size {w}x{h} requested; delivered size depends on the model (refs override size on wan)"

    def _success(self, body, req, *, plan, model, aspect, notes, tried, size_note) -> GenResult:
        image_ref, b64 = extract_image(body)
        if image_ref is None and b64 is None:
            excerpt = str(body)[:300]
            return self._fail(req, f"no image in response: {excerpt}", "empty_response")
        if size_note:
            notes.append(size_note)
        try:
            if b64:
                path = save_b64(b64, f"alibaba_{plan.profile}")
            else:
                path = download(image_ref, _cache_path(f"alibaba_{plan.profile}", image_ref),
                                timeout=60.0)
        except Exception as exc:  # noqa: BLE001 - OSError/URLError both surface as io_error
            return self._fail(req, f"Could not save image locally: {exc}", "io_error")
        result = GenResult(
            success=True, provider=self.name, model=model, image=str(path.resolve()),
            prompt=req.prompt, references=req.references, aspect=aspect,
            plan=plan.profile, notes=notes,
            size_requested=f"{req.size[0]}x{req.size[1]}" if req.size else None,
            credential_source="cli" if req.api_key else "env",
            route="direct" if req.api_key else "standalone")
        # debug_info render metadata, same mapping as the plugin's _success.
        debug = (body.get("output") or {}).get("debug_info") if isinstance(body, dict) else None
        if isinstance(debug, list) and debug and isinstance(debug[0], dict):
            d = debug[0]
            for src, dst in (("actual_seed", "seed"), ("output_W", "width"), ("output_H", "height")):
                if d.get(src) is not None:
                    setattr(result, dst, d[src])
        # Multi-login ladder trail belongs in `notes` (measured width/height stay the
        # pixel truth); `attempts` is reserved for CLI-level failed backends (see cli).
        if len(tried) > 1:
            result.add_note(f"logins tried: {', '.join(tried)}")
        return measure_or_meta(result)


def _cache_path(prefix: str, url: str = ""):
    import re
    import time
    from .adapters import cache_dir
    ext = "png"
    m = re.search(r"\.(png|jpe?g|webp|gif)(?:[?#]|$)", url.lower())
    if m:
        ext = "jpg" if m.group(1) in ("jpeg", "jpg") else m.group(1)
    return cache_dir() / f"{prefix}-{int(time.time() * 1000) % 10**13}.{ext}"
