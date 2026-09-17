"""Leaves install standalone, so the Hermes leaf ships its own COPY of the
shared files. That copy must be byte-identical or it's a silent fork — this
test is the drift gate: sha256 over scripts/ (tree) and the shared references.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
GENERIC = _ROOT / "skills" / "image-pipeline"
HERMES = _ROOT / "skills" / "hermes-image-pipeline"


def _tree(root: Path) -> dict:
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            rel = str(path.relative_to(root))
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def test_leaves_exist():
    assert (GENERIC / "SKILL.md").is_file(), "generic leaf missing"
    assert (HERMES / "SKILL.md").is_file(), "hermes leaf missing"


def test_scripts_trees_are_byte_identical():
    generic = _tree(GENERIC / "scripts")
    hermes = _tree(HERMES / "scripts")
    assert generic, "generic scripts/ empty"
    only_g = sorted(set(generic) - set(hermes))
    only_h = sorted(set(hermes) - set(generic))
    drift = sorted(k for k in set(generic) & set(hermes) if generic[k] != hermes[k])
    assert not (only_g or only_h or drift), (
        f"leaf drift — missing in hermes: {only_g}; extra: {only_h}; changed: {drift}")


@pytest.mark.parametrize("ref", ["references/pipeline.md", "references/prompt-guide.md"])
def test_shared_references_identical(ref):
    g = (GENERIC / ref).read_bytes()
    h = (HERMES / ref).read_bytes()
    assert hashlib.sha256(g).hexdigest() == hashlib.sha256(h).hexdigest(), \
        f"{ref} drifted between leaves"
