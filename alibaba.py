"""Unified Alibaba image_gen provider.

One provider id — ``alibaba`` — that picks the plan and base URL from whichever
API key resolves, in preference order:

  1. ``alibaba-token-plan``     — Token Plan international (ALIBABA_TOKEN_PLAN_API_KEY)
  2. ``alibaba-token-plan-cn``  — Token Plan China        (ALIBABA_TOKEN_PLAN_CN_API_KEY)
  3. ``alibaba``                — DashScope PAYG intl     (DASHSCOPE_API_KEY)
  4. ``alibaba-cn``             — DashScope PAYG China    (DASHSCOPE_API_KEY + DASHSCOPE_CN_BASE_URL)

Credentials resolve through Hermes' own ladder (``resolve_runtime_provider``: credential
pool → ``~/.hermes/.env`` → process env), so keys injected at boot by the OpenBao plugin,
saved by ``hermes tools``, or pooled in ``auth.json`` all work identically.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from agent.image_gen_provider import (
    ImageGenProvider, error_response, resolve_aspect_ratio, success_response)
from agent.secret_scope import get_secret
from plugins.image_gen._common import (
    OPENAI_SIZES, catalog_rows, collect_source_images, load_image_gen_config,
    materialize_image, post_json, HttpFailure)

# DashScope Wan size tiers for the semantic aspects (note: `size` is ignored by the
# model whenever reference images are present).
_SIZES = {"landscape": "1280*720", "square": "1024*1024", "portrait": "720*1280"}

MAX_REFERENCES = 4
# Gateway reality: an 8 MB PNG reference inlined as a data URI 413s ("Request body size
# exceeds maximum"). Keep raw files under this and recompress above it.
MAX_INLINE_IMAGE_BYTES = 3 * 1024 * 1024


def _recompress_for_inline(path: str, data: bytes) -> Optional[Tuple[bytes, str]]:
    """Downscale + JPEG-re-encode an oversized local image until it fits the inline cap.
    Returns (bytes, mime) or None when it can't be decoded — the caller then inlines raw
    and lets the API error surface loudly rather than silently dropping the reference."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    try:
        import io

        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        quality = 85
        for _ in range(4):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            if buf.tell() <= MAX_INLINE_IMAGE_BYTES or quality <= 40:
                return buf.getvalue(), "image/jpeg"
            quality -= 15
            longest = max(img.size)
            scale = 2048 / longest if longest > 2048 else 0.75
            img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
        return buf.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001
        return None


def _image_part(src: str) -> Optional[Dict[str, Any]]:
    """One source image as a chat content part. URLs/data URIs pass through; local
    files are inlined as base64 data URIs (recompressed when oversized); unreadable →
    None (the caller must not silently drop it and bill for a text-to-image render)."""
    s = str(src).strip()
    if s.startswith(("http://", "https://", "data:")):
        return {"type": "image", "image": s}
    try:
        data = Path(s).read_bytes()
    except OSError:
        return None
    mime = mimetypes.guess_type(s)[0] or "image/png"
    if len(data) > MAX_INLINE_IMAGE_BYTES:
        shrunk = _recompress_for_inline(s, data)
        if shrunk is not None:
            data, mime = shrunk
    return {"type": "image", "image": f"data:{mime};base64,{base64.b64encode(data).decode()}"}


def _body_excerpt(body: Any) -> str:
    import json

    return (json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body)[:300]


def _extract_image(body: Any) -> Tuple[Optional[str], Optional[str]]:
    """First ``(url, b64)`` image found across the shapes Alibaba/MaaS and OpenAI-compatible
    servers return:
      * ``data[]`` entries with ``url`` or ``b64_json`` (OpenAI /images/generations),
      * ``output.choices`` (Token Plan) or plain ``choices`` (PAYG/compat) with the image
        at ``message.content[*]`` (``{"type": "image", "image": <url|data:>}``) or at
        ``message.images[]`` (``{"image_url": {"url": ...}}`` or a bare URL string).
    No image → (None, None)."""
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
                image = (img.get("image_url") or {}).get("url") if isinstance(img.get("image_url"), dict) else img.get("image_url") or img.get("image") or img.get("url")
            else:
                image = img
            url, b64 = _from_value(image)
            if url or b64:
                return url, b64
    return None, None


PROVIDER_ID = "alibaba"

