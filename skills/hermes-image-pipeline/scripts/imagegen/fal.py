"""FAL.ai standalone adapter — queue + sync inference (fal docs, 2026):
  * auth header ``Authorization: Key $FAL_KEY``
  * queue:   POST https://queue.fal.run/{model_id} → {request_id, status_url, response_url};
             poll GET status_url (IN_QUEUE → IN_PROGRESS → COMPLETED) then GET response_url
  * sync:    POST https://fal.run/{model_id} (models whose defaults carry sync_mode)
  * result:  ``images[].url`` on ephemeral v3.fal.media links → ALWAYS download locally
  * edits:   ``<model>/edit`` surface with ``image_urls`` (data URIs inlined under cap)

Mini-catalog copied from Hermes' ``tools/image_generation_catalog.py`` (two size
styles: ``image_size`` presets/custom and ``aspect_ratio`` enum). Error bodies use
``detail`` — surfaced verbatim in the error string.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Tuple

from .adapters import (ProviderAdapter, final_aspect, measure_or_meta, nearest_by_ratio)
from .envelope import GenRequest, GenResult
from .http import download, request_json
from .refs import to_data_uri
from .alibaba import _cache_path

QUEUE_BASE = "https://queue.fal.run"
SYNC_BASE = "https://fal.run"
DEFAULT_MODEL = "fal-ai/flux-2/klein/9b"

_SIZE_PRESETS = {
    "square": "square_hd", "landscape": "landscape_16_9", "portrait": "portrait_16_9",
}
_RATIO_TABLE = [
    ("1:1", 1.0), ("4:3", 4 / 3), ("3:2", 1.5), ("16:9", 16 / 9), ("21:9", 21 / 9),
    ("3:4", 0.75), ("2:3", 2 / 3), ("9:16", 9 / 16), ("1:2", 0.5),
]

# size_style: "image_size" (presets or {"width":W,"height":H}) | "aspect_ratio" (enum)
FAL_MODELS: Dict[str, Dict[str, Any]] = {
    "fal-ai/flux-2/klein/9b": {"display": "FLUX 2 Klein 9B", "size_style": "image_size",
                               "edit_endpoint": "fal-ai/flux-2/klein/9b/edit", "max_refs": 9,
                               "defaults": {"num_inference_steps": 4, "output_format": "png",
                                            "enable_safety_checker": False}},
    "fal-ai/flux-2-pro": {"display": "FLUX 2 Pro", "size_style": "image_size", "sync": True,
                          "edit_endpoint": "fal-ai/flux-2-pro/edit", "max_refs": 9,
                          "defaults": {"num_inference_steps": 50, "guidance_scale": 4.5,
                                       "num_images": 1, "output_format": "png",
                                       "enable_safety_checker": False, "sync_mode": True}},
    "fal-ai/z-image/turbo": {"display": "Z-Image Turbo", "size_style": "image_size", "max_refs": 0,
                             "defaults": {"num_inference_steps": 8, "num_images": 1,
                                          "output_format": "png", "enable_safety_checker": False,
                                          "enable_prompt_expansion": False}},
    "fal-ai/nano-banana-pro": {"display": "Nano Banana Pro", "size_style": "aspect_ratio",
                               "edit_endpoint": "fal-ai/nano-banana-pro/edit", "max_refs": 2,
                               "defaults": {"num_images": 1, "output_format": "png",
                                            "safety_tolerance": "5", "resolution": "1K"}},
    "fal-ai/nano-banana-2": {"display": "Nano Banana 2", "size_style": "aspect_ratio",
                             "edit_endpoint": "fal-ai/nano-banana-2/edit", "max_refs": 14,
                             "defaults": {"num_images": 1, "output_format": "png",
                                          "safety_tolerance": "4", "resolution": "1K",
                                          "limit_generations": True}},
}


class FalAdapter(ProviderAdapter):
    name = "fal"
    display = "FAL.ai (queue + sync inference, FLUX / nano-banana family)"
    priority = 50
    key_envs = ("FAL_KEY",)
    supports_endpoint = False
    max_reference_images = 9

    def default_model(self) -> Optional[str]:
        return (os.environ.get("FAL_IMAGE_MODEL") or "").strip() or DEFAULT_MODEL

    def models(self) -> List[str]:
        return list(FAL_MODELS)

    def supports_references(self, model: Optional[str] = None) -> bool:
        meta = FAL_MODELS.get(model or self.default_model() or "", {})
        return bool(meta.get("edit_endpoint"))

    def generate(self, req: GenRequest) -> GenResult:
        return self._guard(req, lambda: self._generate(req))

    def _generate(self, req: GenRequest) -> GenResult:
        prompt = (req.prompt or "").strip()
        if not prompt:
            return self._fail(req, "Prompt is required and must be a non-empty string", "invalid_input")
        key = self.key(req)
        if not key:
            return self._fail(req, "No API key: set FAL_KEY or pass --api-key", "missing_api_key")
        model = (req.model or self.default_model()).strip()
        meta = FAL_MODELS.get(model, {"size_style": "image_size", "max_refs": 0, "defaults": {}})
        aspect = final_aspect(req)
        headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}

        payload: Dict[str, Any] = dict(meta.get("defaults") or {})
        payload["prompt"] = prompt
        size_note = None
        if req.size:
            w, h = req.size
            if meta["size_style"] == "image_size":
                payload["image_size"] = {"width": w, "height": h}
                size_note = f"exact {w}x{h} requested via fal custom image_size"
            else:
                ratio, n = self._nearest_ratio(w, h)
                payload["aspect_ratio"] = ratio
                size_note = (f"--size {w}x{h} snapped to ratio {ratio}" + (f" ({n})" if n else ""))
        else:
            if meta["size_style"] == "image_size":
                payload["image_size"] = _SIZE_PRESETS.get(aspect, "square_hd")
            else:
                payload["aspect_ratio"] = {"landscape": "16:9", "square": "1:1",
                                           "portrait": "9:16"}.get(aspect, "1:1")

        endpoint_model = model
        if req.references:
            edit_ep = meta.get("edit_endpoint")
            if not edit_ep:
                return self._fail(req, f"{model} has no image-to-image edit endpoint; use an "
                                       f"edit-capable model or drop --ref", "modality_unsupported")
            if len(req.references) > int(meta.get("max_refs") or 0):
                return self._fail(req, f"{len(req.references)} refs exceed {model} max "
                                       f"{meta.get('max_refs')}", "modality_unsupported")
            urls = []
            for src in req.references:
                uri, err = to_data_uri(src, cap=10 * 1024 * 1024)
                if uri is None:
                    return self._fail(req, f"reference image unreadable: {err}", "io_error")
                urls.append(uri)
            payload["image_urls"] = urls
            endpoint_model = edit_ep

        url = f"{SYNC_BASE if meta.get('sync') else QUEUE_BASE}/{endpoint_model}"
        body, failure = request_json("POST", url, headers=headers, payload=payload,
                                     timeout=(20.0, min(req.timeout, 120.0)), label="FAL")
        if failure is not None:
            return self._fail(req, failure.error, failure.error_type)
        if meta.get("sync"):
            return self._materialize(body, req, model=model, aspect=aspect, size_note=size_note)
        return self._poll(body, req, headers, model=model, aspect=aspect, size_note=size_note)

    def _poll(self, submit: dict, req: GenRequest, headers: dict, *, model, aspect,
              size_note) -> GenResult:
        status_url = (submit or {}).get("status_url")
        response_url = (submit or {}).get("response_url")
        if not status_url or not response_url:
            return self._fail(req, f"fal queue submit missing urls: {str(submit)[:300]}",
                              "invalid_response")
        deadline = time.monotonic() + req.timeout
        interval = 1.0
        while time.monotonic() < deadline:
            status, failure = request_json("GET", status_url, headers=headers,
                                           timeout=(10.0, 30.0), label="FAL status")
            if failure is not None:
                return self._fail(req, failure.error, failure.error_type)
            state = (status or {}).get("status")
            if state == "COMPLETED":
                result_body, failure = request_json("GET", response_url, headers=headers,
                                                    timeout=(20.0, 60.0), label="FAL result")
                if failure is not None:
                    return self._fail(req, failure.error, failure.error_type)
                return self._materialize(result_body, req, model=model, aspect=aspect,
                                         size_note=size_note)
            if state in ("FAILED", "ERROR"):
                detail = (status or {}).get("logs") or status
                return self._fail(req, f"fal request failed: {str(detail)[:300]}", "api_error")
            qp = (status or {}).get("queue_position")
            if isinstance(qp, int) and qp > 100:
                interval = 5.0
            time.sleep(interval)
        return self._fail(req, f"FAL queue polling timed out ({int(req.timeout)}s)", "timeout")

    def _nearest_ratio(self, w: int, h: int) -> Tuple[str, Optional[str]]:
        chosen = nearest_by_ratio(w, h, _RATIO_TABLE)
        return chosen, None if _parse_ratio(chosen) == w / h else "model serves ratios, not pixels"

    def _materialize(self, body, req, *, model, aspect, size_note) -> GenResult:
        images = (body or {}).get("images")
        if not isinstance(images, list) or not images:
            return self._fail(req, f"no images in response: {str(body)[:300]}", "empty_response")
        first = images[0] if isinstance(images[0], dict) else {}
        url = first.get("url")
        if not url:
            return self._fail(req, "fal image entry has no url", "empty_response")
        try:
            path = download(url, _cache_path("fal", url), timeout=60.0)
        except Exception as exc:  # noqa: BLE001
            return self._fail(req, f"Could not download fal image: {exc}", "io_error")
        seed = body.get("seed")
        result = GenResult(
            success=True, provider=self.name, model=model, image=str(path.resolve()),
            prompt=req.prompt, references=req.references, aspect=aspect,
            notes=[size_note] if size_note else [],
            seed=int(seed) if seed is not None else None,
            width=_as_int(first.get("width")), height=_as_int(first.get("height")),
            size_requested=f"{req.size[0]}x{req.size[1]}" if req.size else None,
            credential_source="cli" if req.api_key else "env",
            route="direct" if req.api_key else "standalone")
        return measure_or_meta(result)


def _parse_ratio(s: str) -> float:
    w, _, h = s.partition(":")
    return float(w) / float(h)


def _as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
