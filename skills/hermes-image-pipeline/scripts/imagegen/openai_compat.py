"""OpenAI / OpenAI-compatible adapter — also the GENERIC custom-endpoint adapter.

Surface: ``POST {base_url|https://api.openai.com/v1}{endpoint|/images/generations}``
with ``{model, prompt, n:1, size, quality?}`` (per official docs: gpt-image sizes are
exactly 1024x1024 | 1536x1024 | 1024x1536 — anything else snaps to nearest + note).
With ``--endpoint /chat/completions`` (or any path not containing "images") the chat
payload shape is used, which makes this cover OpenAI-compatible chat-image servers.
References → ``/images/edits`` multipart ``image[]`` (≤16) on the images surface;
on the chat surface, inlined as image content parts.

Custom providers (user decision: the explicit bypass): ``--provider <anything-unknown>
--api-key K --base-url U [--endpoint E]`` synthesizes an instance of this adapter named
after the provider — gpt-image-style gateways (which most are) just work.
"""

from __future__ import annotations

import os
from typing import List, Optional

from .adapters import (OPENAI_SIZES, ProviderAdapter, extract_image, final_aspect,
                       measure_or_meta, nearest_by_ratio, save_b64)
from .envelope import GenRequest, GenResult
from .http import download, encode_multipart, request_json
from .refs import to_data_uri
from .alibaba import _cache_path

DEFAULT_BASE = "https://api.openai.com/v1"
GPT_IMAGE_2_TIERS = {  # virtual ids → real api model + quality knob (= _common.GPT_IMAGE_2_TIERS)
    "gpt-image-2-low": ("gpt-image-2", "low"),
    "gpt-image-2-medium": ("gpt-image-2", "medium"),
    "gpt-image-2-high": ("gpt-image-2", "high"),
}
DEFAULT_MODEL = "gpt-image-2-medium"
MAX_EDITS_IMAGES = 16