# Wan image models served over chat/completions (verified against Token Plan).
WAN_IMAGE_MODELS: Dict[str, Dict[str, Any]] = {
    "wan2.7-image": {
        "display": "Wan 2.7 Image",
        "speed": "~15s",
        "strengths": "Default; accepts reference images (ignores size when a ref is present)",
    },
    "wan2.7-image-pro": {
        "display": "Wan 2.7 Image Pro",
        "speed": "~40s",
        "strengths": "Higher fidelity",
    },
}
DEFAULT_MODEL = "wan2.7-image"
MODEL_ENV_VAR = "ALIBABA_IMAGE_MODEL"

_TOKEN_INTL = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
_TOKEN_CN = "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
_PAYG_INTL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
_PAYG_CN = "https://dashscope.aliyuncs.com/compatible-mode/v1"


@dataclass(frozen=True)
class Plan:
    """One Alibaba login rung: a Hermes model-provider profile + its key/base contract."""

    profile: str                  # id consumed by resolve_runtime_provider(requested=...)
    key_envs: Tuple[str, ...]     # ordered key vars for the env-only fallback path
    base_default: str
    base_env: str = ""            # per-plan base-URL override var ("" = profile declares none)
    verified_models: bool = False # PAYG wan availability is unproven
    requires_base_env: bool = False  # alibaba-cn: skip unless the CN base is explicitly set


# Preference order per design: Token Plan intl → Token Plan CN → DashScope PAYG intl → CN.
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
    """(api_key, base_url) for a dedicated workspace / OpenAI-compatible endpoint.

    Three tiers, first fully-set pair wins (a half-set tier never mixes with another):
      1. env:   ALIBABA_API_KEY + ALIBABA_BASE_URL
      2. config: image_gen.alibaba.api_key + .base_url
      3. config ref: image_gen.alibaba.provider → top-level providers.<name>
         ({api|url|base_url} + {api_key|key|key_env}) — the same chain as community
         openai-compatible image plugins, so GUI/pool-managed providers work.
    """
    key = (get_secret("ALIBABA_API_KEY", "") or "").strip()
    base = (get_secret("ALIBABA_BASE_URL", "") or "").strip()
    if key and base:
        return key, base.rstrip("/")
    cfg = load_image_gen_config("alibaba")
    key = str(cfg.get("api_key") or "").strip()
    base = str(cfg.get("base_url") or "").strip()
    if key and base:
        return key, base.rstrip("/")
    ref = str(cfg.get("provider") or "").strip()
    if ref:
        try:
            from hermes_cli.config import load_config

            providers = load_config().get("providers")
        except Exception:  # noqa: BLE001 - config is best-effort
            providers = None
        entry = providers.get(ref) if isinstance(providers, dict) else None
        if isinstance(entry, dict):
            base = next((str(entry[k]).strip() for k in ("api", "url", "base_url")
                         if entry.get(k)), "")
            key = str(entry.get("api_key") or entry.get("key") or "").strip()
            if not key and entry.get("key_env"):
                key = (get_secret(str(entry["key_env"]).strip(), "") or "").strip()
            if key and base:
                return key, base.rstrip("/")
    return None


def _custom_plan() -> Optional[Plan]:
    creds = _custom_creds()
    if not creds:
        return None
    return Plan("custom", ("ALIBABA_API_KEY",), creds[1], "ALIBABA_BASE_URL")


def _candidate_plans() -> Tuple[Plan, ...]:
    """Plans eligible now, in order; the custom rung (if configured) is ALWAYS last.
    ``ALIBABA_IMAGE_PLAN`` pins one profile; without it, ``alibaba-cn`` is skipped
    unless its CN base URL is explicitly set (the intl DASHSCOPE key would 401 on the
    CN host)."""
    custom = _custom_plan()
    all_plans = PLANS + ((custom,) if custom else ())
    pin = (os.environ.get("ALIBABA_IMAGE_PLAN") or "").strip().lower()
    if pin:
        return tuple(p for p in all_plans if p.profile == pin)
    return tuple(
        p for p in all_plans
        if not p.requires_base_env or (os.environ.get(p.base_env) or "").strip()
    )


def _is_model_not_found(failure: HttpFailure) -> bool:
    if failure.kind != "http":
        return False
    if failure.status == 404:
        return True
    msg = (failure.message or "").lower()
    return "model" in msg and any(h in msg for h in ("not found", "not exist", "does not exist", "unsupported"))


