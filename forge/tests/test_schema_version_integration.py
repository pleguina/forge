"""
Integration tests for schema_version wiring into
design.yml (DesignConfig/DesignValidator) and modules.yml (RegistryValidator).
Unit tests for the shared policy itself live in test_schema_version.py.
"""

from __future__ import annotations

from pathlib import Path

from forge.contracts.config import DesignConfig
from forge.generation.validation import validate_design, validate_registry

_MINIMAL_DESIGN_BODY = """\
part: xcvu13p
clock_period: 4.0
modules:
  - name: m1
    top: m1_top
    src: [m1.v]
"""


def _write_design(tmp_path: Path, *, schema_version: str | None = None) -> Path:
    design = tmp_path / "design.yml"
    body = _MINIMAL_DESIGN_BODY
    if schema_version is not None:
        body = f"schema_version: {schema_version!r}\n" + body
    design.write_text(body)
    return design


class TestDesignSchemaVersion:
    def test_no_schema_version_produces_no_schema_diagnostic(self, tmp_path):
        cfg = DesignConfig.load_relaxed(_write_design(tmp_path))
        assert cfg.schema_version is None
        validator = validate_design(cfg)
        assert not any(e.category == "schema" for e in validator.errors + validator.warnings)

    def test_matching_schema_version_produces_no_schema_diagnostic(self, tmp_path):
        cfg = DesignConfig.load_relaxed(_write_design(tmp_path, schema_version="1.0"))
        assert cfg.schema_version == "1.0"
        validator = validate_design(cfg)
        assert not any(e.category == "schema" for e in validator.errors + validator.warnings)

    def test_different_major_schema_version_is_a_validation_error(self, tmp_path):
        cfg = DesignConfig.load_relaxed(_write_design(tmp_path, schema_version="2.0"))
        validator = validate_design(cfg)
        assert any(
            e.category == "schema" and "incompatible" in e.message
            for e in validator.errors
        )
        assert not validator.validate_all()


class TestRegistrySchemaVersion:
    def test_legacy_bare_integer_registry_version_produces_no_new_issue(self, tmp_path):
        registry = tmp_path / "modules.yml"
        registry.write_text(
            "registry_version: '1'\n"
            "modules:\n"
            "  - name: dec\n"
            "    kind: rtl\n"
            "    top: dec_top\n"
            "    src: [dec.v]\n"
        )
        validator = validate_registry(registry)
        assert not any(e.category == "schema" for e in validator.errors + validator.warnings)

    def test_major_mismatched_registry_version_is_an_error(self, tmp_path):
        registry = tmp_path / "modules.yml"
        registry.write_text(
            "registry_version: '2'\n"
            "modules:\n"
            "  - name: dec\n"
            "    kind: rtl\n"
            "    top: dec_top\n"
            "    src: [dec.v]\n"
        )
        validator = validate_registry(registry)
        assert validator.has_errors()
        assert any("incompatible" in str(e) for e in validator.errors)
