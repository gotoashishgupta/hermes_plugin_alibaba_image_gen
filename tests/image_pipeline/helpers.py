"""Named helpers for the image_pipeline suite (deliberately NOT in conftest.py —
a second flat conftest would shadow tests/conftest.py for the sibling suites'
``from conftest import ...`` and break them during full-repo collection)."""

from __future__ import annotations

import base64
import struct
import zlib

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBg"
    "AAAABgAHwPh8sQAAAABJRU5ErkJggg==")


def make_png(width: int, height: int) -> bytes:
    """Minimal but header-valid PNG (the reader only parses IHDR)."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"\x00" * (width * height * 3 + height)))
            + chunk(b"IEND", b""))


def png_data_uri(width: int, height: int) -> str:
    return "data:image/png;base64," + base64.b64encode(make_png(width, height)).decode()


def http_failure(kind, status=0, message="", error_type="api_error", label="x"):
    from imagegen.http import HttpFailure
    if kind == "http":
        return HttpFailure("http", f"{label} image generation failed ({status}): {message}",
                           error_type, status=status, message=message)
    if kind == "timeout":
        return HttpFailure("timeout", f"{label} image generation timed out", "timeout")
    return HttpFailure("connection", f"{label} connection error: refused", "connection_error")


# ---- response body builders (shapes mirrored from the plugin suite + live docs) ----

def token_plan_body(image="https://dashscope-result.example/x.png", debug=True):
    body = {
        "output": {"choices": [{"message": {"content": [
            {"type": "text", "text": "Here you go!"},
            {"type": "image", "image": image},
        ]}}]},
        "model": "wan2.7-image",
    }
    if debug:
        body["output"]["debug_info"] = [{"actual_seed": 1234, "output_W": 1280,
                                         "output_H": 720}]
    return body


def openai_b64_body(b64=None):
    return {"data": [{"b64_json": b64 or base64.b64encode(PNG_1PX).decode()}]}


def chat_images_body(url="https://example.org/img.png"):
    return {"choices": [{"message": {"images": [{"image_url": {"url": url}}]}}]}


def fal_queue_submit(request_id="req-1"):
    return {"request_id": request_id,
            "status_url": f"https://queue.fal.run/fal-ai/requests/{request_id}/status",
            "response_url": f"https://queue.fal.run/fal-ai/requests/{request_id}"}


def fal_result(url="https://v3.fal.media/files/x/cat.png"):
    return {"images": [{"url": url, "width": 1024, "height": 1024,
                        "content_type": "image/png", "file_name": "cat.png"}],
            "seed": 777, "tasks": []}


def gemini_body(png_bytes=None, mime="image/png"):
    data = base64.b64encode(png_bytes or PNG_1PX).decode()
    return {"candidates": [{"content": {"parts": [
        {"text": "Sure!"}, {"inlineData": {"mimeType": mime, "data": data}}]}}]}
