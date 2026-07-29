"""
Tests for forge.topgen.config.ModuleTiming — optional per-module latency
metadata (migration step 7, docs/development/release-readiness.md).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from topgen.config import DesignConfig, ModuleTiming, _pop_timing
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
    assert trig.timing.latency_hint == 3


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
