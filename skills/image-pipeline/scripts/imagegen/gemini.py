"""Google Gemini image adapter (generateContent REST, v1beta):
  POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
  header x-goog-api-key; body parts = [text] + inlineData refs;
  generationConfig.responseModalities ["IMAGE"], imageConfig.aspectRatio (10-ratio enum).
  Result: candidates[0].content.parts[].inlineData.data (base64).

2026 caveat (open reports, e.g. googleapis/js-genai#1461): some endpoints silently
ignore imageConfig — so the adapter MEASURES the decoded image (measure_or_meta)
and notes any >5% ratio deviation. Never trust the parameter over the pixels.
"""

from __future__ import annotations

import os
from typing import List, Optional

from .adapters import (ProviderAdapter, final_aspect, measure_or_meta, nearest_by_ratio,
                       save_b64)
from .envelope import GenRequest, GenResult
from .http import request_json
from .refs import to_data_uri

BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-3.1-flash-image"          # Nano Banana 2
MODELS = [DEFAULT_MODEL, "gemini-3-pro-image", "gemini-2.5-flash-image"]
RATIOS = [("1:1", 1.0), ("2:3", 2 / 3), ("3:2", 1.5), ("3:4", 0.75), ("4:3", 4 / 3),
          ("4:5", 0.8), ("5:4", 1.25), ("9:16", 9 / 16), ("16:9", 16 / 9), ("21:9", 21 / 9)]
_ASPECT_TO_RATIO = {"landscape": "16:9", "square": "1:1", "portrait": "9:16"}
_MAX_REFS = 14


class GeminiAdapter(ProviderAdapter):
    name = "gemini"
    display = "Google Gemini image (Nano Banana family, generateContent)"
    priority = 30
    key_envs = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
    supports_endpoint = False
    max_reference_images = _MAX_REFS

    def default_model(self) -> Optional[str]:
        return (os.environ.get("GEMINI_IMAGE_MODEL") or "").strip() or DEFAULT_MODEL

    def models(self) -> List[str]:
        return list(MODELS)

    def generate(self, req: GenRequest) -> GenResult:
        return self._guard(req, lambda: self._generate(req))

    def _generate(self, req: GenRequest) -> GenResult:
        prompt = (req.prompt or "").strip()
        if not prompt:
            return self._fail(req, "Prompt is required and must be a non-empty string", "invalid_input")
        key = self.key(req)
        if not key:
            return self._fail(req, "No API key: set GEMINI_API_KEY (or GOOGLE_API_KEY) "
                                   "or pass --api-key", "missing_api_key")
        if req.base_url:
            return self._fail(req, "gemini does not accept --base-url (fixed endpoint)",
                              "invalid_input")
        model = (req.model or self.default_model()).strip()
        aspect = final_aspect(req)

        parts = [{"text": prompt}]
        if req.references:
            if len(req.references) > _MAX_REFS:
                return self._fail(req, f"{len(req.references)} refs exceed gemini cap {_MAX_REFS}",
                                  "modality_unsupported")
            for src in req.references:
                uri, err = to_data_uri(src)
                if uri is None:
                    return self._fail(req, f"reference image unreadable: {err}", "io_error")
                if not uri.startswith("data:"):
                    return self._fail(req, "gemini generateContent needs local/inline refs "
                                           "(remote URLs unsupported inline)", "modality_unsupported")
                head, _, b64 = uri.partition(",")
                mime = head[5:].split(";")[0] or "image/png"
                parts.append({"inlineData": {"mimeType": mime, "data": b64}})

        ratio, size_note = self._pick_ratio(req, aspect)
        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {"aspectRatio": ratio},
            },
        }
        url = f"{BASE}/models/{model}:generateContent"
        body, failure = request_json("POST", url,
                                     headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                                     payload=payload, timeout=(20.0, req.timeout), label="Gemini")
        if failure is not None:
            et = failure.error_type
            if failure.status in (401, 403):
                et = "auth_error"
            return self._fail(req, failure.error, et)
        blocks = _extract_inline_parts(body)
        if not blocks:
            return self._fail(req, f"no image part in response: {str(body)[:300]}", "empty_response")
        data_b64, mime = blocks[0]
        ext = {"image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "png")
        try:
            path = save_b64(data_b64, "gemini", ext=ext)
        except Exception as exc:  # noqa: BLE001
            return self._fail(req, f"Could not save image locally: {exc}", "io_error")
        result = GenResult(
            success=True, provider=self.name, model=model, image=str(path.resolve()),
            prompt=req.prompt, references=req.references, aspect=aspect,
            notes=[n for n in (size_note,
                               f"{len(blocks)} images returned; first saved"
                               if len(blocks) > 1 else None) if n],
            size_requested=f"{req.size[0]}x{req.size[1]}" if req.size else None,
            credential_source="cli" if req.api_key else "env",
            route="direct" if req.api_key else "standalone")
        return measure_or_meta(result)

    def _pick_ratio(self, req: GenRequest, aspect: str):
        if not req.size:
            return _ASPECT_TO_RATIO.get(aspect, "1:1"), None
        w, h = req.size
        chosen = nearest_by_ratio(w, h, RATIOS)
        same = abs(_pr(chosen) - w / h) < 1e-9
        note = None if same else (f"--size {w}x{h} snapped to ratio {chosen} "
                                  "(gemini serves ratios, not pixels; crop/upscale deck-side)")
        return chosen, note


def _pr(s: str) -> float:
    w, _, h = s.partition(":")
    return float(w) / float(h)


def _extract_inline_parts(body) -> List[tuple]:
    """(b64, mime) for every inlineData part across candidates (finishReason STOP or not)."""
    out = []
    for cand in (body or {}).get("candidates") or []:
        content = cand.get("content") if isinstance(cand, dict) else None
        for part in (content or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data") if isinstance(part, dict) else None
            if isinstance(inline, dict) and inline.get("data"):
                out.append((str(inline["data"]), str(inline.get("mimeType")
                                                     or inline.get("mime_type") or "image/png")))
    return out
