"""Proves forge/topgen/{generators,ip} (deprecation-window compatibility
shims for forge.generation.generators / forge.contracts, see
docs/development/adr/0005-package-and-cli-naming.md) actually work:
identical objects through the old path, and a real DeprecationWarning on
first import — unlike forge.verify/forge.analyze, this pair is not kept
forever.
"""
from __future__ import annotations

import importlib
import sys
import warnings


def _fresh_import_with_warnings(module_name: str):
    for name in list(sys.modules):
        if name == module_name or name.startswith(module_name + "."):
            del sys.modules[name]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        module = importlib.import_module(module_name)
    return module, caught


def test_topgen_ip_shim_is_identical_to_contracts_and_warns() -> None:
    shim, caught = _fresh_import_with_warnings("forge.topgen.ip")
    import forge.contracts as real

    assert shim.auto_match_ports is real.auto_match_ports
    assert shim.load_ip_info is real.load_ip_info
    assert shim.__all__ == real.__all__
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)
    assert any("forge.contracts" in str(w.message) for w in caught)


def test_topgen_generators_shim_is_identical_to_generation_generators_and_warns() -> None:
    shim, caught = _fresh_import_with_warnings("forge.topgen.generators")
    import forge.generation.generators as real

    assert shim.write_structural_verilog is real.write_structural_verilog
    assert shim.write_structural_vhdl is real.write_structural_vhdl
    assert shim.write_bd_tcl is real.write_bd_tcl
    assert shim.__all__ == real.__all__
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)
    assert any("forge.generation.generators" in str(w.message) for w in caught)


def test_forge_top_level_reexports_resolve_to_the_new_canonical_modules() -> None:
    import forge

    assert forge.DesignConfig.__module__ == "forge.contracts.config"
    assert forge.write_structural_vhdl.__module__ == "forge.generation.generators.structural_vhdl"
