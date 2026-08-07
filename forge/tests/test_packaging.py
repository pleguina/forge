"""Guards against forge/pyproject.toml's hand-maintained `packages` list
drifting from the real package tree on disk.

This is exactly the class of bug found and fixed in a real release audit:
`forge.core.cli.groups`, `forge.framework`, `forge.docsgen`,
`forge.analysis.throughput_static`, and `forge.analysis.throughput_runtime`
were all real, imported-elsewhere packages missing from the declared list
— invisible until a clean-venv wheel install crashed on every `forge`
invocation. `[tool.setuptools.packages.find]` auto-discovery was tried as
a fix and rejected: this repo's `package-dir = {"forge" = "."}` remap
(pyproject.toml lives inside the package it describes) doesn't compose
safely with setuptools' package finder — one config silently produced an
empty wheel, another physically duplicated the source tree into a stray
`forge/forge/` directory. A drift-detection test achieves the same
protection without touching the packaging mechanism.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "forge" / "pyproject.toml"
FORGE_ROOT = REPO_ROOT / "forge"

_EXCLUDED_DIR_NAMES = {"tests", "__pycache__", "build", "forge.egg-info"}


def _declared_packages() -> "set[str]":
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return set(config["tool"]["setuptools"]["packages"])


def _real_packages_on_disk() -> "set[str]":
    """Every directory under forge/ with an __init__.py, as a dotted
    package name, excluding the test suite and build/cache artifacts."""
    found: "set[str]" = {"forge"}
    for init_file in FORGE_ROOT.rglob("__init__.py"):
        rel_dir = init_file.parent.relative_to(FORGE_ROOT)
        if any(part in _EXCLUDED_DIR_NAMES for part in rel_dir.parts):
            continue
        if rel_dir == Path("."):
            continue
        found.add("forge." + ".".join(rel_dir.parts))
    return found


def test_declared_packages_match_real_package_tree() -> None:
    declared = _declared_packages()
    real = _real_packages_on_disk()

    missing_from_pyproject = real - declared
    stale_in_pyproject = declared - real

    assert not missing_from_pyproject, (
        f"forge/pyproject.toml's packages list is missing real packages: "
        f"{sorted(missing_from_pyproject)} — a clean wheel install will "
        f"omit them (this exact bug broke `forge --version` on a clean "
        f"install once already). Add them to [tool.setuptools] packages."
    )
    assert not stale_in_pyproject, (
        f"forge/pyproject.toml declares packages that no longer exist on "
        f"disk: {sorted(stale_in_pyproject)} — remove them from "
        f"[tool.setuptools] packages."
    )
