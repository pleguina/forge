"""forge build — compute the generation plan, then generate.

``forge build`` with no design argument builds the ``forge.yml`` project it
is run in (see :mod:`forge.project.resolve`); ``forge build design.yml``
builds that design. Either way it first computes and prints a
``GenerationPlan`` (inferred/explicit connections, generated
transformations, latency changes, matching evidence, compatibility-mode
use, unresolved issues, output artifacts) through
``forge.core.cli.groups.topgen.compute_gen_top_plan`` with
``read_only=True``, then generates by delegating to
``topgen.cmd_gen_top``. ``--dry-run`` stops after the plan.

``--accept-plan-hash <hash>`` is a CI gate: exit 1 if the freshly computed
plan's hash doesn't match.

This command exposes a smaller flag surface than ``gen-top`` (no
``--bd-name``/``--gen-testbench``/``--xml-stimulus``/``--lint`` knobs);
gen-top's defaults fill those in when delegating.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _guided_failure(msg: str, *, json_mode: bool) -> int:
    """Same convention as `forge.core.cli.groups.inspect._guided_failure`:
    a plain stdout message (JSON gets a small envelope), status "fail" so
    the exit code stays 1 — these are pre-existing guided errors, not new
    "unexpected internal exception" cases, so they stay off exit code 2."""
    from forge.core.cli.envelope import CommandEnvelope

    envelope = CommandEnvelope(
        status="fail",
        diagnostics=[{"severity": "error", "message": msg}],
    )
    if json_mode:
        print(json.dumps(envelope.to_dict(), indent=2))
    else:
        print(f"❌ {msg}")
    return envelope.exit_code()


def _strict_violations(ctx) -> List[str]:
    """The checks only `gen-top --strict` applies
    (`core/cli/groups/topgen.py::cmd_gen_top`), as plain description
    strings instead of print+`sys.exit` — presentation only, reusing
    *ctx*'s already-computed `match_report`. Topology-group, cardinality
    and CDC errors are plan errors with or without --strict. Called only
    when `--strict` is set; an empty result means no violations."""
    violations: List[str] = []
    match_report = ctx.match_report
    wmc = match_report.wiring_method_counts

    if match_report.has_compat_modules():
        violations.append(
            "modules with no interface contract were wired via heuristics: "
            + ", ".join(match_report.compat_mode_modules)
        )
    if wmc.get("auto_match", 0) > 0:
        violations.append(
            f"{wmc['auto_match']} connection(s) used auto-match heuristics"
        )
    if wmc.get("port_map_ranges", 0) > 0:
        violations.append(
            f"{wmc['port_map_ranges']} connection(s) still use port_map_ranges"
        )
    return violations


def _unresolved_issue_to_dict(issue) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "severity": issue.severity, "message": issue.message, "code": None,
        "category": issue.category,
    }
    if issue.object_id:
        d["object_id"] = issue.object_id
    return d


def _build_command_options(args) -> Dict[str, Any]:
    """The subset of CLI args that affect plan output — recorded in the
    provenance manifest so a future `--explain-staleness` can tell "you
    changed how you called forge build" apart from "an input file changed"
    (same rationale as `inspect._command_options`/
    `topgen._gen_top_command_options`, scoped to `forge build`'s own flags)."""
    system = getattr(args, "system", None)
    contracts_from = getattr(args, "contracts_from", None)
    return {
        "mode": getattr(args, "mode", None),
        "top_name": getattr(args, "top_name", None),
        "strict": getattr(args, "strict", False),
        "system": str(system) if system is not None else None,
        "contracts_from": str(contracts_from) if contracts_from is not None else None,
    }


def cmd_build(args) -> None:
    from forge.core.cli.envelope import CommandEnvelope, emit
    from forge.core.cli.groups import topgen
    from forge.contracts.config import DesignConfig
    from forge.generation.validation import validate_design
    from forge.ir.plan import build_generation_plan, plan_hash

    json_mode = getattr(args, "json", False)
    strict = getattr(args, "strict", False)
    project = None
    if args.design is None:
        from forge.core.cli._shared import resolve_project_args

        try:
            project = resolve_project_args(args)
        except (FileNotFoundError, ValueError) as e:
            sys.exit(_guided_failure(str(e), json_mode=json_mode))
    args.mode = args.mode or "verilog"
    args.top_name = args.top_name or "algo_top"
    if project is not None and args.output is None:
        args.output = str(project.top_level(args.mode, args.top_name))
    design_path = Path(args.design).expanduser().resolve()

    if not design_path.exists():
        sys.exit(_guided_failure(f"Design file not found: {design_path}", json_mode=json_mode))

    try:
        cfg = DesignConfig.load_relaxed(design_path)
    except Exception as e:  # noqa: BLE001 - surfaced as a clean CLI error
        sys.exit(_guided_failure(f"Failed to load design: {e}", json_mode=json_mode))

    if not json_mode:
        print("🔍 Validating design configuration...")
    validator = validate_design(cfg, design_path)
    if not json_mode:
        validator.print_report()
    if validator.has_errors():
        sys.exit(_guided_failure("Design validation failed (see errors above)", json_mode=json_mode))
    if not json_mode:
        print("✅ Validation passed!\n")

    try:
        ctx = topgen.compute_gen_top_plan(
            cfg, design_path, args, read_only=True, emit_progress=not json_mode,
        )
    except Exception as e:  # noqa: BLE001 - surfaced as a clean CLI error
        sys.exit(_guided_failure(f"Failed to compute plan: {e}", json_mode=json_mode))
        return

    if ctx.auto_delays is not None and ctx.auto_delays.problems:
        sys.exit(_guided_failure(
            "delay_cycles: auto could not be resolved: " + "; ".join(ctx.auto_delays.problems),
            json_mode=json_mode,
        ))

    output_artifacts = _planned_output_artifacts(ctx, args)
    plan = build_generation_plan(
        ctx.project, ctx.match_report,
        topology_group_issues=ctx.topology_group_issues,
        cardinality_issues=ctx.cardinality_issues,
        cdc_issues=ctx.cdc_issues,
        compat_mode_modules=ctx.match_report.compat_mode_modules,
        output_artifacts=output_artifacts,
    )
    h = plan_hash(plan)

    accept_hash = getattr(args, "accept_plan_hash", None)
    if accept_hash and accept_hash != h:
        envelope = CommandEnvelope(
            status="fail",
            diagnostics=[{
                "severity": "error", "code": None,
                "message": "Plan hash mismatch: the design's resolved plan has "
                            "changed since that hash was recorded.",
            }],
            metrics={"expected_plan_hash": accept_hash, "actual_plan_hash": h},
        )
        if json_mode:
            sys.exit(emit(envelope, json_mode=True))
        print("❌ Plan hash mismatch:")
        print(f"   expected: {accept_hash}")
        print(f"   actual:   {h}")
        print("   The design's resolved plan has changed since that hash was recorded.")
        sys.exit(envelope.exit_code())

    diagnostics = [_unresolved_issue_to_dict(i) for i in plan.unresolved_issues]
    strict_violations = _strict_violations(ctx) if strict else []
    for v in strict_violations:
        diagnostics.append({
            "severity": "error", "code": None, "category": "strict_mode",
            "message": f"Strict mode: {v}",
        })

    has_error = any(d["severity"] == "error" for d in diagnostics)
    has_warning = any(d["severity"] == "warning" for d in diagnostics)
    status = "fail" if has_error else ("warn" if has_warning else "pass")

    artifacts = list(plan.output_artifacts)

    def write_requested_provenance() -> None:
        # Written last: generation writes a provenance.json of its own next
        # to the output, which would replace this one at the same path.
        if not getattr(args, "provenance", None):
            return
        from forge.ir.provenance import build_provenance, write_provenance

        prov_path = Path(args.provenance).expanduser().resolve()
        write_provenance(prov_path, build_provenance(
            ctx.project,
            command_options=_build_command_options(args),
            plan_hash=h,
            output_paths=list(plan.output_artifacts),
            project_identity=ctx.c_root.name or None,
        ))
        artifacts.append(str(prov_path))
        if not json_mode:
            print(f"✅ provenance manifest written to {prov_path}")

    if getattr(args, "explain_staleness", None):
        from forge.ir.provenance import build_provenance, explain_staleness, read_provenance

        prov_path = Path(args.explain_staleness).expanduser().resolve()
        if not prov_path.exists():
            sys.exit(_guided_failure(
                f"--explain-staleness file not found: {prov_path}", json_mode=json_mode,
            ))
        previous = read_provenance(prov_path)
        current = build_provenance(
            ctx.project, command_options=_build_command_options(args), plan_hash=h,
        )
        result = explain_staleness(previous, current)
        staleness_envelope = CommandEnvelope(
            status="fail" if result.stale else "pass",
            metrics={"explain_staleness": {"stale": result.stale, "reasons": result.reasons}},
        )
        if json_mode:
            sys.exit(emit(staleness_envelope, json_mode=True))
        if result.stale:
            print(f"⚠️  stale — {len(result.reasons)} reason(s):")
            for r in result.reasons:
                print(f"    - {r}")
        else:
            print("✅ fresh — no reason to regenerate")
        sys.exit(staleness_envelope.exit_code())

    envelope = CommandEnvelope(
        status=status,
        diagnostics=diagnostics,
        artifacts=artifacts,
        metrics={
            "plan_hash": h,
            "design_name": plan.design_name,
            "counts": {
                "inferred_connections": len(plan.inferred_connections),
                "explicit_connections": len(plan.explicit_connections),
                "transformations": len(plan.generated_transformations),
                "latency_changes": len(plan.latency_changes),
                "compat_mode_modules": len(plan.compat_mode_modules),
            },
        },
    )

    if not json_mode:
        _print_plan_human(plan, h, envelope)

    if getattr(args, "dry_run", False) or status == "fail":
        write_requested_provenance()
        if json_mode:
            sys.exit(emit(envelope, json_mode=True))
        sys.exit(envelope.exit_code())

    if not json_mode:
        print("\n▶ Generating …\n")
    ns = _gen_top_namespace_from_build_args(args)
    # gen-top narrates on stdout; under --json that would break the one
    # JSON document stdout must hold, so it goes to stderr instead.
    narration = contextlib.redirect_stdout(sys.stderr) if json_mode else contextlib.nullcontext()
    generated = 0
    with narration:
        try:
            topgen.cmd_gen_top(ns)
        except SystemExit as exc:
            generated = 0 if exc.code is None else int(exc.code)

    if generated != 0:
        envelope.status = "fail"
        envelope.diagnostics.append({
            "severity": "error", "code": None, "category": "generation",
            "message": "generation failed (see the messages above)",
        })
    else:
        write_requested_provenance()
    if json_mode:
        sys.exit(emit(envelope, json_mode=True))
    sys.exit(envelope.exit_code())


def _planned_output_artifacts(ctx, args) -> List[str]:
    """The exact artifact list `gen-top --dry-run` already previews for
    this mode (`forge/core/cli/groups/topgen.py`'s dry-run branch) —
    duplicated here rather than shared, to avoid touching gen-top's own
    code path for a purely read-only preview computation."""
    from forge.core.cli.groups.topgen import _resolve_path

    if args.mode == "vhdl":
        out = _resolve_path(args.output, ctx.c_root, Path(f"{args.top_name}.vhd"))
    elif args.mode == "verilog":
        out = _resolve_path(args.output, ctx.c_root, Path(f"{args.top_name}.v"))
    else:
        out = _resolve_path(args.output, ctx.c_root, Path("block_design.tcl"))

    artifacts = [str(out)]
    if args.mode in ("vhdl", "verilog"):
        for name in (
            "build_manifest.json", "port_map.yaml", "port_signature.json",
            "design_parameters.json", "probe_map.yaml", "tb_bindings.svh",
            "maturity_report.json",
        ):
            artifacts.append(str(out.parent / name))
        artifacts.append(str(out.parent / "design.ir.json"))
    elif args.mode == "bd":
        artifacts.append(str(out.parent / "design.ir.json"))
    return artifacts


def _gen_top_namespace_from_build_args(args):
    """Build an argparse.Namespace `topgen.cmd_gen_top` can consume
    directly — copies every attribute `forge build` already has, and
    fills in gen-top-only knobs `forge build` doesn't expose (see this
    module's docstring "Scope note") with gen-top's own defaults."""
    ns = argparse.Namespace(**vars(args))
    ns.dry_run = False
    # `strict` is already a real `forge build` flag and so is always
    # present on `args`/`ns` here — no hardcoded
    # fallback needed for it (unlike the gen-top-only knobs below, which
    # `forge build` deliberately doesn't expose; see this module's
    # docstring "Scope note").
    for name, default in (
        ("bd_name", "design_1"),
        ("gen_testbench", False),
        ("xml_stimulus", None),
        ("xml_stimulus_tool", None),
        ("event_id", 1),
        ("hls_metrics", None),
        ("lint", False),
        ("fix_lint", False),
        # Verification-plan resolution: gen-top discovers the conventional
        # design.verification.yml when neither is given, which is what
        # `forge build` wants too.
        ("verify_design", None),
        ("no_verify", False),
    ):
        if not hasattr(ns, name):
            setattr(ns, name, default)
    return ns


def _print_plan_human(plan, plan_hash_value: str, envelope) -> None:
    print(f"forge build — {plan.design_name}")
    print(f"  plan schema version : {plan.schema_version}")
    print(f"  plan hash            : {plan_hash_value}")
    print(f"  inferred connections : {len(plan.inferred_connections)}")
    print(f"  explicit connections : {len(plan.explicit_connections)}")
    print(f"  transformations      : {len(plan.generated_transformations)}")
    print(f"  latency changes      : {len(plan.latency_changes)}")
    print(f"  compat-mode modules  : {plan.compat_mode_modules or '(none)'}")
    print(f"  output artifacts     : {len(plan.output_artifacts)}")
    for a in plan.output_artifacts:
        print(f"    - {a}")
    if envelope.diagnostics:
        print(f"  unresolved issues ({len(envelope.diagnostics)}):")
        icon = {"error": "❌", "warning": "⚠️ ", "info": "ℹ️ "}
        for d in envelope.diagnostics:
            print(f"    {icon.get(d['severity'], '•')} [{d.get('category', '')}] {d['message']}")
    else:
        print("  unresolved issues    : (none)")


def register(sub) -> None:
    """Register the top-level ``forge build`` command."""
    p = sub.add_parser(
        "build",
        help="Generate the structural top level (--dry-run: print the plan only)",
    )
    p.add_argument(
        "design", nargs="?", default=None,
        help="Path to design.yml (default: the forge.yml project around the current directory)",
    )
    p.add_argument(
        "--mode", choices=["vhdl", "verilog", "bd"], default=None,
        help="Output mode: vhdl, verilog (default), or bd (Block Design TCL)",
    )
    p.add_argument("--consumer-root", help="Consumer workspace root used to resolve relative defaults")
    p.add_argument("--ip-root", help="IP directory (default: ips/)")
    p.add_argument("--ip-info", help="IP info file (auto-generated if missing)")
    p.add_argument("--build-dir", help="Build directory (default: build/)")
    p.add_argument("--hls-build-root", help="HLS build directory (default: build_hls/)")
    p.add_argument("--src-root", help="Source root for HDL files (default: design file's directory)")
    p.add_argument("--system", help="system.yml for global nets")
    p.add_argument("--output", "-o", help="Output file (same meaning as `gen-top --output`)")
    p.add_argument("--top-name", default=None, help="Top entity/module name (default: algo_top)")
    p.add_argument(
        "--contracts-from", metavar="MODULES_YML",
        help="modules.yml with interface_contract paths; enables contract-driven wiring",
    )
    p.add_argument(
        "--rtl-resource-root", help="Framework RTL helpers root; sets ${TOPGEN_RTL_RESOURCE_ROOT}",
    )
    p.add_argument(
        "--dry-run", "--plan", dest="dry_run", action="store_true", default=False,
        help="Compute and print the generation plan only; write nothing",
    )
    # Generation is the default; --apply is still accepted so existing
    # command lines keep working.
    p.add_argument("--apply", action="store_true", default=False, help=argparse.SUPPRESS)
    p.add_argument(
        "--strict", action="store_true", default=False,
        help="Also fail on heuristic wiring (modules with no contract, auto-match, "
             "port_map_ranges), as `gen-top --strict` does. Topology-group, cardinality "
             "and clock-domain-crossing errors fail without it.",
    )
    p.add_argument(
        "--accept-plan-hash", default=None, metavar="HASH",
        help="Fail (exit 1) if the freshly computed plan's hash doesn't match HASH — a CI gate",
    )
    p.add_argument(
        "--provenance", help="Write a content-hash provenance manifest (with this plan's hash) to this path",
    )
    p.add_argument(
        "--explain-staleness",
        help="Compare against a previously --provenance'd manifest and explain why it's stale",
    )
    p.add_argument("--json", action="store_true", default=False, help="Machine-readable JSON output")
    p.set_defaults(func=cmd_build)
