"""Comprehensive test suite for the OpenBao secret source plugin (Task 7).

Tests are organized in sections:
  A. Identity & Conformance
  B. Auth Flow Validation (mocked — no live vault)
  C. Name Helpers (pure functions)
  D. Secret ID Reading
  E. ErrorKind Enum Completeness
  F. Module Structure
  G. Watcher Lifecycle (stubbed threading.Timer)
  H. Security Properties (env isolation of the bao child process)

No test requires an actual OpenBao vault, network access, or the ``bao``
binary: subprocess calls are intercepted via monkeypatching.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

# Make the plugin package importable from repo (preferred) or installed location
_REPO_ROOT = Path(__file__).resolve().parents[2]
_REPO_PLUGINS = _REPO_ROOT / "plugins"
if str(_REPO_PLUGINS) not in sys.path:
    sys.path.insert(0, str(_REPO_PLUGINS))
_PLUGINS_DIR = Path.home() / ".hermes" / "plugins"
if str(_PLUGINS_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGINS_DIR))

import openbao as plugin  # noqa: E402  (import after sys.path manipulation)

HOME = Path.home()

# The only env vars _bao_cmd is allowed to forward to the child process
ALLOWED_ENV_KEYS = {"PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "LANG", "LC_ALL",
                    "BAO_SKIP_VERIFY", "BAO_CACERT", "BAO_CAPATH",
                    "BAO_CLIENT_CERT", "BAO_CLIENT_KEY", "BAO_TLSSERVERNAME"}


# ---------------------------------------------------------------------------
# Section A: Identity & Conformance
# ---------------------------------------------------------------------------

class TestIdentityAndConformance:
    def test_name_is_lowercase_alphanumeric_nonempty(self):
        name = plugin.OpenBaoSource.name
        assert isinstance(name, str)
        assert name != ""
        assert name.isalnum(), f"name {name!r} must be alphanumeric"
        assert name.islower(), f"name {name!r} must be lowercase"
        assert name == "openbao"

    def test_label_present(self):
        label = plugin.OpenBaoSource.label
        assert isinstance(label, str)
        assert label.strip() != ""

    def test_shape_is_mapped(self):
        assert plugin.OpenBaoSource.shape == "mapped"

    def test_api_version_is_1(self):
        assert plugin.OpenBaoSource.api_version == 1

    def test_is_enabled_empty_cfg_is_false(self):
        src = plugin.OpenBaoSource()
        assert src.is_enabled({}) is False

    def test_is_enabled_enabled_true(self):
        src = plugin.OpenBaoSource()
        assert src.is_enabled({"enabled": True}) is True

    def test_is_enabled_non_dict_is_false(self):
        src = plugin.OpenBaoSource()
        assert src.is_enabled(None) is False
        assert src.is_enabled("enabled") is False

    def test_override_existing_always_false(self):
        src = plugin.OpenBaoSource()
        assert src.override_existing({}) is False
        assert src.override_existing({"enabled": True}) is False
        # Explicit True IS honored — vault clobbers pre-existing env values.
        assert src.override_existing({"override_existing": True}) is True
        assert src.override_existing({"override_existing": False}) is False

    def test_protected_env_vars_returns_frozenset(self):
        src = plugin.OpenBaoSource()
        result = src.protected_env_vars({})
        assert isinstance(result, frozenset)
        result2 = src.protected_env_vars({"enabled": True, "role_id": "r"})
        assert isinstance(result2, frozenset)

    def test_config_schema_has_all_expected_keys(self):
        src = plugin.OpenBaoSource()
        schema = src.config_schema()
        assert isinstance(schema, dict)
        expected_keys = {
            "role_id",
            "secret_id_file",
            "addr",
            "namespace",
            "mount",
            "base",
            "ttl_seconds",
            "watch_interval_seconds",
            "read_workers",
            "timeout_seconds",
            "override_existing",
        }
        assert set(schema.keys()) == expected_keys
        # Each entry should at least carry a description
        for key, entry in schema.items():
            assert isinstance(entry, dict), f"schema[{key!r}] should be a dict"
            assert "description" in entry, f"schema[{key!r}] lacks description"


# ---------------------------------------------------------------------------
# Section B: Auth Flow Validation (mocked)
# ---------------------------------------------------------------------------

class TestAuthFlow:
    def _forbid_bao(self, monkeypatch):
        """Ensure no test in this class ever shells out to the real CLI."""
        def _boom(*args, **kwargs):
            raise AssertionError("bao CLI must not be invoked in this test")
        monkeypatch.setattr(plugin, "_bao_cmd", _boom)

    def test_fetch_empty_cfg_returns_soft_ok(self, monkeypatch):
        """Disabled source returns an ok FetchResult with no secrets — never raises."""
        self._forbid_bao(monkeypatch)
        src = plugin.OpenBaoSource()
        result = src.fetch({}, HOME)
        assert result.ok, "disabled fetch must be soft-ok (error is None)"
        assert result.error is None
        assert dict(result.secrets) == {}

    def test_fetch_non_dict_returns_not_configured(self, monkeypatch):
        self._forbid_bao(monkeypatch)
        src = plugin.OpenBaoSource()
        for bad_cfg in (None, "enabled", 42, ["enabled"]):
            result = src.fetch(bad_cfg, HOME)
            assert not result.ok
            assert result.error_kind == plugin.ErrorKind.NOT_CONFIGURED

    def test_fetch_enabled_without_role_id_mentions_role_id(self, monkeypatch):
        self._forbid_bao(monkeypatch)
        src = plugin.OpenBaoSource()
        result = src.fetch({"enabled": True}, HOME)
        assert not result.ok
        assert result.error_kind == plugin.ErrorKind.NOT_CONFIGURED
        assert "role_id" in result.error

    def test_fetch_enabled_without_secret_id_file_mentions_it(self, monkeypatch):
        self._forbid_bao(monkeypatch)
        src = plugin.OpenBaoSource()
        result = src.fetch({"enabled": True, "role_id": "test-role"}, HOME)
        assert not result.ok
        assert result.error_kind == plugin.ErrorKind.NOT_CONFIGURED
        assert "secret_id_file" in result.error

    def test_fetch_enabled_with_unreadable_secret_id_file_fails_softly(
        self, monkeypatch, tmp_path
    ):
        """Missing secret_id file → NOT_CONFIGURED, never an exception."""
        self._forbid_bao(monkeypatch)
        src = plugin.OpenBaoSource()
        result = src.fetch(
            {
                "enabled": True,
                "role_id": "test-role",
                "secret_id_file": str(tmp_path / "does-not-exist"),
            },
            HOME,
        )
        assert not result.ok
        assert result.error_kind == plugin.ErrorKind.NOT_CONFIGURED
        assert "secret_id" in result.error

    def test_fetch_result_fail_returns_self_for_chaining(self):
        """fail() must return the same object so callers can `return result.fail(...)`."""
        src = plugin.OpenBaoSource()
        result = src.fetch(None, HOME)  # already a failed FetchResult
        chained = result.fail("second error", plugin.ErrorKind.INTERNAL)
        assert chained is result
        assert result.error == "second error"
        assert result.error_kind == plugin.ErrorKind.INTERNAL
        assert not result.ok

    def test_fetch_never_raises_on_login_failure(self, monkeypatch, tmp_path):
        """With a valid-looking config but a failing login, fetch returns AUTH_FAILED."""
        secret_file = tmp_path / "secret-id"
        secret_file.write_text("s3cr3t\n")
        monkeypatch.setattr(plugin, "_do_approle_login", lambda *a, **k: None)
        src = plugin.OpenBaoSource()
        result = src.fetch(
            {
                "enabled": True,
                "role_id": "role",
                "secret_id_file": str(secret_file),
            },
            HOME,
        )
        assert not result.ok
        assert result.error_kind == plugin.ErrorKind.AUTH_FAILED


# ---------------------------------------------------------------------------
# Section C: Name Helpers (pure functions)
# ---------------------------------------------------------------------------

class TestNameHelpers:
    def test_normalize_preserves_already_valid_name(self):
        assert plugin._normalize_env_name("OPENAI_API_KEY") == "OPENAI_API_KEY"

    def test_normalize_dashes_to_underscores_and_uppers(self):
        assert plugin._normalize_env_name("some-key") == "SOME_KEY"

    def test_normalize_dots_to_underscores_and_uppers(self):
        assert plugin._normalize_env_name("mixed.case.key") == "MIXED_CASE_KEY"

    def test_normalize_mixed_separators(self):
        assert plugin._normalize_env_name("a-b.c") == "A_B_C"

    def test_is_valid_env_name_accepts_standard(self):
        assert plugin._is_valid_env_name("FOO_BAR") is True

    def test_is_valid_env_name_accepts_leading_underscore(self):
        assert plugin._is_valid_env_name("_private") is True

    def test_is_valid_env_name_rejects_leading_digit(self):
        assert plugin._is_valid_env_name("123start") is False

    def test_is_valid_env_name_rejects_empty(self):
        assert plugin._is_valid_env_name("") is False

    def test_is_valid_env_name_rejects_separators(self):
        assert plugin._is_valid_env_name("has-dash") is False
        assert plugin._is_valid_env_name("has.dot") is False
        assert plugin._is_valid_env_name("has space") is False

    def test_is_valid_env_name_accepts_digits_after_first_char(self):
        assert plugin._is_valid_env_name("FOO2") is True

    def test_normalized_names_are_always_valid_env_names(self):
        for raw in ("OPENAI_API_KEY", "some-key", "mixed.case.key", "_x"):
            assert plugin._is_valid_env_name(plugin._normalize_env_name(raw)), raw


# ---------------------------------------------------------------------------
# Section D: Secret ID Reading
# ---------------------------------------------------------------------------

class TestSecretIdReading:
    def test_reads_existing_file_content_stripped(self, tmp_path):
        p = tmp_path / "openbao-secret-id"
        p.write_text("  abc123-secret \n")
        assert plugin._read_secret_id_file(str(p)) == "abc123-secret"

    def test_missing_file_returns_empty_string(self, tmp_path):
        missing = tmp_path / "no-such-file"
        assert not missing.exists()
        assert plugin._read_secret_id_file(str(missing)) == ""

    def test_empty_file_returns_empty_string(self, tmp_path):
        p = tmp_path / "empty-secret-id"
        p.write_text("")
        assert plugin._read_secret_id_file(str(p)) == ""

    def test_whitespace_only_file_returns_empty_string(self, tmp_path):
        p = tmp_path / "ws-secret-id"
        p.write_text("   \n\t\n")
        assert plugin._read_secret_id_file(str(p)) == ""

    def test_missing_file_never_raises(self, tmp_path):
        # Even a path in a non-existent directory degrades to ""
        assert plugin._read_secret_id_file(str(tmp_path / "a" / "b" / "c")) == ""


# ---------------------------------------------------------------------------
# Section E: ErrorKind Enum Completeness
# ---------------------------------------------------------------------------

class TestErrorKind:
    EXPECTED_MEMBERS = {
        "NOT_CONFIGURED",
        "BINARY_MISSING",
        "AUTH_FAILED",
        "AUTH_EXPIRED",
        "REF_INVALID",
        "NETWORK",
        "EMPTY_VALUE",
        "TIMEOUT",
        "INTERNAL",
    }

    def test_all_nine_members_exist(self):
        actual = {m.name for m in plugin.ErrorKind}
        missing = self.EXPECTED_MEMBERS - actual
        assert not missing, f"ErrorKind missing members: {missing}"

    def test_each_member_accessible_by_name(self):
        for name in self.EXPECTED_MEMBERS:
            assert hasattr(plugin.ErrorKind, name), name

    def test_member_count_is_at_least_nine(self):
        assert len(list(plugin.ErrorKind)) >= 9

    def test_members_are_distinct(self):
        values = [getattr(plugin.ErrorKind, n) for n in self.EXPECTED_MEMBERS]
        assert len(set(map(repr, values))) == len(values)


# ---------------------------------------------------------------------------
# Section F: Module Structure
# ---------------------------------------------------------------------------

class TestModuleStructure:
    def test_module_exports_openbao_source(self):
        assert hasattr(plugin, "OpenBaoSource")
        assert isinstance(plugin.OpenBaoSource, type)

    def test_module_exports_register(self):
        assert hasattr(plugin, "register")
        assert callable(plugin.register)

    def test_register_instantiates_and_registers_source(self):
        ctx = mock.MagicMock()
        plugin.register(ctx)
        ctx.register_secret_source.assert_called_once()
        registered = ctx.register_secret_source.call_args[0][0]
        assert isinstance(registered, plugin.OpenBaoSource)

    def test_source_is_constructible_without_args(self):
        src = plugin.OpenBaoSource()
        # Internal state initialized
        assert src._token is None
        assert src._timer is None
        assert isinstance(src._last_known, dict)

    def test_source_subclasses_secret_source(self):
        assert issubclass(plugin.OpenBaoSource, plugin.SecretSource)

    def test_required_interface_methods_exist(self):
        src = plugin.OpenBaoSource()
        for meth in ("fetch", "is_enabled", "override_existing",
                     "protected_env_vars", "config_schema",
                     "_start_watcher", "_stop_watcher", "_check_for_changes"):
            assert callable(getattr(src, meth, None)), f"missing method {meth}"


# ---------------------------------------------------------------------------
# Section G: Watcher Lifecycle (stubbed)
# ---------------------------------------------------------------------------

class TestWatcherLifecycle:
    def test_start_watcher_creates_daemon_timer(self):
        src = plugin.OpenBaoSource()
        with mock.patch("threading.Timer") as MockTimer:
            fake_timer = MockTimer.return_value
            src._start_watcher(60, {"namespace_prefix": "hermes/"}, "")

            MockTimer.assert_called_once()
            args = MockTimer.call_args[0]
            assert args[0] == 60, "Timer interval must be the configured seconds"
            assert callable(args[1]), "Timer callback must be callable"

            fake_timer.start.assert_called_once()
            assert fake_timer.daemon is True, "watcher thread must be a daemon"
            assert src._timer is fake_timer
        # Cleanup (cancel the MagicMock — no real thread exists)
        src._stop_watcher()

    def test_stop_watcher_cancels_timer(self):
        src = plugin.OpenBaoSource()
        with mock.patch("threading.Timer") as MockTimer:
            fake_timer = MockTimer.return_value
            src._start_watcher(30, {}, "")
            src._stop_watcher()
            fake_timer.cancel.assert_called_once()

    def test_start_watcher_replaces_previous_timer(self):
        src = plugin.OpenBaoSource()
        with mock.patch("threading.Timer") as MockTimer:
            first = mock.MagicMock()
            second = mock.MagicMock()
            MockTimer.side_effect = [first, second]

            src._start_watcher(10, {}, "")
            assert src._timer is first
            src._start_watcher(10, {}, "")
            # Previous timer must be cancelled before rescheduling
            first.cancel.assert_called_once()
            assert src._timer is second

    def test_stop_watcher_without_timer_is_noop(self):
        src = plugin.OpenBaoSource()
        assert src._timer is None
        src._stop_watcher()  # must not raise
        assert src._timer is None

    def test_stop_watcher_swallows_runtime_error(self):
        src = plugin.OpenBaoSource()
        exploding_timer = mock.MagicMock()
        exploding_timer.cancel.side_effect = RuntimeError("already expired")
        src._timer = exploding_timer
        src._stop_watcher()  # must not raise

    def test_check_for_changes_never_raises_on_backend_errors(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("vault exploded")
        monkeypatch.setattr(plugin, "_query_secret_value", _boom)
        src = plugin.OpenBaoSource()
        src._last_known = {"OPENAI_API_KEY": "old"}
        src._current_token = "tok"
        # _check_for_changes must fail silently
        src._check_for_changes({"base": "hermes"}, "")

    def test_check_for_changes_updates_env_on_rotation(self, monkeypatch):
        monkeypatch.setattr(plugin, "_query_secret_value",
                            lambda path, token, addr, ns=None: "rotated-value")
        src = plugin.OpenBaoSource()
        src._last_known = {"OPENBAO_TEST_ROTATED_KEY": "old-value"}
        src._current_token = "tok"
        monkeypatch.delenv("OPENBAO_TEST_ROTATED_KEY", raising=False)
        try:
            src._check_for_changes({"namespace_prefix": "hermes/"}, "")
            import os
            assert os.environ.get("OPENBAO_TEST_ROTATED_KEY") == "rotated-value"
            assert src._last_known["OPENBAO_TEST_ROTATED_KEY"] == "rotated-value"
        finally:
            import os
            os.environ.pop("OPENBAO_TEST_ROTATED_KEY", None)


# ---------------------------------------------------------------------------
# Section H: Security Properties
# ---------------------------------------------------------------------------

class _FakeCompleted:
    def __init__(self, stdout=""):
        self.stdout = stdout
        self.stderr = ""


class TestSecurityProperties:
    def _capture_run(self, monkeypatch):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured.update(kwargs)
            return _FakeCompleted(stdout="fake-output")

        monkeypatch.setattr(plugin.subprocess, "run", fake_run)
        return captured

    def test_bao_cmd_env_excludes_arbitrary_secrets(self, monkeypatch):
        captured = self._capture_run(monkeypatch)
        monkeypatch.setenv("SUPER_SECRET_AWS_KEY", "hunter2")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_leakme")

        out = plugin._bao_cmd(["status"])

        assert out == "fake-output"
        env = captured["env"]
        assert isinstance(env, dict)
        # Only the whitelisted passthrough keys may appear (no token passed)
        assert set(env.keys()) <= ALLOWED_ENV_KEYS, (
            f"unexpected env keys leaked to child: {set(env.keys()) - ALLOWED_ENV_KEYS}"
        )
        assert "SUPER_SECRET_AWS_KEY" not in env
        assert "GITHUB_TOKEN" not in env
        assert "VAULT_TOKEN" not in env

    def test_bao_cmd_env_whitelist_exact_keys_when_present(self, monkeypatch):
        captured = self._capture_run(monkeypatch)
        for key in ("SUPER_SECRET_AWS_KEY", "DATABASE_URL", "SSH_AUTH_SOCK"):
            monkeypatch.setenv(key, "leak")
        plugin._bao_cmd(["status"])
        env = captured["env"]
        for key in ("SUPER_SECRET_AWS_KEY", "DATABASE_URL", "SSH_AUTH_SOCK"):
            assert key not in env

    def test_bao_cmd_injects_vault_token_only_when_given(self, monkeypatch):
        captured = self._capture_run(monkeypatch)
        plugin._bao_cmd(["kv", "get", "hermes/OPENAI_API_KEY"], token="tok-abc123")
        env = captured["env"]
        assert env.get("VAULT_TOKEN") == "tok-abc123"
        assert set(env.keys()) <= ALLOWED_ENV_KEYS | {"VAULT_TOKEN"}

    def test_bao_cmd_passes_path_and_home_when_available(self, monkeypatch):
        import os
        captured = self._capture_run(monkeypatch)
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        monkeypatch.setenv("HOME", str(Path.home()))
        plugin._bao_cmd(["status"])
        env = captured["env"]
        assert env.get("PATH") == os.environ["PATH"]
        assert env.get("HOME") == os.environ["HOME"]

    def test_bao_cmd_invokes_expected_binary(self, monkeypatch):
        captured = self._capture_run(monkeypatch)
        plugin._bao_cmd(["kv", "list", "hermes/"])
        assert captured["cmd"][0] == "/opt/homebrew/bin/bao"
        assert captured["cmd"][1:] == ["kv", "list", "hermes/"]
        # Never inherits the parent env wholesale
        assert captured["env"] is not None

    def test_bao_cmd_returns_empty_on_file_not_found(self, monkeypatch):
        def fake_run(*args, **kwargs):
            raise FileNotFoundError("/opt/homebrew/bin/bao not installed")
        monkeypatch.setattr(plugin.subprocess, "run", fake_run)
        assert plugin._bao_cmd(["status"]) == ""

    def test_bao_cmd_returns_empty_on_timeout(self, monkeypatch):
        def fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=["bao"], timeout=30.0)
        monkeypatch.setattr(plugin.subprocess, "run", fake_run)
        assert plugin._bao_cmd(["status"]) == ""

    def test_bao_cmd_sets_timeout_and_devnull_stdin(self, monkeypatch):
        captured = self._capture_run(monkeypatch)
        plugin._bao_cmd(["status"])
        assert captured.get("timeout") == 30.0
        assert captured.get("stdin") == subprocess.DEVNULL


# ---------------------------------------------------------------------------
# Section I: Namespace & transport passthrough
# ---------------------------------------------------------------------------

class TestNamespacePassthrough:
    """Vault objects live in namespace m5 behind mount kv-dev; every CLI call
    needs -namespace=m5, and the self-signed-cert transport config must reach
    the child bao process."""

    def _capture_run(self, monkeypatch):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env", {})
            return _FakeCompleted(stdout="{}")

        monkeypatch.setattr(plugin.subprocess, "run", fake_run)
        return captured

    def test_bao_cmd_inserts_namespace_after_subcommand(self, monkeypatch):
        captured = self._capture_run(monkeypatch)
        plugin._bao_cmd(["kv", "list", "-format=json", "kv-dev/hermes"], ns="m5")
        cmd = captured["cmd"]
        assert "-namespace=m5" in cmd
        # namespace flag must appear after the subcommand tokens and before
        # any other flag (bao rejects flags placed before or after positionals)
        ns_i = cmd.index("-namespace=m5")
        assert cmd[1:3] == ["kv", "list"]
        assert ns_i == 3

    def test_bao_cmd_no_namespace_flag_when_absent(self, monkeypatch):
        captured = self._capture_run(monkeypatch)
        plugin._bao_cmd(["kv", "list", "-format=json", "hermes"])
        assert not any(str(a).startswith("-namespace=") for a in captured["cmd"])

    def test_bao_cmd_forwards_skip_verify(self, monkeypatch):
        captured = self._capture_run(monkeypatch)
        monkeypatch.setenv("BAO_SKIP_VERIFY", "true")
        plugin._bao_cmd(["status"])
        assert captured["env"].get("BAO_SKIP_VERIFY") == "true"

    def test_bao_cmd_forwards_cacert(self, monkeypatch):
        captured = self._capture_run(monkeypatch)
        monkeypatch.setenv("BAO_CACERT", "/x/ca.pem")
        plugin._bao_cmd(["status"])
        assert captured["env"].get("BAO_CACERT") == "/x/ca.pem"

    def test_transport_allowlist_covers_bao_env_vars(self):
        allowed = plugin._ALLOWED_CHILD_ENV_KEYS
        for k in ("BAO_SKIP_VERIFY", "BAO_CACERT", "BAO_CAPATH",
                  "BAO_CLIENT_CERT", "BAO_CLIENT_KEY", "BAO_TLSSERVERNAME"):
            assert k in allowed, f"transport var {k} must be forwarded to child"

    def test_query_list_passes_namespace(self, monkeypatch):
        captured = {}

        def spy(argv, token=None, ns=None, **kw):
            captured["argv"] = argv
            captured["ns"] = ns
            return json.dumps({"data": {"keys": ["OPENAI_API_KEY"]}})

        monkeypatch.setattr(plugin, "_bao_cmd", spy)
        out = plugin._query_secrets_list("kv-dev/hermes", "tok", "", ns="m5")
        assert captured["ns"] == "m5"
        assert "OPENAI_API_KEY" in out
        assert out["OPENAI_API_KEY"] == "kv-dev/hermes/OPENAI_API_KEY"


# ---------------------------------------------------------------------------
# Section J: KV v2 parsing & empty placeholders
# ---------------------------------------------------------------------------

class TestKVv2Parsing:
    """`bao kv get -format=json` nests the secret's fields under
    data.data.data; 33 seeded placeholders start EMPTY and must be skipped,
    never injected as empty strings (guaranteed 401s)."""

    def test_parses_kv2_nested_data_value_field(self, monkeypatch):
        payload = json.dumps(
            {"data": {"data": {"value": "sk-abc"}, "metadata": {"version": 1}}})
        monkeypatch.setattr(plugin, "_bao_cmd", lambda *a, **k: payload)
        got = plugin._query_secret_value("kv-dev/hermes/OPENAI_API_KEY", "tok", "")
        assert got == "sk-abc"

    def test_parses_kv2_field_named_after_key(self, monkeypatch):
        payload = json.dumps(
            {"data": {"data": {"OPENAI_API_KEY": "sk-xyz"}, "metadata": {}}})
        monkeypatch.setattr(plugin, "_bao_cmd", lambda *a, **k: payload)
        got = plugin._query_secret_value("kv-dev/hermes/OPENAI_API_KEY", "tok", "")
        assert got == "sk-xyz"

    def test_empty_field_value_treated_as_none(self, monkeypatch):
        payload = json.dumps({"data": {"data": {"value": ""}, "metadata": {}}})
        monkeypatch.setattr(plugin, "_bao_cmd", lambda *a, **k: payload)
        assert plugin._query_secret_value("kv-dev/hermes/OPENAI_API_KEY", "tok", "") is None

    def test_whitespace_field_value_treated_as_none(self, monkeypatch):
        payload = json.dumps({"data": {"data": {"value": "   "}, "metadata": {}}})
        monkeypatch.setattr(plugin, "_bao_cmd", lambda *a, **k: payload)
        assert plugin._query_secret_value("x", "tok", "") is None

    def test_empty_data_dict_returns_none(self, monkeypatch):
        payload = json.dumps({"data": {"data": {}, "metadata": {}}})
        monkeypatch.setattr(plugin, "_bao_cmd", lambda *a, **k: payload)
        assert plugin._query_secret_value("x", "tok", "") is None

    def test_non_json_plain_value_still_returns_value(self, monkeypatch):
        monkeypatch.setattr(plugin, "_bao_cmd", lambda *a, **k: "plain-text-key\n")
        assert plugin._query_secret_value("x", "tok", "") == "plain-text-key"

    def test_fetch_skips_empty_and_warns(self, monkeypatch, tmp_path):
        """An empty placeholder must NOT appear in result.secrets; it must be
        named in warnings so fill-progress is visible."""
        secret_file = tmp_path / "secret-id"
        secret_file.write_text("s\n")
        monkeypatch.setattr(plugin, "_do_approle_login", lambda *a, **k: ("tok", 900))
        monkeypatch.setattr(plugin, "_query_secrets_list",
                            lambda *a, **k: {
                                "OPENAI_API_KEY": "kv-dev/hermes/OPENAI_API_KEY",
                                "EXA_API_KEY": "kv-dev/hermes/EXA_API_KEY"})
        monkeypatch.setattr(plugin, "_query_secret_value",
                            lambda path, token, addr, ns=None:
                            "sk-live" if "OPENAI" in path else None)
        src = plugin.OpenBaoSource()
        result = src.fetch({
            "enabled": True, "role_id": "r", "secret_id_file": str(secret_file),
            "namespace": "m5", "mount": "kv-dev", "base": "hermes",
            "watch_interval_seconds": 0,
        }, HOME)
        assert result.ok
        assert result.secrets == {"OPENAI_API_KEY": "sk-live"}
        assert any("EXA_API_KEY" in w or "EXA" in w for w in result.warnings), result.warnings


# ---------------------------------------------------------------------------
# Section K: Lease-aware TTL & watcher self-heal
# ---------------------------------------------------------------------------

class TestLeaseAwareAuth:
    """The Ansible role issues 15m tokens; the plugin must respect the real
    lease_duration (not just config ttl_seconds) when caching the token."""

    def test_login_returns_token_and_lease(self, monkeypatch):
        payload = json.dumps(
            {"auth": {"client_token": "t-1", "lease_duration": 900}})
        monkeypatch.setattr(plugin, "_bao_cmd", lambda *a, **k: payload)
        tok, lease = plugin._do_approle_login("r", "s", "", ns="m5")
        assert tok == "t-1"
        assert lease == 900

    def test_login_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(plugin, "_bao_cmd", lambda *a, **k: "")
        assert plugin._do_approle_login("r", "s", "") is None

    def test_fetch_caches_expiry_from_short_lease(self, monkeypatch, tmp_path):
        """lease 60s, ttl_seconds 300 → expiry must use the lease (60*0.8=48s)."""
        secret_file = tmp_path / "secret-id"
        secret_file.write_text("s\n")
        monkeypatch.setattr(plugin, "_do_approle_login",
                            lambda *a, **k: ("tok", 60))
        monkeypatch.setattr(plugin, "_query_secrets_list", lambda *a, **k: {})
        src = plugin.OpenBaoSource()
        src.fetch({
            "enabled": True, "role_id": "r", "secret_id_file": str(secret_file),
            "ttl_seconds": 300, "watch_interval_seconds": 0,
        }, HOME)
        remaining = src._token_expiry - time.time()
        assert 30 < remaining <= 48.001, remaining

    def test_fetch_caches_expiry_from_config_ttl_when_lease_longer(
            self, monkeypatch, tmp_path):
        secret_file = tmp_path / "secret-id"
        secret_file.write_text("s\n")
        monkeypatch.setattr(plugin, "_do_approle_login",
                            lambda *a, **k: ("tok", 900))
        monkeypatch.setattr(plugin, "_query_secrets_list", lambda *a, **k: {})
        src = plugin.OpenBaoSource()
        src.fetch({
            "enabled": True, "role_id": "r", "secret_id_file": str(secret_file),
            "ttl_seconds": 120, "watch_interval_seconds": 0,
        }, HOME)
        remaining = src._token_expiry - time.time()
        assert 60 < remaining <= 96.001, remaining


class TestWatcherSelfHeal:
    """Daily LaunchAgent rotation rewrites the secret-id file and short TTLs
    expire tokens; the watcher must re-login (re-reading the file) to keep
    rotation detection alive without a Hermes restart."""

    def test_relogin_when_all_samples_return_none(self, monkeypatch, tmp_path):
        secret_file = tmp_path / "secret-id"
        secret_file.write_text("s2")
        src = plugin.OpenBaoSource()
        src._last_known = {"OPENAI_API_KEY": "v"}
        src._current_token = "old-tok"
        src._auth = {"role_id": "r", "secret_id_file": str(secret_file),
                     "addr": "", "ns": "m5"}
        monkeypatch.setattr(plugin, "_query_secret_value", lambda *a, **k: None)
        calls = []
        monkeypatch.setattr(plugin, "_do_approle_login",
                            lambda role_id, secret_id, addr, ns=None:
                            calls.append((role_id, secret_id)) or ("new-tok", 900))
        src._check_for_changes({"mount": "kv-dev", "base": "hermes"}, "", ns="m5")
        assert calls == [("r", "s2")], calls
        assert src._current_token == "new-tok"

    def test_no_relogin_when_sample_succeeds(self, monkeypatch):
        src = plugin.OpenBaoSource()
        src._last_known = {"K": "same"}
        src._current_token = "tok"
        src._auth = {"role_id": "r", "secret_id_file": "x", "addr": "", "ns": ""}
        monkeypatch.setattr(plugin, "_query_secret_value",
                            lambda *a, **k: "same")
        calls = []
        monkeypatch.setattr(plugin, "_do_approle_login",
                            lambda *a, **k: calls.append(1) or ("new", 900))
        src._check_for_changes({"mount": "kv-dev", "base": "hermes"}, "")
        assert calls == []
        assert src._current_token == "tok"

    def test_watcher_uses_mount_and_base_in_vault_path(self, monkeypatch):
        seen = {}

        def spy(path, token, addr, ns=None):
            seen["path"] = path
            return "same"

        monkeypatch.setattr(plugin, "_query_secret_value", spy)
        src = plugin.OpenBaoSource()
        src._last_known = {"OPENAI_API_KEY": "same"}
        src._current_token = "tok"
        src._auth = {"role_id": "r", "secret_id_file": "x", "addr": "", "ns": "m5"}
        src._check_for_changes({"mount": "kv-dev", "base": "hermes"}, "", ns="m5")
        assert seen["path"] == "kv-dev/hermes/OPENAI_API_KEY"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
