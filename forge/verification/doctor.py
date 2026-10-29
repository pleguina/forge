"""The read-only health check behind ``forge dev verify doctor``."""
from __future__ import annotations

from pathlib import Path

from forge.verification.diagnostics import DiagnosticReport, Severity
from forge.verification.run import resolve_consumer_root


def doctor_report(
    design_file: "str | Path", consumer_root: "str | Path | None" = None,
) -> DiagnosticReport:
    """Run a comprehensive, READ-ONLY health-check on a plugin's verify setup:
      1.  File existence + design contract loading
      2.  Bootstrap.py / gen_stimulus.py presence
      3.  RTL introspection mode available
      4.  Simulator tool availability
      5.  Supported (kind, backend) matrix per flow
      6.  Per-flow artifact completeness
      7.  Stimulus contract validity
      8.  Canonical layout compliance
      9.  Stale artifact detection
      10. Supported-path gate summary
    """
    from forge.core.toolchain_versions import tool_present

    design_path = Path(design_file).resolve()
    consumer_root = resolve_consumer_root(consumer_root) or Path.cwd()

    report = DiagnosticReport(label=design_path.name)

    # ── 1. File exists ─────────────────────────────────────────────────────
    if not design_path.exists():
        report.error(
            "FWV015",
            f"design.verification.yml not found: {design_path}",
            action="Create the file or check the path",
            path=str(design_path),
        )
        return report

    # ── 2. Load contract ───────────────────────────────────────────────────
    from forge.verification.design_contract import load_verify_design
    contract = None
    try:
        contract = load_verify_design(design_path)
        report.note(
            "FWV000",
            f"design.verification.yml loaded OK  ({len(contract.flows)} flows)",
            path=str(design_path),
        )
    except Exception as exc:  # noqa: BLE001
        report.error(
            "FWV015",
            f"design.verification.yml failed to parse: {exc}",
            action="Fix YAML syntax or schema errors in design.verification.yml",
            path=str(design_path),
        )

    # ── 3. Bootstrap + gen_stimulus presence ───────────────────────────────
    verify_root    = design_path.parent
    bootstrap_py   = verify_root / "tools" / "bootstrap.py"
    gen_stimulus_py = verify_root / "tools" / "gen_stimulus.py"

    if bootstrap_py.exists():
        report.note("FWV000", f"bootstrap.py found", path=str(bootstrap_py))
    else:
        report.error(
            "FWV019",
            f"bootstrap.py not found: {bootstrap_py}",
            action="Create with: forge dev verify init-plugin <plugin_id>",
            path=str(bootstrap_py),
        )

    if gen_stimulus_py.exists():
        report.note("FWV000", "gen_stimulus.py found", path=str(gen_stimulus_py))
    else:
        report.warn(
            "FWV020",
            f"gen_stimulus.py not found",
            action=(
                "Required for xsim flows.  "
                f"Create at {gen_stimulus_py} (see forge dev verify init-plugin output)"
            ),
            path=str(gen_stimulus_py),
        )

    # ── 4. RTL introspection mode ──────────────────────────────────────────
    from forge.verification.rtl_introspection import _pyverilog_available, introspection_mode  # noqa: PLC0415
    _parser_ok   = _pyverilog_available()
    _active_mode = introspection_mode()
    if _parser_ok:
        report.note(
            "FWV000",
            f"pyverilog (parser extra): installed  [mode=parser]",
        )
    else:
        report.warn(
            "FWV006",
            "pyverilog (parser extra): NOT installed  [mode=regex fallback]",
            action=(
                "Regex fallback is heuristic and may miss ports on non-standard RTL.  "
                "Install with: pip install -e \"forge/[parser]\""
            ),
        )

    # ── 5. Simulator tool availability ─────────────────────────────────────
    # Reuses forge.core.toolchain_versions.tool_present — the same shared
    # presence check `forge doctor` (core/cli/groups/doctor.py) uses, so
    # both doctor commands agree on tool availability for the same
    # environment.
    for tool in ("xvlog", "xelab", "xsim"):
        if tool_present(tool):
            report.note("FWV000", f"Tool on PATH: {tool}")
        else:
            report.warn(
                "FWV012",
                f"XSIM tool not on PATH: {tool!r}",
                action=f"Source your Vivado setup script.  Required for xsim flows.",
            )

    # ── 6. Supported matrix + per-flow artifact checks ─────────────────────
    if contract is not None:
        from forge.verification.stimulus_contract import validate_stimulus
        from forge.verification.layout import resolve_generated_files
        from forge.verification.supported_matrix import validate_flow_matrix
        _CSIM_KINDS = {"hls_csim", "hls_cosim"}

        for flow_decl in contract.flows:
            flow_dir = verify_root / flow_decl.name
            fn       = flow_decl.name   # short alias for flow name

            # ── 6a. Matrix check ─────────────────────────────────────────
            matrix_errs = validate_flow_matrix(flow_decl.kind, flow_decl.backend)
            if matrix_errs:
                code = "FWV002" if getattr(flow_decl, "experimental", False) else "FWV001"
                sev  = Severity.WARNING if getattr(flow_decl, "experimental", False) else Severity.ERROR
                for e in matrix_errs:
                    if sev == Severity.WARNING:
                        report.warn(code, e, flow=fn,
                            action="Set experimental: true in design.verification.yml to acknowledge")
                    else:
                        report.error(code, e, flow=fn,
                            action="Use a supported (kind, backend) pair or add experimental: true")
            else:
                report.note(
                    "FWV000",
                    f"(kind={flow_decl.kind}, backend={flow_decl.backend}) — supported",
                    flow=fn,
                )

            # ── 6b. verify.flow.yml ──────────────────────────────────────
            flow_yml = flow_dir / "verify.flow.yml"
            if flow_yml.exists():
                report.note("FWV000", "verify.flow.yml: present", flow=fn, path=str(flow_yml))
            else:
                report.warn(
                    "FWV004",
                    "verify.flow.yml: MISSING",
                    action=f"forge dev verify generate {design_path} --flow {fn}",
                    flow=fn,
                    path=str(flow_yml),
                )

            # ── 6c. csim-specific: binary check ─────────────────────────
            if flow_decl.kind in _CSIM_KINDS:
                from forge.verification.backend_csim import find_tb_binary
                tb_bin = find_tb_binary(flow_decl.tb_module, consumer_root)
                if tb_bin:
                    report.note("FWV000", f"csim binary: {tb_bin}", flow=fn)
                else:
                    report.warn(
                        "FWV018",
                        f"csim binary not found: {flow_decl.tb_module!r}",
                        action=(
                            "Build the HLS testbench, or set env var "
                            f"CSIM_TB_{flow_decl.tb_module.upper()}"
                        ),
                        flow=fn,
                    )
                continue

            # ── 6d. xsim-type flows: TB, port_map, stimulus ──────────────
            gen_files = resolve_generated_files(
                verify_root, flow_decl.name, flow_decl.tb_module, is_csim=False
            )

            # TB
            if gen_files.tb_sv and gen_files.tb_sv.exists():
                report.note("FWV000", f"TB: {gen_files.tb_sv.name}", flow=fn)
            else:
                report.error(
                    "FWV004",
                    f"TB not found: {gen_files.tb_sv}",
                    action=f"forge dev verify generate {design_path} --flow {fn}",
                    flow=fn,
                    path=str(gen_files.tb_sv) if gen_files.tb_sv else "",
                )

            # port_map
            if gen_files.port_map.exists():
                try:
                    import yaml as _yaml
                    from forge.verification.gen_sim import _flatten_ports  # noqa: PLC0415
                    _pm_raw   = _yaml.safe_load(gen_files.port_map.read_text()) or {}
                    _nports   = len(_flatten_ports(_pm_raw))
                except Exception:
                    _nports = -1

                if _nports == 0:
                    report.error(
                        "FWV009",
                        "port_map.yaml: ZERO ports found — TB will have no DUT signals",
                        action=(
                            f"RTL style not recognised by {_active_mode} mode.  "
                            "Install [parser] extra, or write port_map.yaml manually."
                        ),
                        flow=fn,
                        path=str(gen_files.port_map),
                    )
                elif _nports < 0:
                    report.warn(
                        "FWV004",
                        "port_map.yaml: present but unreadable",
                        flow=fn,
                        path=str(gen_files.port_map),
                    )
                else:
                    report.note(
                        "FWV000",
                        f"port_map.yaml: {_nports} port(s)  [mode={_active_mode}]",
                        flow=fn,
                    )
            else:
                report.warn(
                    "FWV004",
                    "port_map.yaml: MISSING",
                    action=f"forge dev verify generate {design_path} --flow {fn}",
                    flow=fn,
                    path=str(gen_files.port_map),
                )

            # stimulus
            if not gen_files.stimulus_svh.exists():
                report.warn(
                    "FWV017",
                    "stimulus_current.svh: MISSING",
                    action=f"python3 {gen_stimulus_py} --flow {fn}",
                    flow=fn,
                    path=str(gen_files.stimulus_svh),
                )
            else:
                sc = validate_stimulus(gen_files.stimulus_svh)
                if sc.ok:
                    report.note("FWV000", "stimulus_current.svh: contract OK", flow=fn)
                else:
                    for e in sc.errors:
                        report.error(
                            "FWV010", f"stimulus: {e}",
                            action="Fix the task signature/contract in stimulus_current.svh",
                            flow=fn,
                        )
                for w in sc.warnings:
                    report.warn("FWV010", f"stimulus: {w}", flow=fn)

    # ── 7. Layout ──────────────────────────────────────────────────────────
    from forge.verification.layout import validate_layout
    for le in validate_layout(verify_root):
        report.error(
            "FWV003",
            f"Layout violation: {le}",
            action=(
                "Migrate to the canonical flat <verify_root>/<flow_name>/ layout. "
                "See framework docs §3."
            ),
            path=str(verify_root),
        )

    # ── 8. Supported-path gate ─────────────────────────────────────────────
    from forge.verification.supported_path_validator import validate_supported_path
    spv = validate_supported_path(
        design_path, consumer_root,
        check_tools=False,      # we already checked tools above
        check_artifacts=True,
        check_stimulus=True,
    )
    if spv.ok:
        report.note(
            "FWV000",
            f"Supported-path gate: PASS — plugin is on the canonical supported path",
        )
    else:
        report.warn(
            "FWV001",
            "Supported-path gate: deviations detected (see individual checks above)",
            action="Resolve the ERROR items above to reach the canonical supported path",
        )

    # ── 9. Stale artifact detection ────────────────────────────────────────
    if contract is not None:
        try:
            from forge.verification.stale_artifact import check_flow_staleness
            for flow_decl in contract.flows:
                stale_report = check_flow_staleness(
                    verify_root, flow_decl, contract, consumer_root
                )
                for s in stale_report.stale_artifacts:
                    report.warn(
                        "FWV016",
                        s.message(),
                        action=(
                            f"Re-run: forge dev verify generate {design_path} "
                            f"--flow {flow_decl.name}"
                        ),
                        flow=flow_decl.name,
                        path=str(s.artifact_path),
                    )
        except Exception:  # noqa: BLE001
            # Stale detection is advisory — never block on its failure
            pass

    return report
