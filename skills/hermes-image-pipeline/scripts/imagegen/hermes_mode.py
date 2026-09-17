"""Everything Hermes-aware lives here — and nowhere else in the package.

This module's imports of ``hermes_cli`` / ``agent.*`` happen strictly INSIDE functions
against a discovered repo checkout (the package import-purity test enforces that no
other module touches hermes). Driving Hermes is best-effort by design: the script
must never hard-fail because Hermes is absent or its deps are unloadable — routing
falls through to the standalone adapters instead.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

from .adapters import measure_or_meta
from .envelope import GenRequest, GenResult

DEFAULT_HERMES_REPO = Path.home() / ".hermes" / "hermes-agent"
DEFAULT_HERMES_HOME = Path.home() / ".hermes"
_REPO_MARKER = ("agent", "image_gen_registry.py")


def hermes_repo() -> Optional[Path]:
    """Repo root if discoverable ($HERMES_AGENT_REPO or ~/.hermes/hermes-agent,
    validated by the marker file), else None. Never raises."""
    override = os.environ.get("HERMES_AGENT_REPO")
    repo = Path(override) if override else DEFAULT_HERMES_REPO
    return repo if (repo / Path(*_REPO_MARKER)).is_file() else None


def venv_python(repo: Path) -> Optional[Path]:
    """Hermes' venv interpreter (has hermes' deps incl. requests), if present and
    executable. Override with HERMES_VENV_PY for odd layouts."""
    override = os.environ.get("HERMES_VENV_PY")
    cand = Path(override) if override else repo / "venv" / "bin" / "python"
    return cand if cand.is_file() and os.access(cand, os.X_OK) else None


def load_env(home: Optional[Path] = None) -> int:
    """Load ~/.hermes/.env into os.environ without clobbering what's already set.

    Hermes does this at CLI startup; a bare script driving its providers (or the
    standalone alibaba adapter, whose ladder reads the same vars) has to do it
    itself or every provider reports unavailable.
    """
    env_file = (home or DEFAULT_HERMES_HOME) / ".env"
    if not env_file.is_file():
        return 0
    loaded = 0
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def hermes_importable(repo: Path) -> bool:
    """Can THIS interpreter drive Hermes' provider machinery? Cheap probe — on a
    bare python3 (no pyyaml etc.) this fails and routing re-execs or goes standalone."""
    path = str(repo)
    inserted = path not in sys.path
    if inserted:
        sys.path.insert(0, path)
    try:
        import hermes_cli.plugins  # noqa: F401
        import agent.image_gen_registry  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 - ImportError, SyntaxError in a dev checkout, ...
        return False
    finally:
        if inserted:
            try:
                sys.path.remove(path)
            except ValueError:
                pass


def registry_providers(repo: Path) -> dict:
    """Hermes' registered image_gen providers keyed by name. Uses the PUBLIC
    module-level API bound by ``_registry.export(globals())`` — the registry's
    private ``_registry.merged()`` map is never touched.

    Raises ImportError/RuntimeError on load failure; callers degrade, never abort.
    """
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    os.environ.setdefault("HERMES_HOME", str(DEFAULT_HERMES_HOME))
    load_env()

    from hermes_cli import plugins as _plugins
    _plugins.discover_plugins()

    from agent import image_gen_registry as registry
    return {p.name: p for p in registry.list_providers()}


def _is_available(prov) -> bool:
    from agent.provider_registry import is_available_safe
    return bool(is_available_safe(prov, logging.getLogger("imagegen"), "imagegen %s"))


def _model_ids(prov) -> list:
    """Model ids for a registry provider; catalog shape varies (list of dicts, or dict)."""
    try:
        models = prov.list_models()
    except Exception:  # noqa: BLE001
        return []
    if isinstance(models, dict):
        return [str(k) for k in models]
    out = []
    for entry in models or []:
        if isinstance(entry, dict) and entry.get("id"):
            out.append(str(entry["id"]))
        elif isinstance(entry, str):
            out.append(entry)
    return out


def _key_env_vars(prov) -> list:
    """Env vars a registry provider authenticates with, per its own setup schema.
    Includes ``optional_env_vars`` — Hermes' picker treats ``env_vars`` as mandatory,
    so providers park optional keys (e.g. alibaba's custom workspace pair) there."""
    try:
        schema = prov.get_setup_schema() or {}
        return [str(entry["key"])
                for group in ("env_vars", "optional_env_vars")
                for entry in schema.get(group) or []
                if entry.get("key")]
    except Exception:  # noqa: BLE001
        return []


class HermesBackend:
    """A provider from Hermes' image_gen registry, exposed through the same
    backend interface the standalone adapters satisfy."""

    kind = "registry"
    route = "hermes-registry"

    def __init__(self, name: str, prov, repo: Path):
        self.name = name
        self._prov = prov
        self._repo = repo

    def available(self, req: Optional[GenRequest] = None) -> bool:
        # `is_available()` alone is not enough: Hermes' credential ladder falls back
        # sideways (e.g. it hands `openrouter` the OPENAI_API_KEY when OPENROUTER_API_KEY
        # is unset) and such a provider would look available but 401 on first use. A
        # provider that declares key env vars must actually have at least one of them
        # set; OAuth providers (openai-codex, nous) declare none and rely on is_available().
        vars = _key_env_vars(self._prov)
        if req and req.api_key and not vars:
            return _is_available(self._prov)  # OAuth backend + pinned key: registry decides
        if vars and not any((os.environ.get(v) or "").strip() for v in vars):
            return False
        return _is_available(self._prov)

    @property
    def key_envs(self) -> list:
        return _key_env_vars(self._prov)

    def supports_references(self, model: Optional[str] = None) -> bool:
        try:
            caps = self._prov.capabilities() or {}
        except Exception:  # noqa: BLE001
            return False
        modalities = caps.get("modalities") or []
        try:
            max_refs = int(caps.get("max_reference_images") or 0)
        except (TypeError, ValueError):
            max_refs = 0
        return "image" in modalities and max_refs > 0

    def default_model(self) -> Optional[str]:
        try:
            return self._prov.default_model()
        except Exception:  # noqa: BLE001
            return None

    def models(self) -> list:
        return _model_ids(self._prov)

    def generate(self, req: GenRequest) -> GenResult:
        kwargs: dict = {}
        if req.model:
            kwargs["model"] = req.model
        if req.references:
            kwargs["reference_image_urls"] = list(req.references)
        if req.api_key:
            kwargs["api_key"] = req.api_key
        if req.base_url:
            kwargs["base_url"] = req.base_url
        if req.timeout:
            pass  # registry providers own their timeouts; nothing to forward
        try:
            result = self._prov.generate(req.prompt, req.aspect or "landscape", **kwargs)
        except Exception as exc:  # noqa: BLE001 - ABC discipline: never raise to the CLI
            return GenResult(success=False, provider=self.name, route=self.route,
                             model=req.model or "", error=f"{type(exc).__name__}: {exc}",
                             error_type="provider_exception")
        model = result.get("model", req.model) or ""
        if not result.get("success"):
            return GenResult(success=False, provider=self.name, model=model, route=self.route,
                             prompt=req.prompt, references=req.references,
                             error=result.get("error"), error_type=result.get("error_type"))
        notes = []
        if req.size:
            notes.append(f"--size requested but the {self.name} provider runs through Hermes' "
                         f"registry, which is aspect-only; delivered size is the provider's "
                         f"{result.get('aspect_ratio') or req.aspect} default")
        if isinstance(result.get("note"), str) and result["note"]:
            notes.append(result["note"])
        out = GenResult(
            success=True, provider=self.name, model=model, route=self.route,
            image=result.get("image"), prompt=req.prompt, references=req.references,
            aspect=result.get("aspect_ratio") or req.aspect or "landscape",
            notes=notes,
            credential_source="cli" if req.api_key else "",
            size_requested=f"{req.size[0]}x{req.size[1]}" if req.size else None)
        # Plan-aware providers (alibaba) report which login answered + render metadata.
        for k in ("plan", "seed"):
            if result.get(k) is not None:
                setattr(out, k, result[k])
        for k in ("width", "height"):
            if result.get(k) is not None:
                setattr(out, k, result[k])
        return measure_or_meta(out)