class OpenAICompatAdapter(ProviderAdapter):
    name = "openai"
    display = "OpenAI / OpenAI-compatible (/images/generations or /chat/completions)"
    priority = 20
    key_envs = ("OPENAI_API_KEY",)
    supports_endpoint = True
    max_reference_images = MAX_EDITS_IMAGES

    def __init__(self, *, instance_name: Optional[str] = None,
                 key_env: Optional[str] = None, base_default: Optional[str] = None):
        super().__init__(instance_name=instance_name)
        if key_env:
            self.key_envs = (key_env,)
        self.base_default = (base_default or os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE).rstrip("/")

    def default_model(self) -> Optional[str]:
        return (os.environ.get("OPENAI_IMAGE_MODEL") or "").strip() or DEFAULT_MODEL

    def models(self) -> List[str]:
        return list(GPT_IMAGE_2_TIERS)

    def _base(self, req: GenRequest) -> str:
        return (req.base_url or self.base_default).rstrip("/")

    def _endpoint(self, req: GenRequest) -> str:
        ep = req.endpoint or "/images/generations"
        return ep if ep.startswith("/") else f"/{ep}"

    def generate(self, req: GenRequest) -> GenResult:
        return self._guard(req, lambda: self._generate(req))

    def _generate(self, req: GenRequest) -> GenResult:
        prompt = (req.prompt or "").strip()
        if not prompt:
            return self._fail(req, "Prompt is required and must be a non-empty string", "invalid_input")
        key = self.key(req)
        if not key:
            return self._fail(req, f"No API key: set {', '.join(self.key_envs)} or pass --api-key",
                              "missing_api_key")
        model = (req.model or self.default_model()).strip()
        api_model, quality = GPT_IMAGE_2_TIERS.get(model, (model, None))
        aspect = final_aspect(req)
        size, size_note = self._resolve_size(req, aspect)
        base, endpoint = self._base(req), self._endpoint(req)
        headers = {"Authorization": f"Bearer {key}"}

        if "images/generations" in endpoint:
            if req.references:
                return self._edits(req, base, headers, api_model, prompt, aspect, size, size_note)
            payload: dict = {"model": api_model, "prompt": prompt, "n": 1, "size": size}
            if quality:
                payload["quality"] = quality
            body, failure = request_json("POST", f"{base}{endpoint}", headers=headers,
                                         payload=payload, timeout=(20.0, req.timeout), label=self.name)
        elif "images/edits" in endpoint:
            return self._edits(req, base, headers, api_model, prompt, aspect, size, size_note)
        else:  # chat-style surface
            content = [{"type": "text", "text": prompt}]
            for src in req.references:
                uri, err = to_data_uri(src)
                if uri is None:
                    return self._fail(req, f"reference image unreadable: {err}", "io_error")
                content.append({"type": "image_url", "image_url": {"url": uri}})
            payload = {"model": api_model,
                       "messages": [{"role": "user", "content": content}], "size": size}
            body, failure = request_json("POST", f"{base}{endpoint}", headers=headers,
                                         payload=payload, timeout=(20.0, req.timeout), label=self.name)
        if failure is not None:
            return self._fail(req, failure.error, failure.error_type)
        return self._materialize(body, req, model=model, aspect=aspect, size_note=size_note)

    def _edits(self, req, base, headers, api_model, prompt, aspect, size, size_note) -> GenResult:
        if len(req.references) > MAX_EDITS_IMAGES:
            return self._fail(req, f"{len(req.references)} references exceed the edits cap "
                                   f"({MAX_EDITS_IMAGES})", "modality_unsupported")
        files = []
        import mimetypes
        from pathlib import Path
        for src in req.references:
            if str(src).startswith(("http://", "https://", "data:")):
                uri, err = to_data_uri(src)
                if uri is None:
                    return self._fail(req, f"reference image unreadable: {err}", "io_error")
                if uri.startswith("data:"):
                    head, _, b64 = uri.partition(",")
                    mime = head[5:].split(";")[0]
                    import base64 as _b64
                    blob = _b64.b64decode(b64)
                else:
                    from .http import download as _dl
                    import tempfile
                    tmp = Path(tempfile.mkdtemp()) / "ref.img"
                    _dl(uri, tmp, timeout=60.0)
                    mime = mimetypes.guess_type(uri)[0] or "image/png"
                    blob = tmp.read_bytes()
            else:
                mime = mimetypes.guess_type(str(src))[0] or "image/png"
                blob = Path(src).read_bytes()
            ext = "jpg" if "jpeg" in mime or "jpg" in mime else "png"
            files.append(("image[]", f"ref.{ext}", blob, mime))
        fields = {"model": api_model, "prompt": prompt}
        if size:
            fields["size"] = size
        raw_body, content_type = encode_multipart(fields, files)
        body, failure = request_json("POST", f"{base}/images/edits", headers=headers,
                                     raw_body=raw_body, content_type=content_type,
                                     timeout=(20.0, req.timeout), label=self.name)
        if failure is not None:
            return self._fail(req, failure.error, failure.error_type)
        return self._materialize(body, req, model=api_model, aspect=aspect, size_note=size_note)

    def _resolve_size(self, req: GenRequest, aspect: str):
        if not req.size:
            return OPENAI_SIZES.get(aspect, OPENAI_SIZES["square"]), None
        w, h = req.size
        wanted = nearest_by_ratio(w, h, [(k, float(r)) for k, r in {
            "1024x1024": 1.0, "1536x1024": 1.5, "1024x1536": 1 / 1.5}.items()])
        note = None if f"{w}x{h}" == wanted else \
            f"--size {w}x{h} snapped to {wanted} (gpt-image serves only 3 fixed sizes; crop/upscale deck-side)"
        return wanted, note

    def _materialize(self, body, req, *, model, aspect, size_note) -> GenResult:
        url, b64 = extract_image(body)
        if url is None and b64 is None:
            return self._fail(req, f"no image in response: {str(body)[:300]}", "empty_response")
        try:
            path = save_b64(b64, self.name) if b64 else \
                download(url, _cache_path(self.name, url or ""), timeout=60.0)
        except Exception as exc:  # noqa: BLE001
            return self._fail(req, f"Could not save image locally: {exc}", "io_error")
        result = GenResult(
            success=True, provider=self.name, model=model, image=str(path.resolve()),
            prompt=req.prompt, references=req.references, aspect=aspect,
            notes=[size_note] if size_note else [],
            size_requested=f"{req.size[0]}x{req.size[1]}" if req.size else None,
            credential_source="cli" if req.api_key else "env",
            route="direct" if req.api_key else "standalone")
        return measure_or_meta(result)
