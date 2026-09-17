"""Stdlib-only HTTP transport for the standalone adapters.

Mirrors the failure classification of Hermes' ``plugins/image_gen/_common.py::post_json``
exactly (``kind`` ∈ http / timeout / connection / invalid_json; the same ``error_type``
strings) so fallback logic is uniform across all routes. ``requests`` stays confined to
Hermes mode (its venv ships it); a bare ``python3`` everywhere else gets this.
"""

from __future__ import annotations

import io
import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

DEFAULT_UA = "image-pipeline-hg/1.0 (stdlib urllib)"


@dataclass
class HttpFailure:
    """One failed request. ``kind`` ∈ http / timeout / connection / invalid_json;
    ``status`` / ``body`` set for ``http`` only."""
    kind: str
    error: str
    error_type: str
    status: int = 0
    message: str = ""
    body: Any = None


def http_error_message(status: int, raw_body: bytes) -> str:
    """``error.message`` from an HTTP error body, else its first 300 chars —
    the standalone twin of ``_common.requests_error_message``."""
    text = raw_body.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
        err = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(err, str) and err:
            return err
    except Exception:  # noqa: BLE001 - non-JSON body
        pass
    return text[:300]


def request_json(
    method: str, url: str, *, headers: Dict[str, str],
    payload: Optional[dict] = None, raw_body: Optional[bytes] = None,
    content_type: Optional[str] = None, timeout: Any = (20.0, 300.0), label: str = "",
) -> Tuple[Optional[Any], Optional[HttpFailure]]:
    """POST/GET → ``(json_body, None)`` or ``(None, HttpFailure)``. Never raises.
    ``timeout`` is ``(connect, read)``; urllib applies it per socket op (read dominates)."""
    connect, read = timeout if isinstance(timeout, tuple) else (timeout, timeout)
    hdrs = {"User-Agent": DEFAULT_UA, **headers}
    data: Optional[bytes] = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    elif raw_body is not None:
        data = raw_body
        if content_type:
            hdrs["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=read) as resp:
            body_bytes = resp.read()
    except urllib.error.HTTPError as exc:  # 4xx/5xx — has .code/.read()
        try:
            raw = exc.read()
        except Exception:  # noqa: BLE001
            raw = b""
        message = http_error_message(exc.code, raw)
        return None, HttpFailure(
            "http",
            f"{label} image generation failed ({exc.code}): {message}",
            "api_error", status=exc.code, message=message,
            body=_maybe_json(raw))
    except socket.timeout:
        return None, HttpFailure("timeout", f"{label} image generation timed out ({int(read)}s)", "timeout")
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, socket.timeout):
            return None, HttpFailure("timeout", f"{label} image generation timed out ({int(read)}s)", "timeout")
        return None, HttpFailure("connection", f"{label} connection error: {exc.reason}", "connection_error")
    except (ConnectionError, OSError) as exc:  # noqa: BLE001
        return None, HttpFailure("connection", f"{label} connection error: {exc}", "connection_error")
    try:
        return json.loads(body_bytes.decode("utf-8", errors="replace")), None
    except Exception as exc:  # noqa: BLE001
        return None, HttpFailure(
            "invalid_json", f"{label} returned invalid JSON: {exc}", "invalid_response", message=str(exc))


def _maybe_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return None


def download(url: str, dest: Path, *, timeout: float = 60.0, max_bytes: int = 25 * 1024 * 1024) -> Path:
    """Fetch ``url`` → ``dest`` (raises IOError/URLError on failure; caller classifies)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        length = resp.headers.get("Content-Length")
        if length and int(length) > max_bytes:
            raise IOError(f"remote image too large: {length} bytes")
        buf = io.BytesIO()
        total = 0
        while chunk := resp.read(64 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise IOError(f"remote image exceeded {max_bytes} bytes")
            buf.write(chunk)
    dest.write_bytes(buf.getvalue())
    return dest


def encode_multipart(fields: Dict[str, str], files: list) -> Tuple[bytes, str]:
    """``files``: list of ``(field_name, filename, bytes, mime)``. Returns (body, content-type-with-boundary)."""
    import uuid
    boundary = f"----hgimage-{uuid.uuid4().hex}"
    out = io.BytesIO()
    for key, value in fields.items():
        out.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode())
    for fname, filename, blob, mime in files:
        out.write(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{fname}\"; "
            f"filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n".encode())
        out.write(blob)
        out.write(b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"
