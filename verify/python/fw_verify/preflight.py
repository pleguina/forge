#!/usr/bin/env python3
"""Generic artifact preflight checks for DUT-based simulation flows.

Responsibility boundary
-----------------------
This module owns:
  * Existence checks for required DUT artifacts
  * port_signature ↔ port_map hash consistency check
  * Actionable, uniform error messages naming the regeneration command

This module does NOT own:
  * Schema parsing / path resolution           → plugin flow loader
  * Compile-project generation                 → plugin backend prepare
  * Simulation orchestration                   → plugin run launcher

For v1.0 verification, the framework-standard dataset contract is XML-backed.
Preflight therefore validates the effective XML dataset path that will be used
for execution (flow default or ``--xml-input`` override).

Python API
----------
    from fw_verify.preflight import run_preflight, PreflightResult
    result = run_preflight(cfg)
    if not result.ok:
        print(result.format_errors())
        sys.exit(1)

The ``cfg`` argument is any object that exposes the following attributes
(all defined on the framework generic ``FlowConfig`` and its subclasses):

  cfg.dut_rtl            Path
  cfg.dut_manifest       Path
  cfg.dut_tb_bindings    Path
  cfg.dut_port_map       Path
  cfg.dut_port_signature Path
  cfg.dut_probe_map      Path | None
  cfg.dataset_xml        Path
  cfg.checker            CheckerConfig | None   (checker.observed_log: Path)
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fw_verify.flow_loader import FlowConfig


# ── Result model ───────────────────────────────────────────────────────────

@dataclass
class PreflightResult:
    errors:   list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes:    list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def format_errors(self) -> str:
        lines = [
            "[preflight] FAILED — the following artifacts are missing or inconsistent:"
        ]
        for e in self.errors:
            lines.append(f"  ERROR: {e}")
        for w in self.warnings:
            lines.append(f"  WARNING: {w}")
        return "\n".join(lines)

    def print_summary(self) -> None:
        for n in self.notes:
            print(f"[preflight] {n}")
        for w in self.warnings:
            print(f"[preflight] WARNING: {w}", file=sys.stderr)
        if self.ok:
            print("[preflight] All artifact checks passed.")
        else:
            print(self.format_errors(), file=sys.stderr)


# ── Individual checks ──────────────────────────────────────────────────────

def _check_exists(path: Path, label: str, result: PreflightResult) -> bool:
    """Verify a required artifact file exists. Returns True if present."""
    if not path.exists():
        result.errors.append(
            f"{label} not found: {path}\n"
            f"    → Re-run: topgen gen-top ..."
        )
        return False
    return True


def _check_port_signature(
    sig_path: Path,
    port_map_path: Path,
    result: PreflightResult,
) -> None:
    """Verify port_signature and port_map carry identical interface hashes."""
    try:
        import yaml as _yaml
    except ImportError:
        result.warnings.append(
            "PyYAML not available — skipping port_map ↔ port_signature hash check"
        )
        return

    try:
        sig_data = json.loads(sig_path.read_text())
        pm_data  = _yaml.safe_load(port_map_path.read_text()) or {}
    except Exception as exc:
        result.errors.append(f"Could not read signature artifacts: {exc}")
        return

    sig_hash = sig_data.get("hash", "")
    pm_hash  = pm_data.get("port_signature_hash", "")

    if not sig_hash:
        result.errors.append(
            f"port_signature is missing 'hash' field: {sig_path}"
        )
        return
    if not pm_hash:
        result.warnings.append(
            f"port_map is missing 'port_signature_hash' field: {port_map_path}"
        )
        return

    if sig_hash != pm_hash:
        result.errors.append(
            f"Interface fingerprint mismatch — DUT artifacts are out of sync:\n"
            f"    port_map       port_signature_hash: {pm_hash!r}\n"
            f"    port_signature hash:                {sig_hash!r}\n"
            f"    → Re-run: topgen gen-top ..."
        )
    else:
        result.notes.append(f"Interface fingerprint OK: {sig_hash}")


# ── Main preflight entry point ─────────────────────────────────────────────

def run_preflight(cfg: "Any", xml_input: "Path | None" = None) -> PreflightResult:
    """Run all artifact existence and consistency checks for the given flow.

    Works with any flow config object that exposes the documented attributes
    (framework ``FlowConfig`` or plugin subclass).

    Does not perform any I/O beyond reading artifact files.
    Returns a PreflightResult; callers decide whether to abort on failure.
    """
    result = PreflightResult()

    # 1. Required DUT artifacts must exist.
    _check_exists(cfg.dut_rtl,            "DUT RTL",         result)

    # manifest, port_map, tb_bindings, port_signature are optional for
    # single-module HLS flows (no gen-top outputs).  Only check when set.
    if cfg.dut_manifest is not None:
        _check_exists(cfg.dut_manifest,       "Build manifest",  result)
    if cfg.dut_tb_bindings is not None:
        _check_exists(cfg.dut_tb_bindings,    "TB bindings",     result)
    pm_present  = cfg.dut_port_map is not None and _check_exists(
        cfg.dut_port_map,       "Port map",        result)
    sig_present = cfg.dut_port_signature is not None and _check_exists(
        cfg.dut_port_signature, "Port signature",  result)

    # 2. Signature consistency: only if both files exist.
    if pm_present and sig_present:
        _check_port_signature(cfg.dut_port_signature, cfg.dut_port_map, result)

    # 3. Optional probe_map: warn only.
    if cfg.dut_probe_map and not cfg.dut_probe_map.exists():
        result.warnings.append(
            f"Probe map not found (optional): {cfg.dut_probe_map}"
        )

    # 4. Framework-standard XML dataset source must exist. ``--xml-input``
    #    overrides the flow-YAML default so check whichever path will actually
    #    be used for this run.
    _effective_xml = xml_input if xml_input is not None else cfg.dataset_xml
    _check_exists(_effective_xml, "Dataset XML", result)

    # 5. Checker observed_log parent dir should be accessible.
    if cfg.checker and not cfg.checker.observed_log.parent.exists():
        result.warnings.append(
            f"Checker output dir does not exist yet (will be created): "
            f"{cfg.checker.observed_log.parent}"
        )

    # 6. For RTL simulation flows: check that the generated testbench and the
    #    stimulus handoff file are present before execution begins.
    _flow_kind = getattr(cfg, "flow_kind", "")
    if _flow_kind in ("full_chip_rtl", "reduced_chain_rtl", "single_module_rtl"):
        _flow_dir = getattr(cfg, "flow_file", None)
        if _flow_dir is not None:
            _flow_dir = Path(_flow_dir).parent
            _tb_name  = getattr(cfg, "tb_module", "")
            _tb_file  = _flow_dir / f"{_tb_name}.sv"
            if _tb_name and not _tb_file.exists():
                result.errors.append(
                    f"Generated testbench not found: {_tb_file}\n"
                    f"    → Re-run: fw_verify generate <design.verification.yml>"
                    f" --flow {getattr(cfg, 'flow_name', '<flow>')}"
                )
            _stim_file = _flow_dir / "stimulus_current.svh"
            if not _stim_file.exists():
                result.errors.append(
                    f"Stimulus handoff file not found: {_stim_file}\n"
                    f"    → Re-run: plugin stimulus generator for this flow\n"
                    f"    → See: docs/STIMULUS_HANDOFF_SPEC.md"
                )
            else:
                # Validate stimulus contract when the file exists.
                from fw_verify.stimulus_contract import validate_stimulus  # noqa: PLC0415
                _sc = validate_stimulus(_stim_file)
                for _e in _sc.errors:
                    result.errors.append(_e)
                for _w in _sc.warnings:
                    result.warnings.append(_w)

    # 7. Check DUT top module name exists in the referenced RTL file.
    _dut_rtl   = getattr(cfg, "dut_rtl", None)
    _top_mod   = getattr(cfg, "top_module", None)
    if _dut_rtl and _top_mod and Path(_dut_rtl).exists():
        _rtl_text = Path(_dut_rtl).read_text(errors="replace")
        # Very lightweight check: looks for `module <name>` declaration.
        import re as _re  # noqa: PLC0415
        if not _re.search(
            r"\bmodule\s+" + _re.escape(_top_mod) + r"\s*[(\s;#]",
            _rtl_text,
        ):
            result.warnings.append(
                f"DUT top module {_top_mod!r} not found in RTL file {_dut_rtl}.\n"
                f"    Verify that 'top_module' in the flow config matches the RTL."
            )

    # 8. Check canonical layout: no legacy kind-subdir directories present.
    _flow_file = getattr(cfg, "flow_file", None)
    if _flow_file is not None:
        from fw_verify.layout import validate_layout as _vl  # noqa: PLC0415
        _verify_root = Path(_flow_file).parent.parent
        _layout_errors = _vl(_verify_root)
        for _le in _layout_errors:
            result.warnings.append(_le)

    return result
