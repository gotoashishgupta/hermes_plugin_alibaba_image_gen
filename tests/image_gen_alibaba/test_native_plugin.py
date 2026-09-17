from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import socket
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "image_gen_alibaba"


def test_native_manifest_has_no_credential_gate():
    manifest = yaml.safe_load((PLUGIN / "plugin.yaml").read_text())
    assert manifest["name"] == "alibaba"
    assert manifest["kind"] == "backend"
    assert "requires_env" not in manifest
    assert manifest["python_dependencies"] == ["requests>=2.31,<3"]


@pytest.mark.parametrize("credentials", [
    {},
    {"ALIBABA_TOKEN_PLAN_API_KEY": "test-token"},
    {"ALIBABA_TOKEN_PLAN_CN_API_KEY": "test-token-cn"},
    {"DASHSCOPE_API_KEY": "test-dashscope"},
    {"ALIBABA_API_KEY": "test-custom", "ALIBABA_BASE_URL": "https://custom.example/v1"},
], ids=["no-credentials", "token-plan", "token-plan-cn", "dashscope", "custom"])
def test_native_directory_discovery_registers_instance(tmp_path, monkeypatch, credentials):
    from agent import image_gen_registry
    from agent.image_gen_provider import ImageGenProvider
    from hermes_cli import plugins

    plugin_dir = tmp_path / "plugins" / "checkout-name"
    plugin_dir.parent.mkdir()
    plugin_dir.symlink_to(PLUGIN, target_is_directory=True)
    (tmp_path / "config.yaml").write_text("plugins:\n  enabled: [alibaba]\n")
    monkeypatch.setenv("HERMES_SAFE_MODE", "0")
    monkeypatch.setenv("HERMES_ENABLE_PROJECT_PLUGINS", "0")
    monkeypatch.setattr(plugins, "get_bundled_plugins_dir", lambda: tmp_path / "empty-bundled")
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda: importlib.metadata.EntryPoints())
    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", lambda **kwargs: None)
    for name, value in credentials.items():
        monkeypatch.setenv(name, value)

    manager = plugins.PluginManager(scope_key=str(tmp_path.resolve()))
    try:
        manager.discover_and_load()
        assert set(manager._plugins) == {"alibaba"}
        loaded = manager._plugins["alibaba"]
        assert loaded.enabled, loaded.error
        assert loaded.error is None
        assert loaded.manifest.source == "user"
        assert loaded.manifest.kind == "backend"
        assert loaded.manifest.requires_env == []
        assert Path(loaded.module.__file__).resolve() == PLUGIN / "__init__.py"
        provider = image_gen_registry.get_provider("alibaba", scope=manager.scope_key)
        assert isinstance(provider, ImageGenProvider)
        assert isinstance(provider, loaded.module.AlibabaImageGenProvider)
        assert provider.name == "alibaba"
        assert provider.is_available() is bool(credentials)
        manager.discover_and_load()
        assert image_gen_registry.get_provider("alibaba", scope=manager.scope_key) is provider
    finally:
        manager.unload()
    assert image_gen_registry.snapshot_registration("alibaba", scope=manager.scope_key) is None


def test_install_core_discovers_and_loads_installed_root_files(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(os, "environ", {
        "HOME": str(home),
        "HERMES_HOME": str(home),
        "HERMES_SAFE_MODE": "0",
        "HERMES_ENABLE_PROJECT_PLUGINS": "0",
        "HERMES_BUNDLED_PLUGINS": str(tmp_path / "empty-bundled"),
    })
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.chdir(tmp_path)

    from agent import image_gen_registry
    from hermes_cli import plugins, plugins_cmd

    def deny_network(*args, **kwargs):
        pytest.fail("installation and registration must not access the network")

    monkeypatch.setattr(socket, "create_connection", deny_network)
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_network)
    identifier = "example/hermes-plugin-alibaba-image-gen/plugins/image_gen_alibaba"
    revision = "a" * 40
    files = ("__init__.py", "alibaba.py", "plugin.yaml")

    def populate_clone(destination, git_url, requested_revision):
        assert git_url == "https://github.com/example/hermes-plugin-alibaba-image-gen.git"
        assert requested_revision is None
        destination.mkdir()
        (destination / "plugins").mkdir()
        (destination / "plugins" / "image_gen_alibaba").mkdir(parents=True)
        for name in files:
            shutil.copyfile(PLUGIN / name, destination / "plugins" / "image_gen_alibaba" / name)
        # extra repo files that must NOT be installed
        (destination / "README.md").write_text("repo readme")
        (destination / "tests").mkdir()
        (destination / "tests" / "dummy.py").write_text("# test")
        (destination / "pyproject.toml").write_text("[tool]")
        (destination / "uv.lock").write_text("lock")
        return revision

    clone = Mock(side_effect=populate_clone)
    scan = Mock(wraps=plugins_cmd._scan_plugin_tree)
    monkeypatch.setattr(plugins_cmd, "_clone_plugin_repo", clone)
    monkeypatch.setattr(plugins_cmd, "_scan_plugin_tree", scan)
    (home / "config.yaml").write_text(
        "plugins:\n  enabled: [alibaba]\n  scan_on_install: true\n")

    target, manifest, name = plugins_cmd._install_plugin_core(identifier, force=False)

    assert target == home / "plugins" / "alibaba"
    assert name == manifest["name"] == "alibaba"
    assert manifest["kind"] == "backend"
    assert "requires_env" not in manifest
    clone.assert_called_once()
    scan.assert_called_once()
    assert scan.call_args.args[1] == identifier
    assert scan.call_args.kwargs["force"] is False
    # only plugin/ files are installed, repo extras are not
    assert json.loads((home / "plugins" / ".install-metadata.json").read_text()) == {
        "alibaba": {
            "pinned": False,
            "revision": revision,
            "source": "https://github.com/example/hermes-plugin-alibaba-image-gen.git#plugins/image_gen_alibaba",
        },
    }
    for filename in files:
        assert (target / filename).read_bytes() == (PLUGIN / filename).read_bytes()
        assert not (target / filename).is_symlink()
    assert not (target / "README.md").exists()
    assert not (target / "tests").exists()
    assert not (target / "pyproject.toml").exists()

    monkeypatch.setattr(sys, "path", [
        entry for entry in sys.path
        if not Path(entry).resolve().is_relative_to(ROOT)
        and not Path(entry).resolve().is_relative_to(PLUGIN)
        and Path(entry).resolve() != ROOT.parent
    ])
    monkeypatch.delitem(sys.modules, "alibaba", raising=False)
    monkeypatch.delitem(sys.modules, ROOT.name, raising=False)
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda: importlib.metadata.EntryPoints())
    manager = plugins.PluginManager(scope_key=str(home.resolve()))
    try:
        manager.discover_and_load()
        assert set(manager._plugins) == {"alibaba"}
        loaded = manager._plugins["alibaba"]
        assert loaded.enabled, loaded.error
        assert loaded.error is None
        assert loaded.manifest.source == "user"
        assert Path(loaded.manifest.path) == target
        assert Path(loaded.module.__file__).resolve() == target / "__init__.py"
        provider = image_gen_registry.get_provider("alibaba", scope=manager.scope_key)
        assert isinstance(provider, loaded.module.AlibabaImageGenProvider)
        assert provider.name == "alibaba"
        provider_module = sys.modules[type(provider).__module__]
        assert Path(provider_module.__file__).resolve() == target / "alibaba.py"
        assert "alibaba" not in sys.modules
    finally:
        manager.unload()
    assert image_gen_registry.snapshot_registration("alibaba", scope=manager.scope_key) is None
