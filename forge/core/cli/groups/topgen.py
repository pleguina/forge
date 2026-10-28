"""forge topgen — hardware topology generation commands."""

from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from forge.contracts.config import DesignConfig
from forge.contracts.unpacker import unpack_ip_archives
from forge.contracts.parser import collect_all, write_summary
from forge.contracts.matcher import load_ip_info, auto_match_ports
from forge.contracts.contract_loader import (
    load_contracts_for_design,
    synthesize_ip_info,
)
from forge.generation.generators.sv_testbench_generator import generate_sv_testbench
from forge.generation.pipeline import (
    GenerationRefused,
    GenTopOptions,
    compute_gen_top_plan,
    gate_failure,
    generate_top,
    planned_output_artifacts,
)
from forge.generation.validation import validate_design, validate_registry
from forge.core.diagnostics import ATGDiagnosticReport
from forge.core.stale_detection import (
    check_top_gen_staleness,
    check_ip_info_staleness,
    format_stale_report,
)
from forge.core.cli._shared import (
    print_cli_error,
    consumer_root as _consumer_root,
    resolve_path as _resolve_path,
    remove_path as _remove_path,
    load_verify_flow_entries as _load_verify_flow_entries,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _validator_payload(validator) -> dict:
    """Serialize a DesignValidator/RegistryValidator (topgen/validation.py)
    into a JSON-ready dict. Their errors/warnings/infos are plain
    ValidationError dataclasses, so this is a direct asdict() mapping."""
    return {
        "passed": not validator.has_errors(),
        "errors": [dataclasses.asdict(e) for e in validator.errors],
        "warnings": [dataclasses.asdict(e) for e in validator.warnings],
        "infos": [dataclasses.asdict(e) for e in validator.infos],
    }


def _validation_error_to_diagnostic(err, *, category_prefix: str) -> dict:
    """Reshape one `ValidationError` (topgen/validation.py) into the
    shared envelope's diagnostic-dict shape
    — `code=None` (ValidationError has no stable code table, honest
    absence), `action` from `suggestion`, `object_id` from `location`."""
    d: dict = {
        "severity": err.severity, "message": err.message, "code": None,
        "category": f"{category_prefix}:{err.category}",
    }
    if err.location:
        d["object_id"] = err.location
    if err.suggestion:
        d["action"] = err.suggestion
    return d


def _validators_to_envelope(*validators_with_prefix, metrics: Optional[dict] = None):
    """Build a `CommandEnvelope` from one or more (validator, category_prefix)
    pairs — shared by `cmd_validate` (design + optional registry) and
    `cmd_validate_registry` (registry alone). `status` is the honest 3-way
    pass/warn/fail derived from real severities; `--strict`'s warn->fail
    exit-code promotion is applied by the caller via
    `CommandEnvelope.exit_code(strict=...)`/`emit(..., strict=...)` — same
    "status string never changes, only the exit code does" contract every
    other envelope-adopting command follows."""
    from forge.core.cli.envelope import CommandEnvelope

    diagnostics: List[dict] = []
    for validator, prefix in validators_with_prefix:
        for err in validator.errors:
            diagnostics.append(_validation_error_to_diagnostic(err, category_prefix=prefix))
        for err in validator.warnings:
            diagnostics.append(_validation_error_to_diagnostic(err, category_prefix=prefix))
        for err in validator.infos:
            diagnostics.append(_validation_error_to_diagnostic(err, category_prefix=prefix))

    has_error = any(d["severity"] == "error" for d in diagnostics)
    has_warning = any(d["severity"] == "warning" for d in diagnostics)
    status = "fail" if has_error else ("warn" if has_warning else "pass")

    next_actions: List[str] = []
    seen = set()
    for d in diagnostics:
        if d["severity"] not in ("warning", "error") or "action" not in d:
            continue
        if d["action"] not in seen:
            seen.add(d["action"])
            next_actions.append(d["action"])

    return CommandEnvelope(
        status=status, diagnostics=diagnostics, metrics=dict(metrics or {}),
        next_actions=next_actions,
    )


def _discover_verify_design(design_path: Path, args, consumer_root: Path | None) -> Path | None:
    """The verification contract to resolve the IR's plan against.

    ``--verify-design`` wins; otherwise the conventional sibling location
    (``<plugin>/forge/verify/design.verification.yml``) is used when it
    exists — the same discovery ``forge dev topgen clean`` already performs, so
    a project doesn't have to name the file twice. ``--no-verify`` skips it.
    """
    if getattr(args, "no_verify", False):
        return None
    declared = getattr(args, "verify_design", None)
    if declared:
        return _resolve_path(declared, consumer_root)
    candidate = design_path.parent.parent / "verify" / "design.verification.yml"
    return candidate.resolve() if candidate.exists() else None


def _attach_verification_plan(
    project, design_path: Path, args, consumer_root: Path | None, dut_dir: Path,
) -> None:
    """Resolve this design's verification plan onto the IR it just
    generated from — Phase G3.

    Attached here, after generation, for the same reason ``top_ports`` and
    the tie-off connections are: the plan's stimulus and observation points
    *are* the generated top level's ports, which only exist once the
    generator has run. ``forge inspect``'s pre-generation IR therefore
    carries no plan, exactly as it carries no top-level ports.

    A contract that fails to load is reported and skipped rather than
    failing the build: verification planning is a description of what was
    generated, and a broken verification contract must not stop the RTL
    from being written. Whoever runs `forge dev verify` gets the same load
    error from the loader that owns it.
    """
    from forge.ir.verification_plan import build_verification_plan

    verify_design = _discover_verify_design(design_path, args, consumer_root)
    contract = None
    if verify_design and verify_design.exists():
        try:
            from forge.verification.design_contract import load_verify_design

            contract = load_verify_design(verify_design)
        except Exception as e:  # noqa: BLE001 - never fail generation for this
            print(f"  ⚠️  verification plan not resolved: {e}")

    plan = build_verification_plan(
        project, contract, dut_dir=dut_dir, consumer_root=consumer_root,
    )
    project.design.verification_plan = plan
    if plan.populated:
        mine = [fl for fl in plan.flows if fl.targets_this_design]
        print(
            f"  ✓ Verification plan: {len(mine)} of {len(plan.flows)} declared "
            f"flow(s) target this design, {len(plan.stimulus)} stimulus / "
            f"{len(plan.observation)} observation port(s)"
        )
        for fl in plan.flows:
            if fl.unresolved_reason:
                print(f"    ⚠️  flow '{fl.name}': {fl.unresolved_reason}")


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

def cmd_clean(args):
    """Remove generated artifacts for one design without touching IP/HLS trees."""
    try:
        design_path = Path(args.design).expanduser().resolve()
        if not design_path.exists():
            print(f"❌ Design file not found: {design_path}", file=sys.stderr)
            print("   → Check the design.yml path and re-run forge dev topgen clean.", file=sys.stderr)
            sys.exit(1)

        c_root = _consumer_root(design_path, getattr(args, "consumer_root", None))
        output = _resolve_path(args.output, c_root, Path("algo_top.v"))
        output_dir = output.parent
        ip_info_file = (
            _resolve_path(args.ip_info, c_root) if args.ip_info else output_dir / "ip_info.yaml"
        )

        generated_paths = [
            output,
            output_dir / "build_manifest.json",
            output_dir / "design_parameters.json",
            output_dir / "port_map.yaml",
            output_dir / "probe_map.yaml",
            output_dir / "tb_bindings.svh",
            output_dir / "port_signature.json",
            output_dir / "maturity_report.json",
            output_dir / "tb_algo_top.sv",
            output_dir / "stimulus_current.svh",
            ip_info_file,
        ]

        verify_design = None
        if getattr(args, "verify_design", None):
            verify_design = _resolve_path(args.verify_design, c_root)
        elif not getattr(args, "no_verify", False):
            candidate = design_path.parent.parent / "verify" / "design.verification.yml"
            if candidate.exists():
                verify_design = candidate.resolve()

        planned: list[Path] = [p for p in generated_paths if p.exists()]

        if verify_design and verify_design.exists():
            verify_root = verify_design.parent
            for flow_name, flow_kind in _load_verify_flow_entries(verify_design):
                flow_dirs = [verify_root / flow_name]
                if flow_kind:
                    flow_dirs.append(verify_root / flow_kind / flow_name)
                for flow_dir in flow_dirs:
                    planned.extend(
                        path
                        for path in [
                            flow_dir / "verify.flow.yml",
                            flow_dir / "wave.tcl",
                            flow_dir / "port_map.yaml",
                            flow_dir / "stimulus_current.svh",
                            flow_dir / "xsim_work",
                            flow_dir / "stimulus",
                        ]
                        if path.exists()
                    )
                    planned.extend(
                        path for path in sorted(flow_dir.glob("tb_*.sv")) if path.exists()
                    )

        seen: set[Path] = set()
        unique_planned: list[Path] = []
        for path in planned:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            unique_planned.append(resolved)

        if not unique_planned:
            print("No generated artifacts found to clean.")
            return

        print(f"🧹 Cleaning generated artifacts for design: {design_path}")
        for path in unique_planned:
            rel = (
                path.relative_to(c_root) if path.is_relative_to(c_root) else path
            )
            print(f"  - {rel}")

        if getattr(args, "dry_run", False):
            print("Dry run only; no files were removed.")
            return

        removed: list[Path] = []
        for path in unique_planned:
            _remove_path(path, removed)

        print(f"✓ Removed {len(removed)} generated artifact(s)")
    except Exception as e:
        print_cli_error(
            "Clean failed",
            e,
            hint="Check the design path, output path, and verify-design path, then retry forge clean.",
        )
        sys.exit(1)


def cmd_unpack_ips(args):
    """Unpack IP archives."""
    try:
        src_dir = Path(args.src).expanduser().resolve()
        c_root = _consumer_root(src_dir, getattr(args, "consumer_root", None))
        ip_root = _resolve_path(args.ip_root, c_root, Path("ips"))
        extracted = unpack_ip_archives(
            src_dir=args.src,
            ip_root=ip_root,
            force=args.force,
            verbose=True,
        )
        print(f"\n✓ Successfully extracted {len(extracted)} IP packages")
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


def cmd_ip_summary(args):
    """Generate IP summary (ip_info.yaml)."""
    try:
        design_path = Path(args.design).expanduser().resolve()
        if not design_path.exists():
            print(f"❌ Design file not found: {design_path}")
            sys.exit(1)

        cfg = DesignConfig.load_relaxed(design_path)
        c_root = _consumer_root(design_path, getattr(args, "consumer_root", None))
        build_root = _resolve_path(args.build_dir, c_root, Path("build"))
        ip_root = _resolve_path(args.ip_root, c_root, Path("ips"))
        src_root = (
            _resolve_path(args.src_root, c_root)
            if hasattr(args, "src_root") and args.src_root
            else design_path.parent
        )

        print(f"📋 Collecting IP metadata from {ip_root}...")
        summary = collect_all(
            build_root=build_root,
            modules=cfg.modules,
            ip_root=ip_root,
            src_root=src_root,
        )

        if not any(v is not None for v in summary.values()):
            print("❌ No IP metadata found. Run unpack-ips first.")
            sys.exit(1)

        output = _resolve_path(args.output, c_root, Path("ip_info.yaml"))
        write_summary(summary, output, format="yaml")
        print(f"✓ IP summary written to {output}")

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


def cmd_match_ports(args):
    """Show port matching report."""
    try:
        design_path = Path(args.design).expanduser().resolve()
        cfg = DesignConfig.load_relaxed(design_path)

        c_root = _consumer_root(design_path, getattr(args, "consumer_root", None))
        ip_info_file = _resolve_path(args.ip_info, c_root, Path("ip_info.yaml"))
        if not ip_info_file.exists():
            print(f"❌ IP info file not found: {ip_info_file}")
            print("   Run 'forge ip-summary' first")
            sys.exit(1)

        print("🔗 Matching ports...")
        ip_info = load_ip_info(ip_info_file)
        conn_map, global_nets, match_report = auto_match_ports(
            cfg, ip_info, system_yml=args.system
        )

        print(f"\n✓ Port matching complete")
        print(f"\nConnections: {len(conn_map)}")
        for key, val in conn_map.items():
            print(f"  {key} → {val}")

        if global_nets:
            print(f"\nGlobal nets: {len(global_nets)}")
            for net, binds in global_nets.items():
                fanout = ", ".join(f"{inst}.{port}" for inst, port in binds)
                print(f"  {net}: {fanout}")

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


def cmd_lint(args):
    """Lint VHDL file(s)."""
    try:
        from forge.core.utils.vhdl_linter import lint_vhdl_files

        files = args.files if isinstance(args.files, list) else [args.files]
        vhdl_files = [Path(f) for f in files]

        for vfile in vhdl_files:
            if not vfile.exists():
                print(f"❌ File not found: {vfile}")
                sys.exit(1)

        passed, failed = lint_vhdl_files(
            vhdl_files, std=args.std, ieee=args.ieee, fix=args.fix
        )

        if failed > 0:
            sys.exit(1)

    except ImportError as e:
        print(f"❌ VHDL linter import error: {e}")
        sys.exit(1)
    except Exception as e:
        print_cli_error(
            "VHDL lint failed",
            e,
            hint="Check that the requested files exist and that the linter toolchain is installed correctly.",
        )
        sys.exit(1)


def cmd_lint_verilog(args):
    """Lint Verilog file(s)."""
    try:
        from forge.core.utils.verilog_linter import lint_verilog_files

        files = args.files if isinstance(args.files, list) else [args.files]
        verilog_files = [Path(f) for f in files]

        for vfile in verilog_files:
            if not vfile.exists():
                print(f"❌ File not found: {vfile}")
                sys.exit(1)

        passed, failed = lint_verilog_files(
            verilog_files,
            top_module=args.top_module,
            show_warnings=not args.no_warnings,
            strict=args.strict,
        )

        if failed > 0:
            sys.exit(1)

    except Exception as e:
        print_cli_error(
            "Verilog lint failed",
            e,
            hint="Check that the requested files exist and that the Verilog lint toolchain is available.",
        )
        sys.exit(1)


def cmd_validate(args):
    """Validate design.yml without generating output."""
    from forge.core.cli.envelope import CommandEnvelope, emit, status_for_exception

    json_mode = getattr(args, "json", False)
    strict = getattr(args, "strict", False)

    def _fail_early(msg: str) -> None:
        envelope = CommandEnvelope(status="fail", diagnostics=[{"severity": "error", "message": msg}])
        if not json_mode:
            print(f"❌ {msg}")
            sys.exit(envelope.exit_code())
        sys.exit(emit(envelope, json_mode=True))

    try:
        design_path = Path(args.design).expanduser().resolve()

        if not design_path.exists():
            _fail_early(f"Design file not found: {design_path}")

        from forge.contracts.design_variant import load_design_document

        _raw_design = load_design_document(design_path)
        _registry_ref = _raw_design.get("registry")
        validators: List[Any] = []

        if _registry_ref:
            registry_path = (design_path.parent / _registry_ref).resolve()
            if not registry_path.exists():
                _fail_early(f"Registry file not found: {registry_path}")

            if not json_mode:
                print(f"📖 Loading registry: {registry_path}")
                print("🔍 Validating registry...\n")
            reg_validator = validate_registry(registry_path)
            if not json_mode:
                reg_validator.print_report()
            validators.append((reg_validator, "registry"))

            if reg_validator.has_errors() or (strict and reg_validator.warnings):
                if not json_mode:
                    if reg_validator.has_errors():
                        print(
                            f"\n⛔ Registry has {len(reg_validator.errors)} error(s). "
                            "Fix them before validating design."
                        )
                    else:
                        print(
                            f"\n⛔ Strict mode: treating {len(reg_validator.warnings)} "
                            "registry warning(s) as errors"
                        )
                envelope = _validators_to_envelope(*validators)
                sys.exit(emit(envelope, json_mode=json_mode, strict=strict))
            if not json_mode:
                print()

        if not json_mode:
            print(f"📖 Loading design: {design_path}")
        cfg = DesignConfig.load_relaxed(design_path)

        if not json_mode:
            print("🔍 Validating design configuration...\n")
        validator = validate_design(cfg, design_path)
        if not json_mode:
            validator.print_report()
        validators.append((validator, "design"))

        if validator.has_errors() or (strict and validator.warnings):
            if not json_mode and not validator.has_errors():
                print(f"\n⛔ Strict mode: Treating {len(validator.warnings)} warnings as errors")
            envelope = _validators_to_envelope(*validators)
            sys.exit(emit(envelope, json_mode=json_mode, strict=strict))

        metrics: Dict[str, Any] = {}

        # ── CDC crossing check ────────────────────────────────────────
        # Run verify_cdc here too (it also gates gen-top --strict, see
        # cmd_gen_top), so authors get CDC feedback before the final
        # generation step, as a best-effort check whenever contracts can
        # be loaded without needing already-built HDL (the same
        # --contracts-from-style projection cmd_gen_top itself supports) —
        # skip gracefully (no diagnostic at all) if the registry/contracts
        # aren't available yet, matching every other optional check in
        # this command.
        cdc_diagnostics: List[dict] = []
        if _registry_ref:
            try:
                _cdc_contracts = load_contracts_for_design(registry_path, design_path.parent)
                _cdc_mapped = {}
                for m in cfg.modules:
                    c = _cdc_contracts.get(m.name) or (m.ip_info_key and _cdc_contracts.get(m.ip_info_key))
                    if c:
                        _cdc_mapped[m.name] = c
                if _cdc_mapped:
                    _cdc_ip_info = synthesize_ip_info(_cdc_mapped)
                    _conn_map, _global_nets, _match_report = auto_match_ports(
                        cfg, _cdc_ip_info, contracts=_cdc_contracts,
                    )
                    from forge.contracts.cdc import verify_cdc
                    _cdc_issues = verify_cdc(cfg, _cdc_contracts, _match_report, _conn_map, _global_nets)
                    cdc_diagnostics = [
                        {
                            "severity": "error", "code": issue.code or "ATG023",
                            "category": "cdc", "message": f"[{issue.connection}] {issue.message}",
                            "action": "Add a matching 'cdc:' block to the connection.",
                        }
                        for issue in _cdc_issues
                    ]

                    # forge.cdc_verification_result.v1
                    # — written from the exact same crossing data the
                    # warnings above were derived from, no recomputation.
                    _cdc_result_json = getattr(args, "cdc_result_json", None)
                    if _cdc_result_json:
                        import json as _json

                        from forge.core.utils.content_hash import hash_file
                        from forge.contracts.cdc import report_all_crossings
                        from forge.verification.cdc_verification_result import build_cdc_verification_result

                        _crossings = report_all_crossings(
                            cfg, _cdc_contracts, _match_report, _conn_map, _global_nets,
                        )
                        _cdc_result = build_cdc_verification_result(_crossings, hash_file(design_path))
                        _cdc_result_path = Path(_cdc_result_json).expanduser().resolve()
                        _cdc_result_path.parent.mkdir(parents=True, exist_ok=True)
                        _cdc_result_path.write_text(_json.dumps(_cdc_result.to_dict(), indent=2) + "\n")
                        if not json_mode:
                            print(f"  wrote {_cdc_result_path}")
            except Exception:
                cdc_diagnostics = []

        if cdc_diagnostics:
            if not json_mode:
                print("\n🔌 CDC crossing check:")
                for d in cdc_diagnostics:
                    print(f"  ❌ [{d['code']}] {d['message']}")
            envelope = _validators_to_envelope(*validators)
            envelope.diagnostics.extend(cdc_diagnostics)
            envelope.status = "fail"
            sys.exit(emit(envelope, json_mode=json_mode, strict=strict))

        stale_diagnostic: Optional[dict] = None
        if getattr(args, "check_stale", False):
            if not json_mode:
                print()
                print("🕒 Checking for stale generated artifacts…")
            output_dir = design_path.parent
            stale_report = check_top_gen_staleness(output_dir, design_yml=design_path)
            ip_info_candidates = (
                output_dir / "ip_info.yaml",
                output_dir.parent / "ip_info.yaml",
            )
            for _iic in ip_info_candidates:
                if _iic.exists():
                    ip_stale = check_ip_info_staleness(_iic, design_yml=design_path)
                    stale_report.stale.extend(ip_stale.stale)
                    stale_report.missing.extend(ip_stale.missing)
                    break

            stale_lines = format_stale_report(stale_report)
            metrics["stale"] = {"count": len(stale_report.stale), "artifacts": stale_lines}
            if stale_lines:
                if not json_mode:
                    for line in stale_lines:
                        print(f"  ⚠️  {line}")
                stale_diagnostic = {
                    "severity": "warning", "code": "ATG007", "category": "stale",
                    "message": f"{len(stale_report.stale)} stale artifact(s) detected",
                    "action": "Re-run forge gen-top to rebuild",
                    "path": str(output_dir),
                }
                if strict:
                    if not json_mode:
                        print("\n⛔ Strict mode: stale artifacts treated as errors")
                    envelope = _validators_to_envelope(*validators, metrics=metrics)
                    envelope.diagnostics.append(stale_diagnostic)
                    envelope.next_actions.append(stale_diagnostic["action"])
                    sys.exit(emit(envelope, json_mode=json_mode, strict=True))
            elif not json_mode:
                print("  ✅ All generated artifacts are up to date")

        if not json_mode:
            print("\n✅ Design is valid and ready for generation!")
        envelope = _validators_to_envelope(*validators, metrics=metrics)
        if stale_diagnostic is not None:
            envelope.diagnostics.append(stale_diagnostic)
            envelope.next_actions.append(stale_diagnostic["action"])
            if envelope.status == "pass":
                envelope.status = "warn"
        if cdc_diagnostics:
            envelope.diagnostics.extend(cdc_diagnostics)
            envelope.next_actions.append(cdc_diagnostics[0]["action"])
            if envelope.status == "pass":
                envelope.status = "warn"
        sys.exit(emit(envelope, json_mode=json_mode, strict=strict))

    except SystemExit:
        raise
    except Exception as e:
        # A malformed design.yml is a finding about the user's file (exit 1),
        # not a FORGE crash (exit 2) — see envelope.status_for_exception.
        envelope = CommandEnvelope(
            status=status_for_exception(e),
            diagnostics=[{"severity": "error", "message": str(e)}],
        )
        if json_mode:
            sys.exit(emit(envelope, json_mode=True))
        print_cli_error(
            "Validation failed",
            e,
            hint="Fix the reported design or contract issue, then re-run forge validate.",
        )
        sys.exit(envelope.exit_code())


def cmd_validate_registry(args):
    """Validate a modules.yml registry file independently."""
    from forge.core.cli.envelope import CommandEnvelope, emit, status_for_exception

    json_mode = getattr(args, "json", False)
    strict = getattr(args, "strict", False)
    try:
        registry_path = Path(args.registry).expanduser().resolve()
        if not registry_path.exists():
            envelope = CommandEnvelope(
                status="fail",
                diagnostics=[{
                    "severity": "error",
                    "message": f"Registry file not found: {registry_path}",
                }],
            )
            if not json_mode:
                print(f"❌ Registry file not found: {registry_path}")
                sys.exit(envelope.exit_code())
            sys.exit(emit(envelope, json_mode=True))

        if not json_mode:
            print(f"📖 Loading registry: {registry_path}")
            print("🔍 Validating registry...\n")
        validator = validate_registry(registry_path)
        if not json_mode:
            validator.print_report()

        envelope = _validators_to_envelope((validator, "registry"))

        if json_mode:
            sys.exit(emit(envelope, json_mode=True, strict=strict))
        if strict and envelope.status == "warn":
            print(
                f"\n⛔ Strict mode: treating {len(validator.warnings)} warning(s) as errors"
            )
        sys.exit(envelope.exit_code(strict=strict))

    except SystemExit:
        raise
    except Exception as e:
        envelope = CommandEnvelope(
            status=status_for_exception(e),
            diagnostics=[{"severity": "error", "message": str(e)}],
        )
        if json_mode:
            sys.exit(emit(envelope, json_mode=True))
        print_cli_error(
            "Registry validation failed",
            e,
            hint="Fix the modules.yml registry issue, then re-run forge validate-registry.",
        )
        sys.exit(envelope.exit_code())


def cmd_gen_top(args):
    """Generate algorithm top (Verilog, VHDL or Block Design TCL)."""
    try:
        design_path = Path(args.design).expanduser().resolve()

        if getattr(args, "rtl_resource_root", None):
            os.environ["TOPGEN_RTL_RESOURCE_ROOT"] = str(
                Path(args.rtl_resource_root).expanduser().resolve()
            )

        if not design_path.exists():
            print(f"❌ Design file not found: {design_path}")
            print("   💡 Check the path and try again")
            sys.exit(1)

        cfg = DesignConfig.load_relaxed(design_path)

        print("🔍 Validating design configuration...")
        validator = validate_design(cfg, design_path)
        validator.print_report()

        if validator.has_errors():
            sys.exit(1)

        print("✅ Validation passed!\n")

        options = GenTopOptions.from_namespace(args)
        ctx = compute_gen_top_plan(
            cfg, design_path, options, read_only=getattr(args, "dry_run", False),
        )

        failure = gate_failure(ctx, options)
        if failure:
            print(failure)
            sys.exit(1)

        if args.mode == "bd":
            # A block design's output is a Tcl script, not RTL: there is
            # nothing to testbench or lint, so asking for it is an error
            # rather than a flag silently ignored.
            if args.gen_testbench or (cfg.testbench and cfg.testbench.generate):
                print_cli_error(
                    "--gen-testbench is not supported with --mode bd",
                    ValueError("bd mode's output is a Tcl script, not RTL — there is nothing to testbench"),
                    hint="Generate a Verilog/VHDL top for testbench flows, or drive BD-mode "
                         "verification through Vivado directly.",
                )
                sys.exit(1)
            if getattr(args, "lint", False):
                print_cli_error(
                    "--lint is not supported with --mode bd",
                    ValueError("no Verilog/VHDL linter applies to a generated Tcl script"),
                    hint="Drop --lint, or use --mode verilog/vhdl if RTL linting is needed.",
                )
                sys.exit(1)

        if getattr(args, "dry_run", False):
            _print_dry_run(ctx, options, cfg, args)
            sys.exit(0)

        try:
            generated = generate_top(
                ctx, options,
                attach_verification=lambda project, dut_dir: _attach_verification_plan(
                    project, design_path, args, ctx.c_root, dut_dir=dut_dir,
                ),
            )
        except GenerationRefused as refused:
            print(refused)
            sys.exit(1)

        if args.mode in ("verilog", "vhdl"):
            if args.gen_testbench or (cfg.testbench and cfg.testbench.generate):
                _generate_testbench(generated.output, cfg, design_path, args, ctx.xml_stimulus_tool)
            if args.lint:
                _run_verilog_lint(generated.output, generated.top_name)
                if generated.vhdl_output is not None:
                    _run_vhdl_lint(generated.vhdl_output)

    except Exception as e:
        print_cli_error(
            "Generation failed",
            e,
            hint="Check the design file, mode, and IP paths, then re-run forge dev topgen gen-top.",
        )
        sys.exit(1)


def _print_dry_run(ctx, options: GenTopOptions, cfg, args) -> None:
    preview = Path(planned_output_artifacts(ctx, options)[0])
    if options.mode == "vhdl":
        print("\n[dry-run] Would also write the Verilog top it wraps:")
        print(f"  {preview.with_name(options.top_name + '_core.v')}")

    print("\n[dry-run] Would write:")
    print(f"  {preview}")
    for _artifact in (
        "build_manifest.json", "port_map.yaml", "port_signature.json",
        "design_parameters.json", "probe_map.yaml", "tb_bindings.svh",
        "maturity_report.json",
    ):
        print(f"  {preview.parent / _artifact}")
    # The canonical IR snapshot and its provenance are written in every mode.
    print(f"  {preview.parent / 'design.ir.json'}")
    print(f"  {preview.parent / 'provenance.json'}")
    if options.mode != "bd" and (args.gen_testbench or (cfg.testbench and cfg.testbench.generate)):
        print(f"  {preview.parent}/  (SystemVerilog testbench — exact name set by generate_sv_testbench)")

    assert not ctx.ip_info_generated_now, (
        "dry-run must never persist ip_info.yaml to disk"
    )
    print("\n[dry-run] No files written.")


def _generate_testbench(output: Path, cfg, design_path: Path, args, xml_stimulus_tool) -> None:
    print("\n🧪 Generating SystemVerilog testbench...")
    xml_path = None
    event_id = args.event_id
    if args.xml_stimulus:
        xml_path = Path(args.xml_stimulus)
    elif cfg.testbench and cfg.testbench.xml_stimulus_path:
        xml_path = design_path.parent / cfg.testbench.xml_stimulus_path
        event_id = cfg.testbench.event_id
        print("   ℹ️  Using testbench config from design.yml")
    if xml_path:
        if not xml_path.exists():
            print(f"⚠️  Warning: XML stimulus file not found: {xml_path}")
            print(f"   📁 Searched: {xml_path.absolute()}")
            print("   Will generate testbench with placeholder stimulus")
            xml_path = None
        else:
            print(f"   📄 XML stimulus: {xml_path}")
            print(f"   🔢 Event ID: {event_id}")
    tb_path = generate_sv_testbench(
        rtl_path=output,
        output_dir=output.parent,
        xml_stimulus_path=xml_path,
        event_id=event_id,
        stimulus_converter_path=xml_stimulus_tool,
    )
    print(f"✓ Testbench generated: {tb_path}")


# ---------------------------------------------------------------------------
# gen-top internal helpers
# ---------------------------------------------------------------------------

def _run_vhdl_lint(output: Path) -> None:
    try:
        from forge.core.utils.vhdl_linter import lint_vhdl_file
        print("\n🔍 Running GHDL syntax checker...")
        lint_vhdl_file(output, std="08", ieee="synopsys")
    except ImportError:
        print("⚠️  VHDL linter requires GHDL. Please install it:")
        print("   - Ubuntu/Debian: sudo apt-get install ghdl")
        print("   - Fedora/RHEL: sudo dnf install ghdl")
        print("   - macOS: brew install ghdl")


def _run_verilog_lint(output: Path, top_name: str) -> None:
    try:
        from forge.core.utils.verilog_linter import lint_verilog_file
        print("\n🔍 Running Verilog linter (Verilator)...")
        lint_verilog_file(output, top_module=top_name)
    except ImportError:
        print("⚠️  Verilog linter requires Verilator. Please install it:")
        print("   - Ubuntu/Debian: sudo apt-get install verilator")
        print("   - Fedora/RHEL: sudo dnf install verilator")
        print("   - macOS: brew install verilator")


# ---------------------------------------------------------------------------
# init-plugin command — scaffold the topgen side of a new plugin capsule
# ---------------------------------------------------------------------------
# Mirrors forge.verification.__main__._cmd_init_plugin's shape (template-string
# constants, files_to_create dict, --dry-run lists without writing, skip
# already-existing files on a real run). This scaffolds the *topgen* half
# (modules.yml, designs/design.yml, interfaces/*.yaml, an RTL stub);
# `forge dev verify init-plugin` scaffolds the verify/ half separately.
#
# plugin_id is used as both the module ref/registry name *and* the
# design.yml instance name, so ip_info_key trivially equals the design
# instance name from the start — sidestepping the exact ip_info_key
# mismatch bug found and fixed in plugins/passthrough_demo's own contract
# (see CHANGELOG.md).

_INIT_MODULES_YML_TEMPLATE = '''\
# ═══════════════════════════════════════════════════════════════════════════
# {plugin_id} — Module Registry
# ═══════════════════════════════════════════════════════════════════════════
# Scaffolded by `forge init`. See docs/PLUGIN_AUTHOR_GUIDE.md
# to register HLS modules, add topology_groups, or grow beyond this single
# RTL passthrough stub.
# ═══════════════════════════════════════════════════════════════════════════
registry_version: '1'

defaults:
  part: xcvu9p-flga2104-2L-e
  clock_period: 4.0
  vendor: {plugin_id}
  version: '1.0'

modules:

- name: {plugin_id}
  kind: rtl
  rtl_lang: verilog
  top: {plugin_id}
  latency_hint: 1
  interface_contract: interfaces/{plugin_id}.interface.yaml
  src: [../algo/rtl/{plugin_id}.v]
'''

_INIT_DESIGN_YML_TEMPLATE = '''\
# ═══════════════════════════════════════════════════════════════════════════
# {plugin_id} — Design Topology
# ═══════════════════════════════════════════════════════════════════════════
# Scaffolded by `forge init`. Single module, both data ports
# exposed at the top level — the minimal case. Add `connections:` or
# `topology_groups:` here as you add more modules.
# ═══════════════════════════════════════════════════════════════════════════

part: xcvu9p-flga2104-2L-e
clock_period: 4.0

block_protocol: none
connect_clock: true
connect_reset: true

registry: ../modules.yml

modules:

- name: {plugin_id}
  ref: {plugin_id}
  instances: 1
  external_in_ports: [data_in, data_in_valid]
  external_out_ports: [data_out, data_out_valid]
'''

_INIT_INTERFACE_YAML_TEMPLATE = '''\
# Integration contract for the {plugin_id} RTL reference module.
# Scaffolded by `forge init`.

ip_interface:
  module_name: {plugin_id}
  # Optional: defaults to module_name above. Declare it only when a design
  # instance (design.yml modules[].name) is named differently from its
  # module — `forge dev core verify-contract` names the available keys if it
  # doesn't resolve.
  ip_info_key: {plugin_id}
  source_type: rtl
  normalization_status: ready
  notes: >
    Minimal single-module reference generated by `forge init`.
    Registers an 8-bit data path one clock cycle.

  roles:

    clock_primary:
      raw_port: ap_clk
      direction: input
      width: 1

    reset_primary:
      raw_port: ap_rst
      direction: input
      width: 1
      active_level: high

    data_in:
      raw_port: data_in
      direction: input
      width: 8

    data_in_valid:
      raw_port: data_in_valid
      direction: input
      width: 1

    data_out:
      raw_port: data_out
      direction: output
      width: 8

    data_out_valid:
      raw_port: data_out_valid
      direction: output
      width: 1
'''

_INIT_RTL_STUB_TEMPLATE = '''\
//==============================================================================
// {plugin_id}.v
//==============================================================================
// Scaffolded by `forge init`. A minimal registered N-bit
// passthrough with a valid strobe — replace the body with your real logic.
// Ports and behavior deliberately match plugins/passthrough_demo's proven
// reference implementation, so `forge dev topgen gen-top` succeeds immediately
// with zero further edits.
//
// Parameters:
//   WIDTH - Bit width of the data path (default: 8)
//==============================================================================

`timescale 1ns / 1ps

module {plugin_id} #(
    parameter WIDTH = 8
)(
    input  wire             ap_clk,
    input  wire             ap_rst,
    input  wire [WIDTH-1:0] data_in,
    input  wire             data_in_valid,
    output reg  [WIDTH-1:0] data_out,
    output reg              data_out_valid
);

    always @(posedge ap_clk) begin
        if (ap_rst) begin
            data_out       <= {{WIDTH{{1'b0}}}};
            data_out_valid <= 1'b0;
        end else begin
            data_out       <= data_in;
            data_out_valid <= data_in_valid;
        end
    end

endmodule
'''


def cmd_init_plugin(args):
    """Scaffold the topgen side of a new plugin capsule.

    Creates ``<plugins_root>/<plugin_id>/forge/{{modules.yml,designs/design.yml,
    interfaces/<plugin_id>.interface.yaml}}`` and
    ``<algo_root>/<plugin_id>/algo/rtl/<plugin_id>.v``.
    """
    plugin_id = args.plugin_id
    plugins_root = Path(args.plugins_root)
    algo_root = Path(getattr(args, "algo_root", None) or args.plugins_root)
    forge_root = plugins_root / plugin_id / "forge"
    algo_dir = algo_root / plugin_id / "algo" / "rtl"

    files_to_create = {
        forge_root / "modules.yml": _INIT_MODULES_YML_TEMPLATE.format(plugin_id=plugin_id),
        forge_root / "designs" / "design.yml": _INIT_DESIGN_YML_TEMPLATE.format(plugin_id=plugin_id),
        forge_root / "interfaces" / f"{plugin_id}.interface.yaml":
            _INIT_INTERFACE_YAML_TEMPLATE.format(plugin_id=plugin_id),
        algo_dir / f"{plugin_id}.v": _INIT_RTL_STUB_TEMPLATE.format(plugin_id=plugin_id),
    }

    if args.dry_run:
        print(f"[init-plugin] Would create plugin skeleton for {plugin_id!r}:")
        for path in sorted(files_to_create):
            print(f"  {path}")
        return

    created: list[Path] = []
    skipped: list[Path] = []
    for dest_path, content in files_to_create.items():
        if dest_path.exists():
            skipped.append(dest_path)
            continue
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text(content)
        created.append(dest_path)

    print(f"[init-plugin] Plugin {plugin_id!r} scaffolded:")
    for p in created:
        print(f"  created  {p}")
    for p in skipped:
        print(f"  skipped  {p}  (already exists)")

    design_yml = forge_root / "designs" / "design.yml"
    modules_yml = forge_root / "modules.yml"
    # This command scaffolds only the topology half. `forge init` runs it as
    # one of its stages and hides this epilogue, so what's printed here is
    # what someone who ran *this* command alone still has to do.
    print("\nNext steps:")
    print(f"  1. Run: forge dev verify init-plugin {plugin_id}   (scaffolds the verify/ half)")
    print(
        f"  2. Run: forge build {design_yml} "
        f"--contracts-from {modules_yml} --output out/algo_top.v"
    )
    print(f"  3. Run: forge test prepare {forge_root / 'verify' / 'design.verification.yml'}")
    print("\n  (or run `forge init` next time to do all of the above in one step)")


def cmd_migrate(args):
    """Migration helpers — see
    docs/development/MIGRATION_TOOLING.md for the full policy of each kind.

    Every kind computes its change fully in memory, always prints a
    readable diff/plan, and only writes to disk when ``--dry-run`` is not
    passed — the same compute-then-write-gated-by-dry-run pattern used by
    ``cmd_gen_top``/``cmd_clean``/``cmd_init_plugin``.
    """
    from forge.generation.migrate import (
        apply_legacy_plugin_layout,
        apply_rename_verify_contract,
        find_legacy_plugin_layout,
        find_legacy_verify_contract_name,
        migrate_schema_version,
        partition_to_coordinates,
        unified_diff_text,
    )

    kind = args.kind
    dry_run = getattr(args, "dry_run", False)

    if kind == "schema-version":
        if not args.file:
            print("❌ --file is required for --kind schema-version", file=sys.stderr)
            sys.exit(2)
        if not args.file.exists():
            print(f"❌ file not found: {args.file}", file=sys.stderr)
            sys.exit(2)
        try:
            result = migrate_schema_version(args.file, schema_kind=args.schema_kind)
        except ValueError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            sys.exit(2)
        print(result.message)
        if result.diff:
            print(result.diff)
        if result.changed and not dry_run:
            args.file.write_text(result.new_content)
            print(f"✅ wrote {args.file}")
        sys.exit(0)

    elif kind == "partition-to-coordinates":
        if not args.contract or not args.axis:
            print("❌ --contract and --axis are required for --kind partition-to-coordinates", file=sys.stderr)
            sys.exit(2)
        if not args.contract.exists():
            print(f"❌ file not found: {args.contract}", file=sys.stderr)
            sys.exit(2)
        text = args.contract.read_text()
        try:
            new_text, changed_roles = partition_to_coordinates(text, axis=args.axis, role=args.role)
        except ValueError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            sys.exit(2)
        if not changed_roles:
            print(f"{args.contract.name}: nothing to migrate")
            sys.exit(0)
        print(unified_diff_text(text, new_text, args.contract))
        print(f"Roles migrated: {', '.join(changed_roles)}")
        if not dry_run:
            args.contract.write_text(new_text)
            print(f"✅ wrote {args.contract}")
        sys.exit(0)

    elif kind == "legacy-plugin-layout":
        if not args.plugin_root:
            print("❌ --plugin-root is required for --kind legacy-plugin-layout", file=sys.stderr)
            sys.exit(2)
        issue = find_legacy_plugin_layout(args.plugin_root)
        if issue.is_clean():
            print("Nothing to migrate — layout already current.")
            sys.exit(0)
        if issue.move_needed:
            print(f"Would move: {issue.old_verify_dir} -> {issue.new_verify_dir}")
        for f in issue.fw_python_files:
            print(f"Would remove _FW_PYTHON sys.path block from: {f}")
        for f in issue.manual_files:
            print(f"⚠️  {f}: mentions _FW_PYTHON but the block wasn't confidently "
                  "recognized — remove it manually.")
        if not dry_run:
            for action in apply_legacy_plugin_layout(issue):
                print(f"✅ {action}")
        sys.exit(0)

    elif kind == "rename-verify-contract":
        if not args.plugin_root:
            print("❌ --plugin-root is required for --kind rename-verify-contract", file=sys.stderr)
            sys.exit(2)
        legacy = find_legacy_verify_contract_name(args.plugin_root)
        if legacy is None:
            print("Nothing to migrate.")
            sys.exit(0)
        new_path = legacy.with_name("design.verification.yml")
        print(f"Would rename: {legacy} -> {new_path}")
        if not dry_run:
            apply_rename_verify_contract(legacy)
            print(f"✅ renamed to {new_path}")
        sys.exit(0)

    else:
        print(f"❌ unknown --kind: {kind!r}", file=sys.stderr)
        sys.exit(2)


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------

def register(sub) -> None:
    """Register the ``forge dev topgen`` subparser and its commands."""
    p_tg = sub.add_parser("topgen", help="Generate hardware topology structure")
    tg_sub = p_tg.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # forge dev topgen gen-top
    p_gen = tg_sub.add_parser("gen-top", help="Generate algorithm top-level")
    p_gen.add_argument("design", type=Path, help="design.yaml file")
    p_gen.add_argument(
        "--consumer-root", type=Path,
        help="Consumer workspace root used to resolve relative defaults",
    )
    p_gen.add_argument(
        "--mode", choices=["vhdl", "verilog", "bd"], required=True,
        help="Output mode: verilog, vhdl (a VHDL entity around the Verilog top, written as "
             "<top>_core.v), or bd (Block Design TCL)",
    )
    p_gen.add_argument("--ip-root", type=Path, help="IP directory (default: ips/)")
    p_gen.add_argument("--ip-info", type=Path, help="IP info file (auto-generated if missing)")
    p_gen.add_argument("--build-dir", type=Path, help="Build directory (default: build/)")
    p_gen.add_argument(
        "--hls-build-root", type=Path,
        help="HLS build directory (default: build_hls/)",
    )
    p_gen.add_argument(
        "--src-root", type=Path,
        help="Source root for HDL files (default: design file's directory)",
    )
    p_gen.add_argument("--system", type=Path, help="system.yml for global nets")
    p_gen.add_argument("--output", "-o", type=Path, help="Output file")
    p_gen.add_argument("--top-name", default="algo_top", help="Top entity/module name")
    p_gen.add_argument("--bd-name", default="design_1", help="BD name (BD mode)")
    p_gen.add_argument(
        "--gen-testbench", action="store_true",
        help="Generate SystemVerilog testbench (Verilog mode only)",
    )
    p_gen.add_argument("--xml-stimulus", type=Path, help="XML file for stimulus generation")
    p_gen.add_argument(
        "--xml-stimulus-tool", type=Path,
        help="Path to the xml_to_sv_stimulus helper binary",
    )
    p_gen.add_argument(
        "--event-id", type=int, default=1, help="Event ID to extract from XML (default: 1)",
    )
    p_gen.add_argument("--hls-metrics", type=Path, help="HLS metrics JSON file")
    p_gen.add_argument(
        "--contracts-from", type=Path, metavar="MODULES_YML",
        help="modules.yml with interface_contract paths; enables contract-driven wiring",
    )
    p_gen.add_argument(
        "--strict", action="store_true",
        help=(
            "Fail if any module lacks a contract, any connection uses auto-match or "
            "port_map_ranges, topology groups have verification errors, a "
            "declared cardinality (producers/consumers/fanout/completeness) is violated, "
            "or a connection crosses clock/reset domains without a declared 'cdc:' adapter"
        ),
    )
    p_gen.add_argument("--lint", action="store_true", help="Run linter after generation")
    p_gen.add_argument("--fix-lint", action="store_true", help="Auto-fix linting issues")
    p_gen.add_argument(
        "--rtl-resource-root", type=Path,
        help="Framework RTL helpers root; sets ${TOPGEN_RTL_RESOURCE_ROOT}",
    )
    p_gen.add_argument(
        "--verify-design", type=Path,
        help="design.verification.yml to resolve the IR's verification plan "
             "against (default: ../verify/design.verification.yml, when present)",
    )
    p_gen.add_argument(
        "--no-verify", action="store_true",
        help="Skip verification-plan resolution; the emitted IR carries no plan",
    )
    p_gen.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=False,
        help="Validate, resolve IP info, and match ports, then print what "
             "would be written without generating any output files.",
    )
    p_gen.set_defaults(func=cmd_gen_top)

    # forge dev topgen validate
    p_validate = tg_sub.add_parser("validate", help="Validate design.yml without generating")
    p_validate.add_argument("design", type=Path, help="design.yaml file to validate")
    p_validate.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    p_validate.add_argument(
        "--check-stale", action="store_true", default=False,
        help="Also verify that generated artifacts are not older than design.yml.",
    )
    p_validate.add_argument(
        "--cdc-result-json", dest="cdc_result_json", default=None,
        help="Write a forge.cdc_verification_result.v1 artifact to this path "
             "(only when the registry/contracts needed for the CDC check are available)",
    )
    p_validate.add_argument(
        "--json", action="store_true", default=False,
        help="Machine-readable JSON output instead of the human report.",
    )
    p_validate.set_defaults(func=cmd_validate)

    # forge dev topgen validate-registry
    p_val_reg = tg_sub.add_parser(
        "validate-registry", help="Validate a modules.yml registry independently",
    )
    p_val_reg.add_argument("registry", type=Path, help="modules.yml registry file to validate")
    p_val_reg.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    p_val_reg.add_argument(
        "--json", action="store_true", default=False,
        help="Machine-readable JSON output instead of the human report.",
    )
    p_val_reg.set_defaults(func=cmd_validate_registry)

    # forge dev topgen ip-summary
    p_summary = tg_sub.add_parser(
        "ip-summary", help="Generate IP metadata summary (ip_info.yaml)",
    )
    p_summary.add_argument("design", type=Path, help="design.yaml file")
    p_summary.add_argument(
        "--consumer-root", type=Path,
        help="Consumer workspace root used to resolve relative defaults",
    )
    p_summary.add_argument("--ip-root", type=Path, help="IP directory (default: ips/)")
    p_summary.add_argument("--build-dir", type=Path, help="Build directory (default: build/)")
    p_summary.add_argument(
        "--src-root", type=Path,
        help="Source root for HDL files (default: design file's directory)",
    )
    p_summary.add_argument(
        "--output", "-o", type=Path, help="Output file (default: ip_info.yaml)",
    )
    p_summary.set_defaults(func=cmd_ip_summary)

    # forge dev topgen match-ports
    p_match = tg_sub.add_parser("match-ports", help="Show port connection report")
    p_match.add_argument("design", type=Path, help="design.yaml file")
    p_match.add_argument(
        "--consumer-root", type=Path,
        help="Consumer workspace root used to resolve relative defaults",
    )
    p_match.add_argument("--ip-info", type=Path, help="IP info file (default: ip_info.yaml)")
    p_match.add_argument("--system", type=Path, help="system.yml for global nets")
    p_match.set_defaults(func=cmd_match_ports)

    # forge dev topgen unpack-ips
    p_unpack = tg_sub.add_parser("unpack-ips", help="Extract IP archives")
    p_unpack.add_argument(
        "src", type=Path,
        help="Directory containing IP archives (*.zip, *.tar.*)",
    )
    p_unpack.add_argument(
        "--consumer-root", type=Path,
        help="Consumer workspace root used to resolve default output paths",
    )
    p_unpack.add_argument("--ip-root", type=Path, help="Output directory (default: ips/)")
    p_unpack.add_argument("--force", action="store_true", help="Overwrite existing directories")
    p_unpack.set_defaults(func=cmd_unpack_ips)

    # forge dev topgen clean
    p_clean = tg_sub.add_parser("clean", help="Remove generated artifacts for one design")
    p_clean.add_argument("design", type=Path, help="design.yaml file")
    p_clean.add_argument(
        "--consumer-root", type=Path,
        help="Consumer workspace root used to resolve relative defaults",
    )
    p_clean.add_argument(
        "--output", "-o", type=Path,
        help="Generated top file path used to anchor DUT artifact cleanup",
    )
    p_clean.add_argument(
        "--ip-info", type=Path,
        help="Generated ip_info.yaml path if it lives outside the output directory",
    )
    p_clean.add_argument(
        "--verify-design", type=Path,
        help="design.verification.yml to also clean generated verify artifacts",
    )
    p_clean.add_argument(
        "--no-verify", action="store_true",
        help="Only clean DUT artifacts; leave verify-generated files untouched",
    )
    p_clean.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be removed without deleting files",
    )
    p_clean.set_defaults(func=cmd_clean)

    # forge dev topgen lint
    p_lint = tg_sub.add_parser("lint", help="Lint VHDL file(s) using GHDL")
    p_lint.add_argument("files", nargs="+", type=Path, help="VHDL file(s) to lint")
    p_lint.add_argument(
        "--fix", action="store_true",
        help="Placeholder for compatibility (GHDL doesn't auto-fix)",
    )
    p_lint.add_argument(
        "--std", default="08",
        help="VHDL standard (87, 93, 02, 08, 19) — default: 08",
    )
    p_lint.add_argument(
        "--ieee", default="synopsys",
        help="IEEE library (standard, synopsys, mentor) — default: synopsys",
    )
    p_lint.set_defaults(func=cmd_lint)

    # forge dev topgen lint-verilog
    p_lint_verilog = tg_sub.add_parser(
        "lint-verilog", help="Lint Verilog file(s) with Verilator",
    )
    p_lint_verilog.add_argument("files", nargs="+", type=Path, help="Verilog file(s) to lint")
    p_lint_verilog.add_argument(
        "--top-module", help="Top module name (auto-detected if not provided)",
    )
    p_lint_verilog.add_argument(
        "--no-warnings", action="store_true", help="Suppress warnings, show only errors",
    )
    p_lint_verilog.add_argument(
        "--strict", action="store_true", help="Enable strict mode with additional checks",
    )
    p_lint_verilog.set_defaults(func=cmd_lint_verilog)

    # forge dev topgen init-plugin
    p_init = tg_sub.add_parser(
        "init-plugin",
        help="Scaffold the topgen side of a new plugin (modules.yml, "
             "design.yml, interface contract, RTL stub)",
    )
    p_init.add_argument("plugin_id", help="Plugin identifier (e.g. 'my_algo')")
    p_init.add_argument(
        "--plugins-root", default="plugins",
        help="Root directory under which to create <plugin_id>/forge/ (default: plugins/)",
    )
    p_init.add_argument(
        "--algo-root", default=None,
        help="Root directory under which to create <plugin_id>/algo/ "
             "(default: same as --plugins-root)",
    )
    p_init.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=False,
        help="Print what would be created without writing files",
    )
    p_init.set_defaults(func=cmd_init_plugin)

    p_migrate = tg_sub.add_parser(
        "migrate",
        help="Migration helpers: schema-version insertion, partition->coordinates, "
             "legacy plugin layout, legacy verify-contract filename",
    )
    p_migrate.add_argument(
        "--kind", required=True,
        choices=[
            "schema-version", "partition-to-coordinates", "legacy-plugin-layout",
            "rename-verify-contract",
        ],
        help="Which migration to run",
    )
    p_migrate.add_argument(
        "--file", type=Path,
        help="[schema-version] path to the design.yml/modules.yml/*.interface.yaml/"
             "design.verification.yml file to migrate",
    )
    p_migrate.add_argument(
        "--schema-kind", dest="schema_kind",
        choices=["design", "registry", "interface", "verify_contract"],
        default=None,
        help="[schema-version] override auto-detection from --file's filename",
    )
    p_migrate.add_argument(
        "--contract", type=Path,
        help="[partition-to-coordinates] path to the *.interface.yaml file",
    )
    p_migrate.add_argument(
        "--axis", default=None,
        help="[partition-to-coordinates] axis name to wrap the partition value under",
    )
    p_migrate.add_argument(
        "--role", default=None,
        help="[partition-to-coordinates] migrate only this role (default: all eligible roles)",
    )
    p_migrate.add_argument(
        "--plugin-root", type=Path,
        help="[legacy-plugin-layout, rename-verify-contract] plugin root directory "
             "(the directory containing verify/ or forge/)",
    )
    p_migrate.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=False,
        help="Show the planned change/diff without writing anything",
    )
    p_migrate.set_defaults(func=cmd_migrate)
