"""
Tests for forge.topgen.config.ModuleTiming — optional per-module latency
metadata.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from topgen.config import DesignConfig, LatencyDeclaration, ModuleTiming, _pop_timing
from topgen.validation import validate_registry


# ─────────────────────────────────────────────────────────────────────────────
# ModuleTiming construction
# ─────────────────────────────────────────────────────────────────────────────

def test_fixed_only_is_valid():
    t = ModuleTiming(latency_cycles=7)
    assert t.latency_cycles == 7
    assert t.latency_hint is None
    assert t.variable_latency is False


def test_hint_only_is_valid():
    t = ModuleTiming(latency_hint=3)
    assert t.latency_cycles is None
    assert t.latency_hint == 3


def test_variable_only_is_valid():
    t = ModuleTiming(variable_latency=True)
    assert t.latency_cycles is None
    assert t.variable_latency is True


def test_absent_is_valid_and_default():
    t = ModuleTiming()
    assert t.latency_cycles is None
    assert t.latency_hint is None
    assert t.variable_latency is False


def test_fixed_and_variable_is_rejected():
    with pytest.raises(ValueError, match="contradictory"):
        ModuleTiming(latency_cycles=7, variable_latency=True)


# ─────────────────────────────────────────────────────────────────────────────
# _pop_timing
# ─────────────────────────────────────────────────────────────────────────────

def test_pop_timing_returns_none_when_absent():
    raw = {"name": "mod", "kind": "rtl"}
    assert _pop_timing(raw) is None
    assert raw == {"name": "mod", "kind": "rtl"}  # untouched


def test_pop_timing_extracts_and_removes_raw_keys():
    raw = {"name": "mod", "latency_hint": 3}
    timing = _pop_timing(raw)
    assert timing == ModuleTiming(latency_hint=3)
    assert "latency_hint" not in raw


def test_pop_timing_malformed_value_raises():
    raw = {"latency_cycles": "not-a-number"}
    with pytest.raises(ValueError):
        _pop_timing(raw)


def test_pop_timing_conflicting_fields_raises():
    raw = {"latency_cycles": 7, "variable_latency": True}
    with pytest.raises(ValueError, match="contradictory"):
        _pop_timing(raw)


# ─────────────────────────────────────────────────────────────────────────────
# Integration: DesignConfig loading real designs
# ─────────────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]
TRIGGER_DESIGN = REPO_ROOT / "plugins/trigger_demo/forge/designs/design.yml"
PASSTHROUGH_DESIGN = REPO_ROOT / "plugins/passthrough_demo/forge/designs/design.yml"


def test_trigger_demo_modules_have_timing_from_registry():
    cfg = DesignConfig.load_relaxed(TRIGGER_DESIGN)
    trig = next(m for m in cfg.modules if m.name == "trig")
    assert trig.timing is not None
    # Phase 4 slice 2: migrated to the structured latency: {kind: fixed,
    # cycles: 3} spelling (a real, known-fixed HLS latency, not a rough
    # estimate) — see plugins/trigger_demo/forge/modules.yml.
    assert trig.timing.latency_hint is None
    assert trig.timing.latency.kind == "fixed"
    assert trig.timing.latency.cycles == 3


def test_passthrough_demo_module_has_timing_from_registry():
    cfg = DesignConfig.load_relaxed(PASSTHROUGH_DESIGN)
    pt = next(m for m in cfg.modules if m.name == "pt")
    assert pt.timing is not None
    assert pt.timing.latency_hint == 1


def test_module_with_no_timing_fields_declared_has_none(tmp_path):
    """A module that declares no latency fields at all must have
    Module.timing stay None (not a ModuleTiming with all-None fields), so
    callers can distinguish "not declared" from "declared as unknown"."""
    design = tmp_path / "design.yml"
    design.write_text(
        "part: xcvu13p\n"
        "clock_period: 4.0\n"
        "modules:\n"
        "  - name: plain\n"
        "    kind: rtl\n"
        "    top: plain_top\n"
        "    src: []\n"
    )
    cfg = DesignConfig.load_relaxed(design)
    assert cfg.modules[0].timing is None


def test_inline_design_level_override_wins_over_registry(tmp_path):
    """A design.yml module entry that inlines latency_hint (bypassing the
    registry) must win over whatever the registry declares for that ref —
    same override precedence as every other module field."""
    registry = tmp_path / "modules.yml"
    registry.write_text(
        "modules:\n"
        "  - name: dec\n"
        "    kind: rtl\n"
        "    top: dec_top\n"
        "    src: []\n"
        "    latency_hint: 5\n"
    )
    design = tmp_path / "design.yml"
    design.write_text(
        "part: xcvu13p\n"
        "clock_period: 4.0\n"
        "registry: modules.yml\n"
        "modules:\n"
        "  - name: dec_inst\n"
        "    ref: dec\n"
        "    latency_hint: 9\n"
    )
    cfg = DesignConfig.load_relaxed(design)
    mod = cfg.modules[0]
    assert mod.timing.latency_hint == 9


# ─────────────────────────────────────────────────────────────────────────────
# RegistryValidator: conflicting-fields case is a structured error, not a crash
# ─────────────────────────────────────────────────────────────────────────────

def test_registry_validator_reports_conflicting_latency_fields(tmp_path):
    registry = tmp_path / "modules.yml"
    registry.write_text(
        "modules:\n"
        "  - name: dec\n"
        "    kind: rtl\n"
        "    top: dec_top\n"
        "    src: [dec.v]\n"
        "    latency_cycles: 7\n"
        "    variable_latency: true\n"
    )
    validator = validate_registry(registry)
    assert validator.has_errors()
    assert any("contradictory" in str(e) for e in validator.errors)


def test_registry_validator_passes_with_only_latency_hint(tmp_path):
    registry = tmp_path / "modules.yml"
    registry.write_text(
        "modules:\n"
        "  - name: dec\n"
        "    kind: rtl\n"
        "    top: dec_top\n"
        "    src: [dec.v]\n"
        "    latency_hint: 3\n"
    )
    validator = validate_registry(registry)
    assert not validator.has_errors()


def test_registry_validator_passes_with_no_timing_fields(tmp_path):
    registry = tmp_path / "modules.yml"
    registry.write_text(
        "modules:\n"
        "  - name: dec\n"
        "    kind: rtl\n"
        "    top: dec_top\n"
        "    src: [dec.v]\n"
    )
    validator = validate_registry(registry)
    assert not validator.has_errors()


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 slice 2 — LatencyDeclaration (release-plan §4.2)
# ─────────────────────────────────────────────────────────────────────────────

def test_latency_declaration_fixed_requires_cycles():
    d = LatencyDeclaration(kind="fixed", cycles=7)
    assert d.cycles == 7
    with pytest.raises(ValueError, match="requires 'cycles'"):
        LatencyDeclaration(kind="fixed")


def test_latency_declaration_bounded_requires_min_and_max():
    d = LatencyDeclaration(kind="bounded", min_cycles=3, max_cycles=8)
    assert (d.min_cycles, d.max_cycles) == (3, 8)
    with pytest.raises(ValueError, match="requires both"):
        LatencyDeclaration(kind="bounded", min_cycles=3)


def test_latency_declaration_bounded_rejects_min_greater_than_max():
    with pytest.raises(ValueError, match="must be <="):
        LatencyDeclaration(kind="bounded", min_cycles=8, max_cycles=3)


def test_latency_declaration_elastic_rejects_stray_cycle_fields():
    d = LatencyDeclaration(kind="elastic")
    assert d.cycles is None
    with pytest.raises(ValueError, match="must not declare"):
        LatencyDeclaration(kind="elastic", cycles=3)


def test_latency_declaration_rejects_unknown_kind():
    with pytest.raises(ValueError, match="must be one of"):
        LatencyDeclaration(kind="asynchronous")


def test_pop_timing_parses_fixed_latency_block():
    raw = {"latency": {"kind": "fixed", "cycles": 3}}
    timing = _pop_timing(raw)
    assert timing.latency == LatencyDeclaration(kind="fixed", cycles=3)
    assert timing.latency_cycles is None  # the flat field stays unpopulated
    assert "latency" not in raw


def test_pop_timing_parses_bounded_latency_block():
    raw = {"latency": {"kind": "bounded", "min_cycles": 2, "max_cycles": 5}}
    timing = _pop_timing(raw)
    assert timing.latency == LatencyDeclaration(kind="bounded", min_cycles=2, max_cycles=5)


def test_pop_timing_elastic_block_normalizes_variable_latency():
    """latency: {kind: elastic} is the soft-migration alias for
    variable_latency: true — every existing variable_latency consumer
    (e.g. the checker's is_variable folding) must keep working unchanged."""
    raw = {"latency": {"kind": "elastic"}}
    timing = _pop_timing(raw)
    assert timing.variable_latency is True
    assert timing.latency == LatencyDeclaration(kind="elastic")


def test_pop_timing_rejects_both_syntaxes_on_one_module():
    raw = {"latency": {"kind": "fixed", "cycles": 3}, "latency_hint": 2}
    with pytest.raises(ValueError, match="two ways of declaring the same thing"):
        _pop_timing(raw)


def test_pop_timing_rejects_flat_variable_latency_plus_latency_block():
    raw = {"latency": {"kind": "elastic"}, "variable_latency": True}
    with pytest.raises(ValueError, match="two ways of declaring the same thing"):
        _pop_timing(raw)


# ─────────────────────────────────────────────────────────────────────────────
# RegistryValidator: latency: {...} structural validation
# ─────────────────────────────────────────────────────────────────────────────

def _registry_with_latency_block(tmp_path, block_yaml: str):
    registry = tmp_path / "modules.yml"
    registry.write_text(
        "modules:\n"
        "  - name: dec\n"
        "    kind: rtl\n"
        "    top: dec_top\n"
        "    src: [dec.v]\n"
        f"    latency:\n{block_yaml}"
    )
    return registry


def test_registry_validator_accepts_valid_fixed_block(tmp_path):
    registry = _registry_with_latency_block(tmp_path, "      kind: fixed\n      cycles: 3\n")
    validator = validate_registry(registry)
    assert not validator.has_errors()


def test_registry_validator_accepts_valid_bounded_block(tmp_path):
    registry = _registry_with_latency_block(
        tmp_path, "      kind: bounded\n      min_cycles: 2\n      max_cycles: 5\n",
    )
    validator = validate_registry(registry)
    assert not validator.has_errors()


def test_registry_validator_accepts_valid_elastic_block(tmp_path):
    registry = _registry_with_latency_block(tmp_path, "      kind: elastic\n")
    validator = validate_registry(registry)
    assert not validator.has_errors()


def test_registry_validator_rejects_bad_kind(tmp_path):
    registry = _registry_with_latency_block(tmp_path, "      kind: asynchronous\n")
    validator = validate_registry(registry)
    assert validator.has_errors()
    assert any("must be one of" in str(e) for e in validator.errors)


def test_registry_validator_rejects_fixed_missing_cycles(tmp_path):
    registry = _registry_with_latency_block(tmp_path, "      kind: fixed\n")
    validator = validate_registry(registry)
    assert validator.has_errors()
    assert any("no 'cycles'" in str(e) for e in validator.errors)


def test_registry_validator_rejects_bounded_min_greater_than_max(tmp_path):
    registry = _registry_with_latency_block(
        tmp_path, "      kind: bounded\n      min_cycles: 8\n      max_cycles: 3\n",
    )
    validator = validate_registry(registry)
    assert validator.has_errors()
    assert any("min_cycles" in str(e) and "max_cycles" in str(e) for e in validator.errors)


def test_registry_validator_rejects_elastic_with_stray_cycles(tmp_path):
    registry = _registry_with_latency_block(tmp_path, "      kind: elastic\n      cycles: 3\n")
    validator = validate_registry(registry)
    assert validator.has_errors()
    assert any("no fixed cycle count" in str(e) for e in validator.errors)


def test_registry_validator_rejects_unknown_key_in_latency_block(tmp_path):
    registry = _registry_with_latency_block(
        tmp_path, "      kind: fixed\n      cycles: 3\n      frobnicate: true\n",
    )
    validator = validate_registry(registry)
    assert validator.has_errors()
    assert any("unknown key" in str(e) for e in validator.errors)


# ─────────────────────────────────────────────────────────────────────────────
# Real-design integration: IR/graph pick up the structured latency: block
# ─────────────────────────────────────────────────────────────────────────────

def test_resolved_module_definition_carries_latency_declaration(tmp_path):
    """forge.ir.build.assemble_project_ir populates
    ResolvedModuleDefinition.latency from Module.timing.latency, additive
    alongside the existing flat fields."""
    from forge.ir.build import build_project_ir

    design = tmp_path / "design.yml"
    design.write_text(
        "part: xcvu13p\n"
        "clock_period: 4.0\n"
        "modules:\n"
        "  - name: m\n"
        "    kind: rtl\n"
        "    top: m_top\n"
        "    src: [m.v]\n"
        "    latency:\n"
        "      kind: fixed\n"
        "      cycles: 4\n"
    )
    project = build_project_ir(design)
    mod = project.design.modules[0]
    # Field-by-field, not a full dataclass == : forge.ir.build imports
    # LatencyDeclaration via the "forge.topgen.config" module path while
    # this test file uses the short "topgen.config" path — same source
    # file, but two distinct sys.modules entries/classes, so a direct
    # dataclass equality would spuriously fail despite identical data.
    assert mod.latency.kind == "fixed"
    assert mod.latency.cycles == 4