def _should_advance(plan: Plan, failure: HttpFailure) -> bool:
    """Which failures justify retrying with the next Alibaba login, and which are
    request-level (bad payload/model) and will fail identically everywhere."""
    if failure.kind in ("timeout", "connection", "request"):
        return True
    if failure.kind == "http":
        if _is_model_not_found(failure):
            # Verified plans serve the declared wan catalog; a model error there is a
            # caller mistake, not a plan problem. On unverified PAYG rungs it may be the
            # plan lacking the model — advance and let another login answer.
            return not plan.verified_models
        return failure.status in (401, 403, 429) or failure.status >= 500
    return False


def _plan_hosts_ok(plan: Plan, base_url: str) -> bool:
    expected = {_hostname(plan.base_default)}
    override = (os.environ.get(plan.base_env) or "").strip() if plan.base_env else ""
    if override:
        expected.add(_hostname(override))
    return _hostname(base_url) in expected


def _resolve_plan_credentials(plan: Plan) -> Optional[Tuple[str, str]]:
    """(api_key, base_url) for one plan, or None when unusable.

    Prefers Hermes' resolver (credential pool → ~/.hermes/.env → process env); falls
    back to plain env reads outside a Hermes process. A runtime whose host matches
    neither the plan's default nor its override is rejected — that would be the
    resolver's fallback machinery handing us another vendor's credentials.
    """
    if plan.profile == "custom":
        # The custom rung IS its own config — no resolver probing ("custom" could be
        # claimed by Hermes' bare-custom alias machinery), no host guard needed.
        return _custom_creds()
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider
    except ImportError:
        runtime = None
    else:
        try:
            runtime = resolve_runtime_provider(requested=plan.profile)
        except Exception:
            runtime = None
    if isinstance(runtime, dict):
        api_key = str(runtime.get("api_key") or "").strip()
        base_url = str(runtime.get("base_url") or "").strip().rstrip("/")
        if api_key and base_url and _plan_hosts_ok(plan, base_url):
            return api_key, base_url
        return None
    base_url = ((get_secret(plan.base_env, "") or "").strip() or plan.base_default).rstrip("/")
    for var in plan.key_envs:
        key = (get_secret(var, "") or "").strip()
        if key:
            return key, base_url
    return None


