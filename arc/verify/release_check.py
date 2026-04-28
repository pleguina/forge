"""fw_verify.release_check — Single-call release readiness gate (F2).

This module aggregates every programmatic check from the framework into one
function — ``run_release_check()`` — and exposes it as the
``fw_verify release-check <design.yml>`` CLI command.

It is the canonical "all green before merge / tagging" gate.

Criteria covered
----------------
RC-01  design.verification.yml parses without error
RC-02  Supported (kind, backend) matrix for every declared flow
RC-03  Canonical layout: no legacy hand-authored files detected
RC-04  Framework bootstrap.py + gen_stimulus.py present
RC-05  Per-flow verify.flow.yml present for every declared flow
RC-06  Per-flow TB (tb_<module>.sv) present for every xsim flow
RC-07  Per-flow port_map.yaml has > 0 ports for every declared flow
RC-08  Per-flow stimulus_current.svh passes stimulus contract
RC-09  Supported-path gate passes (validate_supported_path) — full check
RC-10  No stale generated artifacts detected

Each criterion maps to an ``FWVxxxx`` diagnostic code so that CI tooling can
filter by criterion.

Usage
-----
Programmatic::

    from arc.verify.release_check import run_release_check

    result = run_release_check(
        design_path="plugins/my_plugin/verify/design.verification.yml",
        consumer_root=".",
    )
    if not result.ok:
        result.print_summary()
        sys.exit(1)

CLI::

    fw_verify release-check plugins/my_plugin/verify/design.verification.yml
    fw_verify release-check design.verification.yml --json
    fw_verify release-check design.verification.yml --strict

Exit codes
----------
0   All criteria pass (or no errors, no warnings in strict mode).
1   One or more criteria failed.
2   Unexpected internal error (see --debug).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from arc.verify.diagnostics import DiagnosticReport


# ── ReleaseCheckResult ────────────────────────────────────────────────────


@dataclass
class ReleaseCheckResult:
    """Return value from :func:`run_release_check`.

    Attributes:
        report:        The :class:`~fw_verify.diagnostics.DiagnosticReport`
                       containing all per-criterion diagnostics.
        design_path:   Resolved path of the design.verification.yml checked.
        consumer_root: Resolved consumer root used for path resolution.
    """
    report:        "DiagnosticReport"
    design_path:   Path
    consumer_root: Path

    @property
    def ok(self) -> bool:
        """True when no ERROR or CRITICAL diagnostics are present."""
        return self.report.ok

    @property
    def ok_strict(self) -> bool:
        """True in strict mode: no WARNING, ERROR, or CRITICAL diagnostics."""
        return self.report.ok_strict

    def print_summary(self, *, strict: bool = False) -> None:
        """Print a concise human-readable summary to stdout."""
        self.report.print_console()
        print()
        print(self.report.summary_line(strict=strict))


# ── Core gate function ────────────────────────────────────────────────────


def run_release_check(
    design_path: "str | Path",
    consumer_root: "str | Path | None" = None,
    *,
    check_tools: bool = False,
    check_stale: bool = True,
) -> ReleaseCheckResult:
    """Execute all release-readiness criteria and return a structured report.

    This is a **read-only** operation — it never writes any files.

    Args:
        design_path:    Path to ``design.verification.yml``.
        consumer_root:  Repository root used to resolve relative paths.
                        Falls back to ``VERIFY_CONSUMER_ROOT`` env var, then
                        the directory containing *design_path*.
        check_tools:    When *True*, also verify that simulator binaries
                        (xvlog, xelab, xsim) are on ``PATH``.  Defaults to
                        *False* so the gate can run in tool-less CI containers.
        check_stale:    When *True* (default), include RC-10 stale-artifact
                        check.

    Returns:
        :class:`ReleaseCheckResult`
    """
    import os
    from arc.verify.diagnostics import DiagnosticReport, Severity

    design_path = Path(design_path).resolve()

    if consumer_root is None:
        env_root = os.environ.get("VERIFY_CONSUMER_ROOT")
        consumer_root = Path(env_root).resolve() if env_root else design_path.parent
    else:
        consumer_root = Path(consumer_root).resolve()

    report = DiagnosticReport(label=f"release-check:{design_path.name}")

    verify_root = design_path.parent

    # ── RC-01: design.verification.yml parseable ──────────────────────────
    contract = None
    if not design_path.exists():
        report.error(
            "FWV015",
            f"[RC-01] design.verification.yml not found: {design_path}",
            action="Create the file or correct the path",
            path=str(design_path),
        )
    else:
        from arc.verify.design_contract import load_verify_design
        try:
            contract = load_verify_design(design_path)
            report.note(
                "FWV000",
                f"[RC-01] design.verification.yml: OK  ({len(contract.flows)} flows)",
                path=str(design_path),
            )
        except Exception as exc:  # noqa: BLE001
            report.error(
                "FWV015",
                f"[RC-01] design.verification.yml parse error: {exc}",
                action="Fix YAML syntax / schema in design.verification.yml",
                path=str(design_path),
            )

    # ── RC-04: framework bootstrap + gen_stimulus ─────────────────────────
    bootstrap_py    = verify_root / "tools" / "bootstrap.py"
    gen_stimulus_py = verify_root / "tools" / "gen_stimulus.py"

    if bootstrap_py.exists():
        report.note("FWV000", "[RC-04] bootstrap.py: present", path=str(bootstrap_py))
    else:
        report.error(
            "FWV019",
            f"[RC-04] bootstrap.py not found: {bootstrap_py}",
            action="Create with: fw_verify init-plugin <plugin_id>",
            path=str(bootstrap_py),
        )

    if gen_stimulus_py.exists():
        report.note("FWV000", "[RC-04] gen_stimulus.py: present", path=str(gen_stimulus_py))
    else:
        report.warn(
            "FWV020",
            "[RC-04] gen_stimulus.py not found",
            action=f"Required for xsim flows.  Create at {gen_stimulus_py}",
            path=str(gen_stimulus_py),
        )

    # ── RC-03: canonical layout ───────────────────────────────────────────
    from arc.verify.layout import validate_layout
    layout_errors = validate_layout(verify_root)
    if layout_errors:
        for le in layout_errors:
            report.error(
                "FWV003",
                f"[RC-03] Layout violation: {le}",
                action=(
                    "Migrate to the canonical flat <verify_root>/<flow_name>/ layout.  "
                    "See framework docs §3."
                ),
                path=str(verify_root),
            )
    else:
        report.note("FWV000", "[RC-03] Canonical layout: OK", path=str(verify_root))

    # ── Per-flow checks (RC-02, RC-05, RC-06, RC-07, RC-08) ───────────────
    if contract is not None:
        _CSIM_KINDS = {"hls_csim", "hls_cosim"}
        from arc.verify.supported_matrix import validate_flow_matrix
        from arc.verify.layout import resolve_generated_files
        from arc.verify.stimulus_contract import validate_stimulus

        for flow_decl in contract.flows:
            fn = flow_decl.name

            # RC-02 — Supported matrix
            matrix_errs = validate_flow_matrix(flow_decl.kind, flow_decl.backend)
            if matrix_errs:
                is_exp = getattr(flow_decl, "experimental", False)
                code   = "FWV002" if is_exp else "FWV001"
                for e in matrix_errs:
                    if is_exp:
                        report.warn(
                            code,
                            f"[RC-02] [{fn}] experimental flow: {e}",
                            action="Acknowledged via experimental: true",
                            flow=fn,
                        )
                    else:
                        report.error(
                            code,
                            f"[RC-02] [{fn}] unsupported matrix: {e}",
                            action="Use a supported (kind, backend) pair "
                                   "or add experimental: true",
                            flow=fn,
                        )
            else:
                report.note(
                    "FWV000",
                    f"[RC-02] [{fn}] (kind={flow_decl.kind}, "
                    f"backend={flow_decl.backend}): supported",
                    flow=fn,
                )

            # RC-05 — verify.flow.yml
            flow_yml = verify_root / fn / "verify.flow.yml"
            if flow_yml.exists():
                report.note("FWV000", f"[RC-05] [{fn}] verify.flow.yml: present", flow=fn)
            else:
                report.warn(
                    "FWV004",
                    f"[RC-05] [{fn}] verify.flow.yml: MISSING",
                    action=f"fw_verify generate {design_path} --flow {fn}",
                    flow=fn,
                    path=str(flow_yml),
                )

            if flow_decl.kind in _CSIM_KINDS:
                # csim: check binary instead
                from arc.verify.backend_csim import find_tb_binary
                tb_bin = find_tb_binary(flow_decl.tb_module, consumer_root)
                if tb_bin:
                    report.note("FWV000", f"[RC-06] [{fn}] csim binary: {tb_bin}", flow=fn)
                else:
                    report.warn(
                        "FWV018",
                        f"[RC-06] [{fn}] csim binary not found: {flow_decl.tb_module!r}",
                        action=(
                            "Build the HLS testbench, or set env var "
                            f"CSIM_TB_{flow_decl.tb_module.upper()}"
                        ),
                        flow=fn,
                    )
                continue

            # xsim flow: TB, port_map, stimulus
            gen = resolve_generated_files(
                verify_root, fn, flow_decl.tb_module, is_csim=False
            )

            # RC-06 — TB
            if gen.tb_sv and gen.tb_sv.exists():
                report.note("FWV000", f"[RC-06] [{fn}] TB: {gen.tb_sv.name}", flow=fn)
            else:
                report.error(
                    "FWV004",
                    f"[RC-06] [{fn}] TB not found: {gen.tb_sv}",
                    action=f"fw_verify generate {design_path} --flow {fn}",
                    flow=fn,
                    path=str(gen.tb_sv) if gen.tb_sv else "",
                )

            # RC-07 — port_map
            if gen.port_map.exists():
                try:
                    import yaml as _yaml
                    _pm_raw = _yaml.safe_load(gen.port_map.read_text()) or {}
                    _nports = len(_pm_raw.get("ports", []) or [])
                except Exception:
                    _nports = -1

                if _nports == 0:
                    report.error(
                        "FWV009",
                        f"[RC-07] [{fn}] port_map.yaml: ZERO ports — TB will have no DUT signals",
                        action=(
                            "RTL style not recognised by the active introspection mode.  "
                            "Install [parser] extra or write port_map.yaml manually."
                        ),
                        flow=fn,
                        path=str(gen.port_map),
                    )
                elif _nports < 0:
                    report.warn(
                        "FWV004",
                        f"[RC-07] [{fn}] port_map.yaml: present but unreadable",
                        flow=fn,
                        path=str(gen.port_map),
                    )
                else:
                    report.note(
                        "FWV000",
                        f"[RC-07] [{fn}] port_map.yaml: {_nports} port(s)",
                        flow=fn,
                    )
            else:
                report.warn(
                    "FWV004",
                    f"[RC-07] [{fn}] port_map.yaml: MISSING",
                    action=f"fw_verify generate {design_path} --flow {fn}",
                    flow=fn,
                    path=str(gen.port_map),
                )

            # RC-08 — stimulus contract
            if not gen.stimulus_svh.exists():
                report.warn(
                    "FWV017",
                    f"[RC-08] [{fn}] stimulus_current.svh: MISSING",
                    action=f"python3 {gen_stimulus_py} --flow {fn}",
                    flow=fn,
                    path=str(gen.stimulus_svh),
                )
            else:
                sc = validate_stimulus(gen.stimulus_svh)
                if sc.ok:
                    report.note(
                        "FWV000",
                        f"[RC-08] [{fn}] stimulus_current.svh: contract OK",
                        flow=fn,
                    )
                else:
                    for e in sc.errors:
                        report.error(
                            "FWV010",
                            f"[RC-08] [{fn}] stimulus: {e}",
                            action="Fix the task signature/contract in stimulus_current.svh",
                            flow=fn,
                        )
                for w in sc.warnings:
                    report.warn("FWV010", f"[RC-08] [{fn}] stimulus: {w}", flow=fn)

    # ── RC-09: Supported-path gate ────────────────────────────────────────
    from arc.verify.supported_path_validator import validate_supported_path
    spv = validate_supported_path(
        design_path, consumer_root,
        check_tools=check_tools,
        check_artifacts=True,
        check_stimulus=True,
    )
    if spv.ok:
        report.note(
            "FWV000",
            "[RC-09] Supported-path gate: PASS",
        )
    else:
        report.warn(
            "FWV001",
            "[RC-09] Supported-path gate: deviations detected "
            "(see per-flow checks above for details)",
            action="Resolve every ERROR finding above to reach the canonical supported path",
        )

    # ── RC-10: Stale artifacts ────────────────────────────────────────────
    if check_stale and contract is not None:
        try:
            from arc.verify.stale_artifact import check_flow_staleness
            stale_count = 0
            for flow_decl in contract.flows:
                stale_report = check_flow_staleness(
                    verify_root, flow_decl, contract, consumer_root
                )
                for s in stale_report.stale_artifacts:
                    stale_count += 1
                    report.warn(
                        "FWV016",
                        f"[RC-10] [{flow_decl.name}] {s.message()}",
                        action=(
                            f"Re-run: fw_verify generate {design_path} "
                            f"--flow {flow_decl.name}"
                        ),
                        flow=flow_decl.name,
                        path=str(s.artifact_path),
                    )
            if stale_count == 0:
                report.note(
                    "FWV000",
                    "[RC-10] Stale-artifact check: no stale artifacts detected",
                )
        except Exception:  # noqa: BLE001
            pass  # stale detection is advisory

    # ── Optional: tool availability ───────────────────────────────────────
    if check_tools:
        import shutil as _shutil
        for tool in ("xvlog", "xelab", "xsim"):
            if _shutil.which(tool):
                report.note("FWV000", f"[tools] {tool}: on PATH")
            else:
                report.warn(
                    "FWV012",
                    f"[tools] {tool}: NOT on PATH",
                    action="Source your Vivado setup script.  Required for xsim flows.",
                )

    return ReleaseCheckResult(
        report=report,
        design_path=design_path,
        consumer_root=consumer_root,
    )
