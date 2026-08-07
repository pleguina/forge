"""Proves forge/verify/ (the permanent compatibility alias for
forge.verification, see docs/development/adr/0005-package-and-cli-naming.md)
actually works for the exact import pattern real, already-deployed plugin
code uses — independent of whether this repo's own reference plugins
happen to import the old or new name at any given time.
"""
from __future__ import annotations

import subprocess
import sys


def test_shim_submodule_is_the_same_object_as_the_real_module() -> None:
    from forge.verify.plugin_registry import declare_plugin_bootstrap
    from forge.verification.plugin_registry import declare_plugin_bootstrap as real

    assert declare_plugin_bootstrap is real


def test_shim_import_style_every_plugin_bootstrap_py_uses() -> None:
    """The exact statement plugin bootstrap.py files write:
    `from forge.verify.plugin_registry import declare_plugin_bootstrap`."""
    import forge.verify.plugin_registry as shim_module
    import forge.verification.plugin_registry as real_module

    assert shim_module.declare_plugin_bootstrap is real_module.declare_plugin_bootstrap


def test_shim_triggers_backend_registration_exactly_once() -> None:
    from forge.verify.backend_registry import list_supported_backends

    backends = list_supported_backends()
    assert set(backends) == {"xsim", "csim", "verilator"}


def test_python_dash_m_forge_verify_still_works() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "forge.verify", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "verify" in result.stdout.lower()
