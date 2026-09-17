"""OpenBao secret source for Hermes Agent.

Reads LLM provider API keys from OpenBao KV secrets engine via the ``bao`` CLI.
AppRole authentication bootstraps a short-lived token used for all fetches.
A background daemon thread polls for rotated values and updates ``os.environ``
directly so providers see new keys without restart.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional

logger = logging.getLogger(__name__)

_LOG_HANDLERS_READY = False


def _ensure_handlers() -> None:
    """Attach stderr + file handlers once so logs are visible.

    Hermes captures stderr; the file at ``~/.hermes/logs/openbao.log`` survives
    process exit. Level is DEBUG when ``HERMES_OPENBAO_DEBUG=1`` else INFO.
    Never logs secret values, tokens, or secret-ids (see _redact_argv).
    """
    global _LOG_HANDLERS_READY
    if _LOG_HANDLERS_READY:
        return
    level = logging.DEBUG if os.environ.get("HERMES_OPENBAO_DEBUG") == "1" else logging.INFO
    logger.setLevel(level)
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s %(levelname)s openbao: %(message)s")
        try:
            log_dir = Path.home() / ".hermes" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(str(log_dir / "openbao.log"))
            fh.setFormatter(fmt)
            fh.setLevel(level)
            logger.addHandler(fh)
        except OSError:
            pass
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        sh.setLevel(level)
        logger.addHandler(sh)
    _LOG_HANDLERS_READY = True


def _redact_argv(argv) -> list:
    """Redact credential-bearing args (role_id=/secret_id=) for safe logging."""
    redacted = []
    for a in argv:
        s = str(a)
        if s.startswith("secret_id=") or s.startswith("role_id="):
            redacted.append(s.split("=", 1)[0] + "=***")
        else:
            redacted.append(s)
    return redacted


def _resolve_bao_binary() -> str:
    """Prefer PATH lookup, fall back to the Homebrew default."""
    found = shutil.which("bao")
    return found or "/opt/homebrew/bin/bao"

# These come from the hermes-agent package which is always on sys.path
# at runtime. For testing outside that context, patch or mock instead.
try:
    from agent.secret_sources.base import (
        FetchResult,
        ErrorKind,
        SecretSource,
        is_valid_env_name,
        run_cli,
    )
except ImportError:
    # Testing outside hermes context — minimal stubs
    from dataclasses import dataclass, field
    from enum import Enum

    class ErrorKind(str, Enum):
        NOT_CONFIGURED = "not_configured"
        BINARY_MISSING = "binary_missing"
        AUTH_FAILED = "auth_failed"
        AUTH_EXPIRED = "auth_expired"
        REF_INVALID = "ref_invalid"
        NETWORK = "network"
        EMPTY_VALUE = "empty_value"
        TIMEOUT = "timeout"
        INTERNAL = "internal"

    @dataclass
    class FetchResult:
        secrets: Dict[str, str] = field(default_factory=dict)
        applied: list = field(default_factory=list)
        skipped: list = field(default_factory=list)
        warnings: list = field(default_factory=list)
        error: Optional[str] = None
        error_kind: Optional[ErrorKind] = None
        binary_path: Optional[str] = None

        @property
        def ok(self) -> bool:
            return self.error is None

        def fail(self, error: str, kind: ErrorKind):
            self.error, self.error_kind = error, kind
            return self

    class SecretSource:
        name = ""
        label = ""
        shape = "mapped"
        api_version = 1

        def is_enabled(self, cfg: dict) -> bool:
            return bool(isinstance(cfg, dict) and cfg.get("enabled"))

        def fetch(self, cfg: dict, home_path: Path):
            pass

        def protected_env_vars(self, cfg: dict) -> FrozenSet[str]:
            return frozenset()


# The child bao process gets ONLY these env vars: basics plus the OpenBao
# transport config (self-signed cert / skip-verify setups need it). Credentials
# never travel this way except VAULT_TOKEN, injected per-call when a token is
# passed explicitly.
_ALLOWED_CHILD_ENV_KEYS = (
    "PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "LANG", "LC_ALL",
    "BAO_SKIP_VERIFY", "BAO_CACERT", "BAO_CAPATH",
    "BAO_CLIENT_CERT", "BAO_CLIENT_KEY", "BAO_TLSSERVERNAME",
)


def _bao_cmd(argv, token=None, ns=None, addr=None, stdin_data=None):
    """Run a bao CLI command via argv list. Returns stdout string.

    Passes PATH/HOME/locale plus OpenBao transport config (BAO_SKIP_VERIFY,
    BAO_CACERT, ...) so self-signed endpoints work. Optionally injects
    VAULT_TOKEN. Never passes the user's full environment to child processes.
    ``-namespace=`` and ``-address=`` are inserted after the subcommand tokens
    and before any other flag/positional (bao rejects flags placed before the
    subcommand or after positionals). Non-zero exits, timeouts, and missing
    binaries are logged (stderr snippet, never secrets) and degrade to "".
    Timeout enforced via subprocess.run."""
    _ensure_handlers()
    argv = list(argv)
    binary = _resolve_bao_binary()
    cmd = [binary]
    env = {k: v for k, v in os.environ.items() if k in _ALLOWED_CHILD_ENV_KEYS}
    if token:
        env["VAULT_TOKEN"] = token

    try:
        flags = []
        if ns:
            flags.append(f"-namespace={ns}")
        if addr:
            flags.append(f"-address={addr}")
        if flags:
            insert_at = next((i for i, arg in enumerate(argv) if arg.startswith("-")),
                             len(argv))
            argv[insert_at:insert_at] = flags
        logger.debug("bao run: %s", " ".join(_redact_argv(cmd + argv)))
        proc = subprocess.run(
            cmd + argv, capture_output=True, text=True, encoding="utf-8",
            env=env, timeout=30.0, stdin=subprocess.DEVNULL,
        )
        rc = getattr(proc, "returncode", 0)
        stderr = getattr(proc, "stderr", "") or ""
        if rc != 0:
            logger.warning("bao %s exited rc=%s: %s",
                           " ".join(_redact_argv(argv[:3])),
                           rc, stderr[:1000].strip())
            return getattr(proc, "stdout", "") or ""
        return getattr(proc, "stdout", "")
    except subprocess.TimeoutExpired:
        logger.warning("bao %s timed out after 30s",
                       " ".join(_redact_argv(argv[:3])))
        return ""
    except FileNotFoundError:
        logger.warning("bao binary not found at %s (and not on PATH)", binary)
        return ""


def _do_approle_login(role_id, secret_id, addr, ns=None):
    """Perform AppRole login.

    Returns ``(client_token, lease_duration_seconds)`` on success, None on any
    failure. The real lease (Ansible issues short 15m tokens) drives cache
    expiry so the plugin never reuses a token the server already expired."""
    cmd_args = ["write", "-format=json", "auth/approle/login",
                 f"role_id={role_id}", f"secret_id={secret_id}"]

    stdout = _bao_cmd(cmd_args, ns=ns, addr=addr or None)

    try:
        data = json.loads(stdout)
        auth = data["auth"]
        token = auth["client_token"]
        try:
            lease = float(auth.get("lease_duration") or 0)
        except (TypeError, ValueError):
            lease = 0.0
        return token, lease
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.debug("openbao: login response unparseable: %s (stdout %.200r)", exc, stdout)
        return None


def _read_secret_id_file(path_str):
    """Read secret_id from disk. Returns stripped content or empty string."""
    expanded = os.path.expanduser(path_str)
    try:
        with open(expanded, "r") as f:
            return f.read().strip()
    except (OSError, IOError) as exc:
        logger.debug("openbao: cannot read secret-id file %s: %s", expanded, exc)
        return ""


def _is_valid_env_name(name):
    """Check if name is a legal environment variable name.
    Matches regex ^[A-Za-z_][A-Za-z0-9_]*$"""
    if not name:
        return False
    for i, c in enumerate(name):
        if i == 0:
            if not (c.isalpha() or c == '_'):
                return False
        else:
            if not (c.isalnum() or c == '_'):
                return False
    return True


def _normalize_env_name(raw_name):
    """Convert a vault path component to env var name.

    Examples: 'OPENAI_API_KEY' stays, 'some-path' becomes 'SOME_PATH',
    'mixed.case.key' becomes 'MIXED_CASE_KEY'."""
    normalized = raw_name.upper().replace('-', '_').replace('.', '_')
    return normalized


def _query_secrets_list(prefix, token, addr, ns=None):
    """Query secrets from vault at the specified prefix.

    Returns a dictionary mapping secret paths to their values.
    Performs the vault API call to retrieve all secrets under the given prefix.
    Handles both KV v1 and v2 mount formats gracefully."""
    if "/" in prefix:
        mount, rel = prefix.split("/", 1)
        cmd_args = ["kv", "list", "-format=json", f"-mount={mount}", rel]
    else:
        cmd_args = ["kv", "list", "-format=json", prefix]

    stdout = _bao_cmd(cmd_args, token=token, ns=ns, addr=addr or None)

    if not stdout or not stdout.strip():
        logger.warning("openbao: empty list response for prefix %r (vault unreachable or no keys)", prefix)
        return {}

    try:
        data = json.loads(stdout)
        entries = []

        # Support multiple response formats
        if "data" in data and "keys" in data["data"]:
            entries = data["data"]["keys"]
        elif "wrap" in data:
            wrap_data = json.loads(data["wrap"]["data"].get("response", "{}"))
            entries = wrap_data.get("data", {}).get("keys", [])
        else:
            # Try to parse as direct list
            if isinstance(data, list):
                entries = data
            else:
                entries = data.get("data", {})

        secrets_map = {}
        for entry in entries:
            if isinstance(entry, dict):
                key_path = entry.get("key", entry.get("name", ""))
                if key_path:
                    leaf = key_path.strip().rstrip("/")
                    if "/" in leaf:
                        logger.warning("openbao: skipping nested entry %r (flat layout expected)", key_path)
                        continue
                    full_path = f"{prefix.rstrip('/')}/{leaf}"
                    secrets_map[leaf] = full_path
            elif isinstance(entry, str):
                leaf = entry.strip().rstrip("/")
                if not leaf:
                    continue
                if "/" in leaf:
                    logger.warning("openbao: skipping nested entry %r (flat layout expected)", entry)
                    continue
                full_path = f"{prefix.rstrip('/')}/{leaf}"
                secrets_map[leaf] = full_path

        logger.info("openbao: list %r -> %d entries", prefix, len(secrets_map))
        return secrets_map

    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        logger.warning("openbao: could not parse list response for %r: %s", prefix, exc)
        return {}


def _query_secret_value(path, token, addr, ns=None):
    """Retrieve a single secret value from vault.

    ``bao kv get -format=json`` on a KV v2 mount nests the secret's fields
    under ``data.data.data``. Seeded-but-unfilled placeholders carry an empty
    value — those return None (skip) so an empty string never reaches an env
    var, where it would guarantee a 401 at the provider. Non-JSON stdout is
    treated as a raw value (e.g. ``-field`` output).
    Returns the string value on success, None if the read failed or is empty."""
    if "/" in path:
        mount, rel = path.split("/", 1)
        cmd_args = ["kv", "get", "-format=json", f"-mount={mount}", "-version=2", rel]
    else:
        cmd_args = ["kv", "get", "-format=json", path]

    stdout = _bao_cmd(cmd_args, token=token, ns=ns, addr=addr or None)

    if not stdout or not stdout.strip():
        logger.debug("openbao: empty read for %r", path)
        return None

    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout.strip() or None

    try:
        payload = parsed["data"]
        if not isinstance(payload, dict):
            return None
        # KV v2 nests fields under payload["data"] (payload carries "metadata");
        # KV v1 makes payload itself the fields dict.
        fields = payload.get("data") if "metadata" in payload else payload
        if not isinstance(fields, dict) or not fields:
            return None
        # Uniform layout: one field per object. Prefer the conventional
        # `value` field; otherwise take the single field (handles a field
        # named after the key). Multi-field objects warn and use `value`.
        if "value" in fields:
            if len(fields) > 1:
                logger.warning("openbao: %r has %d fields, using 'value'", path, len(fields))
            value = str(fields["value"])
        else:
            if len(fields) > 1:
                logger.warning("openbao: %r has %d fields and no 'value', using first", path, len(fields))
            value = str(next(iter(fields.values())))
        return value if value.strip() else None
    except (KeyError, TypeError, StopIteration):
        return None


class OpenBaoSource(SecretSource):
    """Fetch secrets from an OpenBao Vault backend.

    Configures itself from the ``secrets.openbao`` section of config.yaml::

        secrets:
          openbao:
            enabled: true
            role_id: "<approle-role-id>"          # from `ansible role` output; non-secret
            secret_id_file: "~/.hermes/auth/openbao-secret-id"
            namespace: "m5"
            mount: "kv-dev"
            base: "hermes"
            addr: ""                              # defaults to $BAO_ADDR
            ttl_seconds: 300
            watch_interval_seconds: 60
            override_existing: false   # true lets vault clobber .env/shell values
    """

    name = "openbao"
    label = "OpenBao"
    shape = "mapped"
    api_version = 1

    def __init__(self):
        # Token state — reset each fetch() call to avoid cross-call leakage
        self._token: Optional[str] = None
        self._token_time: float = 0.0
        self._token_expiry: float = 0.0  # wall-clock when the cached token dies
        self._current_token: Optional[str] = None  # set after login, used for read
        self._last_known: Dict[str, str] = {}  # for change detection
        self._timer: Optional[object] = None  # threading.Timer handle
        # Auth params + vault location kept for the watcher's self-heal re-login
        # (daily secret-id rotation must be picked up without a restart).
        self._auth: Dict[str, str] = {}

    def fetch(self, cfg: dict, home_path: Path):
        """Resolve all secrets under the configured prefix.

        Lifecycle: validate config → authenticate → discover secrets →
        read values → start watcher. Never raises, never prompts."""
        _ensure_handlers()
        result = FetchResult()

        if not isinstance(cfg, dict):
            logger.warning("openbao: config is not a dict, skipping")
            return result.fail("config is not a dict", ErrorKind.NOT_CONFIGURED)

        if not self.is_enabled(cfg):
            logger.info("openbao: disabled, skipping")
            return FetchResult(secrets={}, warnings=[])

        # Extract config fields
        role_id = str(cfg.get("role_id") or "").strip()
        secret_id_file = str(cfg.get("secret_id_file") or "").strip()
        addr = str(cfg.get("addr") or os.environ.get("BAO_ADDR") or "").strip()
        ns = str(cfg.get("namespace") or os.environ.get("BAO_NAMESPACE") or "").strip()
        mount = str(cfg.get("mount") or "kv-dev").strip("/")
        # ``base`` replaces ``namespace_prefix``; the old key still resolves so
        # configs written against the first design keep working unchanged.
        base = str(cfg.get("base") or cfg.get("namespace_prefix") or "hermes").strip("/")

        # Validate required fields
        if not role_id:
            logger.warning("openbao: enabled but role_id is not set")
            return result.fail(
                "secrets.openbao.enabled is true but role_id is not set.",
                ErrorKind.NOT_CONFIGURED
            )

        if not secret_id_file:
            logger.warning("openbao: enabled but secret_id_file is not set")
            return result.fail(
                "secrets.openbao.enabled is true but secret_id_file is not set.",
                ErrorKind.NOT_CONFIGURED
            )

        secret_id = _read_secret_id_file(secret_id_file)
        if not secret_id:
            logger.warning("openbao: could not read secret_id from %s", secret_id_file)
            return result.fail(
                f"could not read secret_id from {secret_id_file} (file missing or empty)",
                ErrorKind.NOT_CONFIGURED
            )

        logger.info("openbao: fetch start addr_host=%s ns=%s prefix=%s/%s override=%s",
                    addr.split("://")[-1].split("/")[0] if addr else "(env)",
                    ns or "(default)", mount, base,
                    bool(cfg.get("override_existing", False)))

        # Auth: reuse cached token until 80% of min(config TTL, real lease);
        # the Ansible role issues 15m tokens, so the lease wins when shorter.
        try:
            ttl = float(cfg.get("ttl_seconds", 300))
        except (TypeError, ValueError):
            ttl = 300.0
        cached_token = getattr(self, "_token", None)

        if cached_token and time.time() < getattr(self, "_token_expiry", 0.0):
            self._current_token = cached_token
        else:
            # Fresh login — re-read the secret-id file: a daily rotation lands
            # here without any restart.
            new_token = _do_approle_login(role_id, secret_id, addr, ns=ns)
            if not new_token:
                logger.warning("openbao: AppRole login failed (addr_host=%s ns=%s)",
                               addr.split("://")[-1].split("/")[0] if addr else "(env)",
                               ns or "(default)")
                return result.fail(
                    "AppRole login failed — check role_id, secret_id, and vault address",
                    ErrorKind.AUTH_FAILED
                )
            token, lease = new_token
            lifetime = ttl if lease <= 0 else min(ttl, lease)
            self._token = token
            self._token_time = time.time()
            self._token_expiry = self._token_time + lifetime * 0.8
            self._current_token = token

        # Keep what the watcher needs to re-login when its token dies mid-run.
        self._auth = {"role_id": role_id, "secret_id_file": secret_id_file,
                      "addr": addr, "ns": ns, "ttl": str(ttl)}

        # Discovery phase: objects live at kv-dev/hermes/<NAME> in namespace m5
        prefix = f"{mount}/{base}" if mount else base

        if not _is_valid_env_name(base.split('/')[-1]):
            logger.warning("openbao: invalid base component %r", base.split('/')[-1])
            return result.fail(
                f"invalid namespace prefix component: {base.split('/')[-1]!r}",
                ErrorKind.REF_INVALID
            )

        # Query all secrets
        secrets_map = _query_secrets_list(prefix, self._current_token, addr, ns=ns)

        # Read values in parallel: one `bao kv get` takes ~5s against this
        # server, so 33 sequential reads blow the orchestrator's 120s fetch
        # budget (TIMEOUT → result discarded → keys stay "(not set)"). A
        # bounded pool keeps it to ~list + 33/workers round trips.
        try:
            read_workers = int(cfg.get("read_workers", 8))
        except (TypeError, ValueError):
            read_workers = 8
        read_workers = max(1, min(read_workers, 16))
        read_t0 = time.time()
        values: Dict[str, Optional[str]] = {}
        if secrets_map:
            import concurrent.futures
            token, vault_addr, vault_ns = self._current_token, addr, ns
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=min(read_workers, len(secrets_map)),
                    thread_name_prefix="openbao-read") as pool:
                future_of = {
                    pool.submit(_query_secret_value, path, token, vault_addr, ns=vault_ns): name
                    for name, path in secrets_map.items()
                }
                for future in concurrent.futures.as_completed(future_of):
                    name = future_of[future]
                    try:
                        values[name] = future.result()
                    except Exception as exc:
                        logger.debug("openbao: read %r failed: %s", name, exc)
                        values[name] = None
        logger.debug("openbao: read %d values in %.1fs (%d workers)",
                     len(secrets_map), time.time() - read_t0, read_workers)

        for var_name, secret_path in secrets_map.items():
            # Read each secret's value
            value = values.get(var_name)

            if value is not None:
                env_name = _normalize_env_name(var_name)
                if _is_valid_env_name(env_name):
                    result.secrets[env_name] = value
                else:
                    result.warnings.append(f"skipped invalid env var name: {var_name!r}")
            else:
                # Empty (unfilled placeholder) or read failure — surface it so
                # the operator can see which of the seeded keys still need values.
                result.warnings.append(
                    f"no value for {var_name!r} at {secret_path!r} "
                    "(empty placeholder or read failure)")

        # Store snapshot for comparison
        self._last_known.update(result.secrets)

        logger.info("openbao: fetched %d keys, %d warnings (listed %d)",
                    len(result.secrets), len(result.warnings), len(secrets_map))

        # Start background watcher for live rotation
        try:
            watch_interval = int(cfg.get("watch_interval_seconds", 60))
        except (TypeError, ValueError):
            watch_interval = 60
        if watch_interval > 0 and result.secrets:
            self._start_watcher(watch_interval, cfg, addr)

        return result

    def is_enabled(self, cfg: dict) -> bool:
        return bool(isinstance(cfg, dict) and cfg.get("enabled"))

    def override_existing(self, cfg: dict) -> bool:
        # Honored from config: true lets vault clobber .env/shell values,
        # false (default) keeps pre-existing values.
        return bool(isinstance(cfg, dict) and cfg.get("override_existing", False))

    def protected_env_vars(self, cfg: dict) -> FrozenSet[str]:
        # AppRole credentials travel in extra_env per-cli, not env vars.
        return frozenset()

    def _start_watcher(self, interval, cfg, addr):
        """Start background watcher for live rotation.

        Creates a daemon thread that periodically checks vault for changes
        and updates os.environ when detected. Self-schedules via Timer for
        clean lifecycle tied to parent process exit."""
        import threading

        # Stop any previous timer first
        self._stop_watcher()

        def _tick():
            try:
                self._check_for_changes(cfg, addr)
            except Exception as exc:
                logger.debug("openbao: watcher tick error: %s", exc)

            self._start_watcher(interval, cfg, addr)  # Reschedule

        timer = threading.Timer(interval, _tick)
        timer.daemon = True
        timer.start()
        self._timer = timer

    def _stop_watcher(self):
        """Stop current watcher timer if running."""
        if hasattr(self, '_timer') and self._timer:
            try:
                self._timer.cancel()
            except RuntimeError:
                pass  # Timer already expired

    def _check_for_changes(self, cfg, addr, ns=None):
        """Check for secret rotations against latest known state.

        Queries a subset of secrets from vault and compares against stored
        values. Only samples up to 3 keys to keep overhead minimal.
        On change detected, writes new values directly to os.environ.

        Self-heal: the Ansible role issues short-lived tokens (15m) and a daily
        LaunchAgent rotates the secret-id file. If every sampled read comes
        back None, re-login — re-reading the secret-id file — so rotation keeps
        working without a Hermes restart."""
        mount = str(cfg.get("mount") or "kv-dev").strip("/")
        base = str(cfg.get("base") or cfg.get("namespace_prefix") or "hermes").strip("/")
        prefix = f"{mount}/{base}" if mount else base
        if ns is None:
            ns = (self._auth or {}).get("ns", "")

        try:
            # Sample 3-4 keys across namespaces for change detection
            sample_keys = list(self._last_known.keys())[:3]

            changed = False
            updated_values = {}
            sampled = 0
            missed = 0

            for var_name in sample_keys:
                # Vault layout is flat: the env name IS the leaf object name.
                vault_path = f"{prefix}/{var_name}"
                value = _query_secret_value(vault_path, self._current_token, addr, ns=ns)

                if value is not None:
                    sampled += 1
                    old_value = self._last_known.get(var_name, '')

                    if value != old_value:
                        # Value changed - update environment immediately
                        os.environ[var_name] = value
                        updated_values[var_name] = value
                        changed = True

                        # Also update the last known state
                        self._last_known[var_name] = value
                else:
                    missed += 1

            if sampled == 0 and missed > 0:
                # Token likely expired / secret-id rotated — re-authenticate.
                logger.warning("openbao: watcher missed %d/%d samples, re-logging in", missed, missed + sampled)
                self._relogin()

            if changed:
                logger.info("openbao: rotated %d secrets: %s",
                            len(updated_values), sorted(updated_values))

        except Exception as exc:
            logger.debug("openbao: watcher tick failed: %s", exc)
            pass  # Fail silently, next tick retries

    def _relogin(self) -> None:
        """Refresh the watcher's token, re-reading the secret-id file so a
        daily rotation is picked up. Failures are swallowed — next tick
        retries; the watcher never blocks."""
        auth = self._auth
        role_id = auth.get("role_id", "")
        secret_id_file = auth.get("secret_id_file", "")
        if not role_id or not secret_id_file:
            return
        try:
            secret_id = _read_secret_id_file(secret_id_file)
            if not secret_id:
                logger.warning("openbao: watcher re-login found empty secret-id file")
                return
            new_token = _do_approle_login(
                role_id, secret_id, auth.get("addr", ""), ns=auth.get("ns", ""))
            if not new_token:
                logger.warning("openbao: watcher re-login failed")
                return
            token, lease = new_token
            try:
                ttl = float(auth.get("ttl", 300))
            except (TypeError, ValueError):
                ttl = 300.0
            lifetime = ttl if lease <= 0 else min(ttl, lease)
            self._token = token
            self._token_time = time.time()
            self._token_expiry = self._token_time + lifetime * 0.8
            self._current_token = token
            logger.info("openbao: watcher re-authenticated")
        except Exception as exc:
            logger.debug("openbao: watcher re-login error: %s", exc)

    def config_schema(self) -> dict:
        return {
            "role_id": {"description": "AppRole role identifier", "default": ""},
            "secret_id_file": {"description": "Path to 0600 file containing AppRole secret_id", "default": ""},
            "addr": {"description": "Vault address (defaults to $BAO_ADDR)", "default": ""},
            "namespace": {"description": "OpenBao namespace holding the mount (e.g. m5)", "default": ""},
            "mount": {"description": "KV mount the secrets live under", "default": "kv-dev"},
            "base": {"description": "Base path (object prefix) for all secrets", "default": "hermes"},
            "ttl_seconds": {"description": "Token cache window; real lease_duration wins when shorter", "default": 300},
            "watch_interval_seconds": {"description": "Background poll frequency", "default": 60},
            "read_workers": {"description": "Parallel `bao kv get` workers for the read phase", "default": 8},
            "timeout_seconds": {"description": "Orchestrator fetch budget; raise if the backend is just slow", "default": 120},
            "override_existing": {"description": "Overwrite pre-existing .env/shell values", "default": False},
        }


def register(ctx):
    """Register OpenBaoSecretSource with Hermes plugin system.

    Called automatically by Hermes plugin manager during discovery.
    Registers the source so it's picked up by the orchestrator.
    """
    ctx.register_secret_source(OpenBaoSource())
