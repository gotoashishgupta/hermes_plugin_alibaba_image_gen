"""OpenRouter standalone adapter — mirrors the working Hermes plugin's dual-surface
logic (~/.hermes/hermes-agent/plugins/image_gen/openrouter/__init__.py) in miniature.

Two live surfaces (verified against the plugin, which supersedes the June-2026
"unified /api/v1/images" announcement wording):
  * Dedicated Image API: ``POST {base}/images/generations`` — curated models below,
    exact ``aspect_ratio`` enum, refs as ``input_references: [{type:"image_url", image_url:{url}}]``.
  * Legacy chat: ``POST {base}/chat/completions`` with ``modalities:["image","text"]``
    + ``image_config.aspect_ratio`` — refs as ``image_url`` content parts (cap 3).

Outputs: images→``data[].b64_json|url``; chat→``choices[].message.images[].image_url.url``.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from .adapters import (ProviderAdapter, extract_image, final_aspect, measure_or_meta,
                       nearest_by_ratio, save_b64)
from .envelope import GenRequest, GenResult
from .http import download, request_json
from .refs import to_data_uri
from .alibaba import _cache_path

BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-5.4-image-2"
_FALLBACK_MODEL = "google/gemini-3-pro-image"
CHAT_ONLY = {DEFAULT_MODEL, _FALLBACK_MODEL}

# Curated exact-ratio sets per model family (copied from the plugin's tables).
GEMINI_RATIOS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
ASPECT_PREFERENCES: Dict[str, Tuple[str, ...]] = {
    "landscape": ("16:9", "3:2", "4:3", "5:4", "21:9"),
    "portrait": ("9:16", "2:3", "3:4", "4:5", "1:2"),
    "square": ("1:1",),
}
CHAT_RATIOS = {"landscape": "16:9", "square": "1:1", "portrait": "9:16"}

IMAGE_API_MODELS: Dict[str, Dict[str, object]] = {
    "google/gemini-3.1-flash-image": {"ratios": GEMINI_RATIOS, "max_refs": 14},
    "openai/gpt-image-2": {"ratios": ("1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16", "21:9"),
                           "max_refs": 16},
    "microsoft/mai-image-2.5": {"ratios": ("1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3"), "max_refs": 1},
    "krea/krea-2-medium": {"ratios": ("1:1", "4:3", "3:2", "16:9", "4:5", "2:3", "9:16"), "max_refs": 1},
    "qwen/qwen-image-3-pro": {"ratios": ("1:1", "2:1", "2:3", "3:2", "3:4", "4:1", "4:3", "4:5",
                                          "5:4", "9:16", "16:9"), "max_refs": 4},
}


class OpenRouterAdapter(ProviderAdapter):
    name = "openrouter"
    display = "OpenRouter (dedicated Image API + chat-completions image models)"
    priority = 10
    key_envs = ("OPENROUTER_API_KEY",)
    supports_endpoint = True
    max_reference_images = 14

    def default_model(self) -> Optional[str]:
        return (os.environ.get("OPENROUTER_IMAGE_MODEL") or "").strip() or DEFAULT_MODEL

    def models(self) -> List[str]:
        return [DEFAULT_MODEL, _FALLBACK_MODEL, *IMAGE_API_MODELS]

    def supports_references(self, model: Optional[str] = None) -> bool:
        return True

    def generate(self, req: GenRequest) -> GenResult:
        return self._guard(req, lambda: self._generate(req))

    def _generate(self, req: GenRequest) -> GenResult:
        prompt = (req.prompt or "").strip()
        if not prompt:
            return self._fail(req, "Prompt is required and must be a non-empty string", "invalid_input")
        key = self.key(req)
        if not key:
            return self._fail(req, f"No API key: set OPENROUTER_API_KEY or pass --api-key",
                              "missing_api_key")
        model = (req.model or self.default_model()).strip()
        base = (req.base_url or os.environ.get("OPENROUTER_BASE_URL") or BASE).rstrip("/")
        headers = {"Authorization": f"Bearer {key}",
                   "HTTP-Referer": "https://local.harness/image-pipeline",
                   "X-Title": "image-pipeline"}
        aspect = final_aspect(req)
        refs = list(req.references)
        if refs:
            inlined = []
            for src in refs:
                uri, err = to_data_uri(src)
                if uri is None:
                    return self._fail(req, f"reference image unreadable: {err}", "io_error")
                inlined.append(uri)
            refs = inlined

        use_images = (req.endpoint == "/images/generations") if req.endpoint \
            else (model in IMAGE_API_MODELS and model not in CHAT_ONLY)
        if use_images:
            meta = IMAGE_API_MODELS.get(model, {"ratios": (), "max_refs": 16})
            ratios = meta["ratios"] or ASPECT_PREFERENCES.get(aspect, ("1:1",))
            ratio, size_note = self._pick_ratio(req, aspect, ratios)
            if refs and len(refs) > int(meta["max_refs"]):
                return self._fail(req, f"{len(refs)} refs exceed {model} max_refs {meta['max_refs']}",
                                  "modality_unsupported")
            payload: dict = {"model": model, "prompt": prompt, "aspect_ratio": ratio, "n": 1}
            if refs:
                payload["input_references"] = [
                    {"type": "image_url", "image_url": {"url": u}} for u in refs]
            endpoint = req.endpoint or "/images/generations"
        else:
            ratio = CHAT_RATIOS.get(aspect, "16:9")
            size_note = None
            if req.size:
                size_note = (f"--size {req.size[0]}x{req.size[1]} mapped to {ratio} on the chat "
                             f"surface (exact pixels unsupported here)")
            content = [{"type": "text", "text": prompt}]
            content += [{"type": "image_url", "image_url": {"url": u}} for u in refs[:3]]
            payload = {"model": model, "messages": [{"role": "user", "content": content}],
                       "modalities": ["image", "text"],
                       "image_config": {"aspect_ratio": ratio}}
            endpoint = req.endpoint or "/chat/completions"

        body, failure = request_json("POST", f"{base}{endpoint}", headers=headers,
                                     payload=payload, timeout=(20.0, req.timeout), label="OpenRouter")
        if failure is not None:
            return self._fail(req, failure.error, failure.error_type)
        url, b64 = extract_image(body)
        if url is None and b64 is None:
            return self._fail(req, f"no image in response: {str(body)[:300]}", "empty_response")
        try:
            path = save_b64(b64, "openrouter") if b64 else \
                download(url, _cache_path("openrouter", url or ""), timeout=60.0)
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

    def _pick_ratio(self, req: GenRequest, aspect: str, ratios) -> Tuple[str, Optional[str]]:
        allowed = [r for r in ratios if r != "auto"]
        if req.size:
            w, h = req.size
            chosen = nearest_by_ratio(w, h, [(r, _ratio(r)) for r in allowed]) \
                if allowed else CHAT_RATIOS[aspect]
            note = None if _ratio(chosen) == w / h else \
                f"--size {w}x{h} snapped to ratio {chosen} (model serves ratios, not pixels)"
            return chosen, note
        for pref in ASPECT_PREFERENCES.get(aspect, ("1:1",)):
            if pref in allowed:
                return pref, None
        return (allowed[0] if allowed else "1:1"), None


def _ratio(s: str) -> float:
    w, _, h = s.partition(":")
    return float(w) / float(h)
