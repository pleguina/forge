"""forge.verify.supported_path_validator — Unified supported-path gate.

Aggregates all framework health checks into a single callable. This is the
canonical way to determine whether a design contract is on the supported path
(i.e., it works with standard ``forge verify generate / prepare / run`` commands
without unsupported workarounds).
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from forge.verify.exceptions import ForgeVerifyError


@dataclass
class SupportedPathResult:
    """Structured result from ``validate_supported_path``."""

    design_path: Path
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when no issues were found (warnings are tolerated)."""
        return len(self.issues) == 0

    @property
    def ok_strict(self) -> bool:
        """True when both issues and warnings are empty."""
        return len(self.issues) == 0 and len(self.warnings) == 0

    def __str__(self) -> str:  # pragma: no cover
        lines: list[str] = [f"SupportedPathResult({self.design_path})"]
        for note in self.notes:
            lines.append(f"  OK   {note}")
        for warning in self.warnings:
            lines.append(f"  WARN {warning}")
        for issue in self.issues:
            lines.append(f"  ERR  {issue}")
        lines.append(
            f"  → {'PASS' if self.ok else 'FAIL'}  "
            f"({len(self.notes)} OK, {len(self.warnings)} WARN, {len(self.issues)} ERR)"
        )
        return "\n".join(lines)


def validate_supported_path(
    design_path: "str | Path",
    consumer_root: "str | Path | None" = None,
    *,
    check_tools: bool = True,
    check_artifacts: bool = True,
    check_stimulus: bool = True,
) -> SupportedPathResult:
    """Run all supported-path checks against *design_path*."""
    design_path = Path(design_path).resolve()
    if consumer_root is None:
        consumer_root = design_path.parents[2]
    else:
        consumer_root = Path(consumer_root).resolve()

    result = SupportedPathResult(design_path=design_path)
    add_issue = result.issues.append
    add_warning = result.warnings.append
    add_note = result.notes.append

    if not design_path.exists():
        add_issue(f"design.verification.yml not found: {design_path}")
        return result
    add_note("design.verification.yml exists")

    from forge.verify.design_contract import load_verify_design

    try:
        contract = load_verify_design(design_path)
        add_note(f"contract loaded OK ({len(contract.flows)} flows, {len(contract.datasets)} datasets)")
    except ForgeVerifyError as exc:
        add_issue(f"contract parse error: {exc}  → {exc.action}")
        return result
    except Exception as exc:  # noqa: BLE001
        add_issue(f"contract parse error: {exc}")
        return result

    verify_root = design_path.parent

    from forge.verify.layout import validate_layout

    for layout_error in validate_layout(verify_root):
        add_issue(f"[FWV003] [layout] {layout_error}")

    bootstrap_py = verify_root / "tools" / "bootstrap.py"
    if bootstrap_py.exists():
        add_note("tools/bootstrap.py exists")
    else:
        add_issue(
            f"[FWV019] tools/bootstrap.py not found: {bootstrap_py}  "
            f"→ create with: forge verify init-plugin <plugin_id>"
        )

    from forge.verify.supported_matrix import validate_flow_matrix

    for flow in contract.flows:
        errors = validate_flow_matrix(flow.kind, flow.backend)
        if errors:
            code = "[FWV002]" if getattr(flow, "experimental", False) else "[FWV001]"
            if getattr(flow, "experimental", False):
                for error in errors:
                    add_warning(f"{code} [{flow.name}] experimental flow: {error}")
            else:
                for error in errors:
                    add_issue(f"{code} [{flow.name}] unsupported matrix: {error}")
        else:
            add_note(f"[{flow.name}] (kind={flow.kind}, backend={flow.backend}) supported")

    if check_tools:
        for tool in ("xvlog", "xelab", "xsim"):
            if shutil.which(tool):
                add_note(f"tool on PATH: {tool}")
            else:
                add_warning(
                    f"[FWV012] Vivado tool not on PATH: {tool!r}  "
                    f"(required for xsim flows; ignore for csim-only)"
                )

    csim_kinds = {"hls_csim", "hls_cosim"}

    for flow in contract.flows:
        prefix = f"[{flow.name}]"
        flow_yml = verify_root / flow.name / "verify.flow.yml"
        if flow_yml.exists():
            add_note(f"{prefix} verify.flow.yml present")
        else:
            add_warning(
                f"[FWV004] {prefix} verify.flow.yml missing  "
                f"→ forge verify generate {design_path} --flow {flow.name}"
            )

        if flow.kind in csim_kinds:
            from forge.verify.backend_csim import find_tb_binary

            tb_bin = find_tb_binary(flow.tb_module, consumer_root)
            if tb_bin:
                add_note(f"{prefix} csim binary found: {tb_bin}")
            else:
                add_warning(
                    f"[FWV018] {prefix} csim binary not found: {flow.tb_module!r}  "
                    f"→ build HLS testbench, or set CSIM_TB_{flow.tb_module.upper()} env var"
                )
            continue

        if not check_artifacts:
            continue

        from forge.verify.layout import resolve_generated_files

        generated = resolve_generated_files(
            verify_root,
            flow.name,
            flow.tb_module,
            is_csim=False,
        )

        if generated.tb_sv and generated.tb_sv.exists():
            add_note(f"{prefix} TB: {generated.tb_sv.name}")
        else:
            add_issue(
                f"[FWV004] {prefix} TB not found: {generated.tb_sv}  "
                f"→ forge verify generate {design_path} --flow {flow.name}"
            )

        if generated.port_map.exists():
            add_note(f"{prefix} port_map.yaml: present")
        else:
            add_warning(
                f"[FWV004] {prefix} port_map.yaml missing  "
                f"→ forge verify generate {design_path} --flow {flow.name}"
            )

        if not check_stimulus:
            continue

        if not generated.stimulus_svh.exists():
            add_warning(
                f"[FWV017] {prefix} stimulus_current.svh missing  "
                f"→ python3 {verify_root}/tools/gen_stimulus.py --flow {flow.name}"
            )
            continue

        from forge.verify.stimulus_contract import validate_stimulus

        stimulus_result = validate_stimulus(generated.stimulus_svh)
        if stimulus_result.ok:
            add_note(f"{prefix} stimulus_current.svh: contract OK")
        else:
            for error in stimulus_result.errors:
                add_issue(f"[FWV010] {prefix} stimulus: {error}")
        for warning in stimulus_result.warnings:
            add_warning(f"[FWV010] {prefix} stimulus: {warning}")

    return result


def is_on_supported_path(
    design_path: "str | Path",
    consumer_root: "str | Path | None" = None,
) -> bool:
    """Return True if *design_path* passes all supported-path checks."""
    return validate_supported_path(design_path, consumer_root).ok
