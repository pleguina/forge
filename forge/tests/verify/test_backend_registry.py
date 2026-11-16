"""Unit tests for forge.verification.backend_registry: dispatch errors,
artifact checks and introspection, with stand-in backend modules registered
under names of their own. The autouse fixture in tests/conftest.py restores
the framework's own registrations after each test.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from forge.verification import backend_registry as registry
from forge.verification.backend_base import ArtifactRequirement
from forge.verification.backend_xsim import ADAPTER as XSIM


def _module(monkeypatch: pytest.MonkeyPatch, name: str, **attrs) -> None:
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)


def test_unknown_backend_names_the_registered_ones() -> None:
    with pytest.raises(ValueError, match=r"Unsupported backend: 'nosuch'.*xsim"):
        registry.get_adapter("nosuch")


def test_a_module_that_does_not_import_is_reported_with_its_backend() -> None:
    registry.register_backend("ghost", "forge_tests_no_such_backend_module")

    with pytest.raises(ImportError, match="'forge_tests_no_such_backend_module' for backend 'ghost'"):
        registry.get_adapter("ghost")


def test_a_module_without_adapter_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _module(monkeypatch, "forge_tests_bare_backend")
    registry.register_backend("bare", "forge_tests_bare_backend")

    with pytest.raises(AttributeError, match="does not export an 'ADAPTER'"):
        registry.get_adapter("bare")


def test_an_adapter_of_the_wrong_type_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _module(monkeypatch, "forge_tests_odd_backend", ADAPTER=object())
    registry.register_backend("odd", "forge_tests_odd_backend")

    with pytest.raises(TypeError, match="expected a BackendAdapter instance"):
        registry.get_adapter("odd")


def test_registering_a_backend_lists_it_and_updates_the_compatibility_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _module(monkeypatch, "forge_tests_xsim_alias", ADAPTER=XSIM)
    before = registry.get_compatible_pairs()
    registry.register_backend("xsim_alias", "forge_tests_xsim_alias")

    assert "xsim_alias" in registry.list_supported_backends()
    assert "xsim_alias" in registry.get_allowed_backends()
    added = registry.get_compatible_pairs() - before
    assert added == {(kind, "xsim_alias") for kind in XSIM.compatible_flow_kinds}


def test_artifact_requirements(tmp_path: Path) -> None:
    present = tmp_path / "present.v"
    present.write_text("module m; endmodule\n")
    adapter = SimpleNamespace(required_artifacts=[
        ArtifactRequirement("custom"),                                   # adapter checks it itself
        ArtifactRequirement("rtl", path_attr="rtl"),
        ArtifactRequirement("unset", path_attr="unset"),
        ArtifactRequirement("unset_optional", path_attr="unset_optional", optional=True),
        ArtifactRequirement("missing", path_attr="missing"),
        ArtifactRequirement("missing_optional", path_attr="missing_optional", optional=True),
    ])
    cfg = SimpleNamespace(
        rtl=present, missing=tmp_path / "missing.v", missing_optional=tmp_path / "gone.v",
    )

    assert registry.validate_artifact_requirements(adapter, cfg) == [
        "Required artifact 'unset': path not set in flow config",
        f"Required artifact 'missing' not found: {tmp_path / 'missing.v'}",
    ]


def test_introspect_reports_available_and_unavailable_backends() -> None:
    registry.register_backend("ghost", "forge_tests_no_such_backend_module")

    snapshot = registry.introspect()

    assert snapshot["backends"]["ghost"]["status"] == "unavailable"
    xsim = snapshot["backends"]["xsim"]
    assert xsim["status"] == "available"
    assert xsim["required_tools"] == list(XSIM.required_tools)
    assert {"kind": "full_chip_rtl", "backend": "xsim"} in snapshot["compatibility_matrix"]
    assert all(entry["backend"] != "ghost" for entry in snapshot["compatibility_matrix"])
