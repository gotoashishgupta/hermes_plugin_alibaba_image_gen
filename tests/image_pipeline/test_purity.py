"""The harness-independence contract: importing the package touches stdlib ONLY.

Runs in a fresh interpreter (-I: no site-packages, no env) so a stray module-level
``import requests`` or hermes import fails this test, not just a live environment.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "image-pipeline" / "scripts"


def test_package_imports_with_bare_interpreter():
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "import imagegen.cli, imagegen.routing, imagegen.hermes_mode\n"
        "import imagegen.alibaba, imagegen.openai_compat, imagegen.openrouter\n"
        "import imagegen.fal, imagegen.gemini, imagegen.http, imagegen.images\n"
        "import imagegen.refs, imagegen.envelope, imagegen.adapters\n"
        "blocked = [m for m in ('requests','yaml','PIL','openai','hermes_cli','agent')\n"
        "           if m in sys.modules]\n"
        "print('BLOCKED:', blocked)\n"
        "sys.exit(1 if blocked else 0)\n" % str(SCRIPTS)
    )
    proc = subprocess.run([sys.executable, "-I", "-c", code],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_entrypoint_help_runs_without_hermes():
    proc = subprocess.run([sys.executable, "-I", str(SCRIPTS / "imagegen.py"), "--help"],
                          capture_output=True, text=True, timeout=60,
                          env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent-home"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "standalone" in proc.stdout