class AlibabaImageGenProvider(ImageGenProvider):
    @property
    def name(self) -> str:
        return PROVIDER_ID

    @property
    def display_name(self) -> str:
        return "Alibaba (unified)"

    def is_available(self) -> bool:
        # No network: a plan is available iff its credential ladder yields a key.
        return any(_resolve_plan_credentials(plan) for plan in _candidate_plans())

    def list_models(self):
        return catalog_rows(WAN_IMAGE_MODELS)

    def _resolve_model(self, explicit: Optional[str]) -> str:
        """explicit → ``ALIBABA_IMAGE_MODEL`` → ``image_gen.alibaba.model`` →
        ``image_gen.model`` → default. Unknown ids pass through everywhere: PAYG
        endpoints may serve models outside the verified wan catalog, and an id the
        caller named must never be silently dropped."""
        for candidate in (
            explicit,
            os.environ.get(MODEL_ENV_VAR),
        ):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        cfg = load_image_gen_config()
        scoped = cfg.get("alibaba")
        for candidate in (
            scoped.get("model") if isinstance(scoped, dict) else None,
            cfg.get("model"),
        ):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return DEFAULT_MODEL

    @staticmethod
    def _resolve_endpoint() -> str:
        """Endpoint path — always returns a valid path (never None).

        ``ALIBABA_IMAGE_ENDPOINT`` env → ``image_gen.alibaba.endpoint`` config →
        ``/images/generations``. The provider POSTs once to ``{base_url}{endpoint}``
        with no retry. Payload shape is inferred from the path: ``images/generations``
        → images payload; anything else → chat payload. Invalid values fall through
        to the default.
        """
        env_val = (os.environ.get("ALIBABA_IMAGE_ENDPOINT") or "").strip()
        if env_val.startswith("/"):
            return env_val
        cfg = load_image_gen_config("alibaba")
        cfg_val = str(cfg.get("endpoint") or "").strip()
        if cfg_val.startswith("/"):
            return cfg_val
        return "/images/generations"

    def capabilities(self) -> Dict[str, Any]:
        # Verified: wan2.7-image accepts an image content part and adopts its aspect.
        return {"modalities": ["text", "image"], "max_reference_images": MAX_REFERENCES}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Alibaba (unified)",
            "badge": "paid",
            "tag": (
                "Wan 2.7 image via Alibaba — auto-selects Token Plan "
                "(ALIBABA_TOKEN_PLAN_API_KEY), Token Plan CN, or DashScope PAYG "
                "(DASHSCOPE_API_KEY [+ DASHSCOPE_CN_BASE_URL for China]). A dedicated "
                "workspace / OpenAI-compatible endpoint can pin ALIBABA_API_KEY + "
                "ALIBABA_BASE_URL (or image_gen.alibaba.api_key/base_url/provider) with "
                "ALIBABA_IMAGE_MODEL."
            ),
            "env_vars": [
                {
                    "key": "ALIBABA_TOKEN_PLAN_API_KEY",
                    "prompt": "Alibaba Model Studio Token Plan API key (optional if you use DashScope PAYG)",
                    "url": "https://bailian.console.aliyun.com/?apiKey=1",
                },
            ],
            # NOT env_vars: the hermes tools picker prompts every env_vars entry as
            # mandatory. hg_image.py's availability gate merges both lists.
            "optional_env_vars": [
                {
                    "key": "ALIBABA_API_KEY",
                    "prompt": "Dedicated workspace / OpenAI-compatible endpoint key (requires ALIBABA_BASE_URL)",
                    "url": "",
                },
            ],
        }

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = "landscape",
        *,
        image_url: Optional[str] = None,
        reference_image_urls: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        # The ABC contract says never raise: a plumbing bug must surface as an
        # error_response the LLM can explain, not a traceback in the tool layer.
        try:
            return self._generate(
                prompt, aspect_ratio,
                image_url=image_url, reference_image_urls=reference_image_urls, **kwargs)
        except Exception as exc:  # noqa: BLE001
            return error_response(
                error=f"{type(exc).__name__}: {exc}",
                error_type="provider_exception",
                provider=PROVIDER_ID,
                model=str(kwargs.get("model") or ""),
                prompt=(prompt or "").strip(),
                aspect_ratio=resolve_aspect_ratio(aspect_ratio),
            )

    def _plan_attempts(self, kwargs: Dict[str, Any]):
        """(plan, (api_key, base_url)) attempts in order. An ``api_key`` kwarg pins a
        synthetic one-plan attempt — deliberate, because Hermes prefers ``~/.hermes/.env``
        over the process env, so writing an env var for a one-off key would be shadowed."""
        pinned = kwargs.get("api_key")
        if isinstance(pinned, str) and pinned.strip():
            base = ((kwargs.get("base_url") if isinstance(kwargs.get("base_url"), str) else "")
                    or _TOKEN_INTL).strip().rstrip("/")
            yield Plan("explicit", (), base, verified_models=True), (pinned.strip(), base)
            return
        for plan in _candidate_plans():
            creds = _resolve_plan_credentials(plan)
            if creds:
                yield plan, creds

    def _generate(self, prompt, aspect_ratio, *, image_url, reference_image_urls, **kwargs):
        prompt = (prompt or "").strip()
        aspect_ratio = resolve_aspect_ratio(aspect_ratio)
        if not prompt:
            return error_response(
                error="Prompt is required and must be a non-empty string",
                error_type="invalid_input", provider=PROVIDER_ID,
                prompt="", aspect_ratio=aspect_ratio)
        model = self._resolve_model(kwargs.get("model"))
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        sources = collect_source_images(image_url, reference_image_urls, limit=MAX_REFERENCES)
        skipped = 0
        for src in sources:
            part = _image_part(src)
            if part is None:
                skipped += 1
            else:
                content.append(part)
        if sources and len(content) == 1:
            # All named references unreadable: refuse rather than fake brand fidelity with a
            # text-to-image render.
            return error_response(
                error=f"all {len(sources)} reference image(s) could not be read",
                error_type="io_error", provider=PROVIDER_ID,
                model=model, prompt=prompt, aspect_ratio=aspect_ratio)
        notes: List[str] = []
        if skipped:
            notes.append(f"{skipped} reference image(s) skipped (unreadable)")
        if len(content) > 1:
            notes.append("wan ignores size when reference images are supplied")
        modality = "image" if len(content) > 1 else "text"

        attempts = list(self._plan_attempts(kwargs))
        if not attempts:
            return error_response(
                error="No usable Alibaba credentials: set one of "
                      "ALIBABA_TOKEN_PLAN_API_KEY / ALIBABA_TOKEN_PLAN_CN_API_KEY / DASHSCOPE_API_KEY",
                error_type="missing_api_key", provider=PROVIDER_ID,
                model=model, prompt=prompt, aspect_ratio=aspect_ratio)

        endpoint = self._resolve_endpoint()
        is_images = "images/generations" in endpoint
        tried: List[str] = []
        last_failure: Optional[HttpFailure] = None
        last_plan: Optional[Plan] = None
        for plan, (api_key, base_url) in attempts:
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            if is_images and len(content) > 1:
                return error_response(
                    error="reference images are not supported on the /images/generations "
                          "surface (set ALIBABA_IMAGE_ENDPOINT=/chat/completions, or omit "
                          "references)",
                    error_type="modality_unsupported", provider=PROVIDER_ID,
                    model=model, prompt=prompt, aspect_ratio=aspect_ratio)
            payload: Dict[str, Any] = (
                {"model": model, "prompt": prompt, "n": 1,
                 "size": OPENAI_SIZES.get(aspect_ratio, OPENAI_SIZES["square"])}
                if is_images else
                {"model": model, "messages": [{"role": "user", "content": content}],
                 "size": _SIZES.get(aspect_ratio, _SIZES["landscape"])}
            )
            body, failure = post_json(
                f"{base_url}{endpoint}", headers=headers, payload=payload,
                timeout=(20.0, 300.0), label=f"Alibaba {plan.profile}")
            if failure is None:
                return self._success(body, plan=plan, model=model, prompt=prompt,
                                     aspect_ratio=aspect_ratio, modality=modality,
                                     notes=notes, tried=tried + [plan.profile])
            tried.append(plan.profile)
            last_failure, last_plan = failure, plan
            if not _should_advance(plan, failure):
                return self._failure(failure, plan=plan, model=model, prompt=prompt,
                                     aspect_ratio=aspect_ratio, tried=tried)
        return self._failure(last_failure, plan=last_plan, model=model, prompt=prompt,
                             aspect_ratio=aspect_ratio, tried=tried, exhausted=True)

    def _success(self, body, *, plan, model, prompt, aspect_ratio, modality, notes, tried):
        image_ref, b64 = _extract_image(body)
        if image_ref is None and b64 is None:
            return error_response(
                error=f"no image in response: {_body_excerpt(body)}",
                error_type="empty_response", provider=PROVIDER_ID,
                model=model, prompt=prompt, aspect_ratio=aspect_ratio)
        extra: Dict[str, Any] = {"plan": plan.profile, "plans_tried": tried}
        if notes:
            extra["note"] = "; ".join(notes)
        debug = (body.get("output") or {}).get("debug_info") if isinstance(body, dict) else None
        if isinstance(debug, list) and debug and isinstance(debug[0], dict):
            d = debug[0]
            for src, dst in (("actual_seed", "seed"), ("output_W", "width"), ("output_H", "height")):
                if d.get(src) is not None:
                    extra[dst] = d[src]
        image, err = materialize_image(
            b64, image_ref,
            prefix=f"alibaba_{plan.profile}", label=f"Alibaba {plan.profile}",
            provider=PROVIDER_ID, model=model, prompt=prompt, aspect=aspect_ratio)
        if err:
            return err
        return success_response(
            image=image,
            model=(body.get("model") if isinstance(body, dict) else None) or model,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            provider=PROVIDER_ID,
            modality=modality,
            extra=extra,
        )

    def _failure(self, failure, *, plan, model, prompt, aspect_ratio, tried, exhausted=False):
        error = failure.error
        if exhausted:
            error = f"all Alibaba logins failed — tried {', '.join(tried)}; last: {failure.error}"
        if plan is not None and not plan.verified_models and _is_model_not_found(failure):
            error += (f" — model '{model}' may not exist on {plan.profile}; "
                      f"check the catalog with GET {plan.base_default}/models "
                      f"or pin one with ALIBABA_IMAGE_MODEL")
        return error_response(
            error=error, error_type=failure.error_type, provider=PROVIDER_ID,
            model=model, prompt=prompt, aspect_ratio=aspect_ratio)
