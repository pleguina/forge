"""forge build — deterministic generation plan (release-plan §3.4).

``forge build design.yml`` computes and prints a ``GenerationPlan``
(inferred/explicit connections, generated transformations, latency
changes, matching evidence, compatibility-mode use, unresolved issues,
output artifacts) without writing anything — the same read-only guarantee
``forge inspect``/``gen-top --dry-run`` already provide, reusing
``forge.core.cli.groups.topgen.compute_gen_top_plan`` (the same compute
phase ``gen-top`` itself uses) with ``read_only=True`` unconditionally.

``--apply`` performs the real generation by delegating to
``topgen.cmd_gen_top`` directly — this reuses 100% of gen-top's existing
write logic (artifact generation, manifests, testbenches, IR snapshot,
tie-off/top-port attachment, lint) with zero duplication here.

``--accept-plan-hash <hash>`` is a CI gate: exit 1 if the freshly computed
plan's hash doesn't match. No CI *pipeline* is implemented — this is just
the flag/exit-code contract any CI runner can already invoke.

Scope note: this command intentionally exposes a smaller flag surface
than ``gen-top`` (no ``--bd-name``/``--gen-testbench``/``--xml-stimulus``/
``--lint`` knobs) — a deliberate v1 choice, not an oversight. ``--apply``
fills in gen-top's defaults for those when delegating.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Optional


def cmd_build(args) -> None:
    from forge.core.cli.groups import topgen
    from forge.topgen.config import DesignConfig
    from forge.topgen.validation import validate_design
    from forge.ir.plan import build_generation_plan, plan_hash

    json_mode = getattr(args, "json", False)
    design_path = Path(args.design).expanduser().resolve()

    def _fail(msg: str) -> "None":
        if json_mode:
            print(json.dumps({"error": msg}, indent=2))
        else:
            print(f"❌ {msg}")
        sys.exit(1)

    if not design_path.exists():
        _fail(f"Design file not found: {design_path}")

    try:
        cfg = DesignConfig.load_relaxed(design_path)
    except Exception as e:  # noqa: BLE001 - surfaced as a clean CLI error
        _fail(f"Failed to load design: {e}")
        return

    if not json_mode:
        print("🔍 Validating design configuration...")
    validator = validate_design(cfg, design_path)
    if not json_mode:
        validator.print_report()
    if validator.has_errors():
        _fail("Design validation failed (see errors above)")
        return
    if not json_mode:
        print("✅ Validation passed!\n")

    try:
        ctx = topgen.compute_gen_top_plan(
            cfg, design_path, args, read_only=True, emit_progress=not json_mode,
        )
    except Exception as e:  # noqa: BLE001 - surfaced as a clean CLI error
        _fail(f"Failed to compute plan: {e}")
        return

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
        if json_mode:
            print(json.dumps(
                {"error": "plan hash mismatch", "expected": accept_hash, "actual": h},
                indent=2,
            ))
        else:
            print("❌ Plan hash mismatch:")
            print(f"   expected: {accept_hash}")
            print(f"   actual:   {h}")
            print("   The design's resolved plan has changed since that hash was recorded.")
        sys.exit(1)

    if json_mode:
        from dataclasses import asdict
        print(json.dumps({"plan": asdict(plan), "plan_hash": h}, indent=2, sort_keys=True))
    else:
        _print_plan_human(plan, h)

    if getattr(args, "apply", False):
        if not json_mode:
            print("\n▶ Applying — delegating to `topgen gen-top` for real generation …\n")
        ns = _gen_top_namespace_from_build_args(args)
        topgen.cmd_gen_top(ns)  # SystemExit propagates: forge build's own exit code IS this one
        return

    exit_code = 1 if any(i.severity == "error" for i in plan.unresolved_issues) else 0
    sys.exit(exit_code)


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
    import argparse

    ns = argparse.Namespace(**vars(args))
    ns.dry_run = False
    for name, default in (
        ("bd_name", "design_1"),
        ("gen_testbench", False),
        ("xml_stimulus", None),
        ("xml_stimulus_tool", None),
        ("event_id", 1),
        ("hls_metrics", None),
        ("lint", False),
        ("fix_lint", False),
        ("strict", False),
    ):
        if not hasattr(ns, name):
            setattr(ns, name, default)
    return ns


def _print_plan_human(plan, plan_hash_value: str) -> None:
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
    if plan.unresolved_issues:
        print(f"  unresolved issues ({len(plan.unresolved_issues)}):")
        icon = {"error": "❌", "warning": "⚠️ ", "info": "ℹ️ "}
        for i in plan.unresolved_issues:
            print(f"    {icon.get(i.severity, '•')} [{i.category}] {i.message}")
    else:
        print("  unresolved issues    : (none)")


def register(sub) -> None:
    """Register the top-level ``forge build`` command."""
    p = sub.add_parser(
        "build",
        help="Compute (and optionally apply) a deterministic generation plan",
    )
    p.add_argument("design", help="Path to design.yml")
    p.add_argument(
        "--mode", choices=["vhdl", "verilog", "bd"], default="verilog",
        help="Output mode: vhdl, verilog, or bd (Block Design TCL)",
    )
    p.add_argument("--consumer-root", help="Consumer workspace root used to resolve relative defaults")
    p.add_argument("--ip-root", help="IP directory (default: ips/)")
    p.add_argument("--ip-info", help="IP info file (auto-generated if missing)")
    p.add_argument("--build-dir", help="Build directory (default: build/)")
    p.add_argument("--hls-build-root", help="HLS build directory (default: build_hls/)")
    p.add_argument("--src-root", help="Source root for HDL files (default: design file's directory)")
    p.add_argument("--system", help="system.yml for global nets")
    p.add_argument("--output", "-o", help="Output file (same meaning as `gen-top --output`)")
    p.add_argument("--top-name", default="algo_top", help="Top entity/module name")
    p.add_argument(
        "--contracts-from", metavar="MODULES_YML",
        help="modules.yml with interface_contract paths; enables contract-driven wiring",
    )
    p.add_argument(
        "--rtl-resource-root", help="Framework RTL helpers root; sets ${TOPGEN_RTL_RESOURCE_ROOT}",
    )
    p.add_argument(
        "--plan", action="store_true", default=True,
        help="Compute and print the generation plan (the default behavior — "
             "this flag exists for symmetry with --apply)",
    )
    p.add_argument(
        "--apply", action="store_true", default=False,
        help="After computing the plan, generate for real (delegates to `topgen gen-top`)",
    )
    p.add_argument(
        "--accept-plan-hash", default=None, metavar="HASH",
        help="Fail (exit 1) if the freshly computed plan's hash doesn't match HASH — a CI gate",
    )
    p.add_argument("--json", action="store_true", default=False, help="Machine-readable JSON output")
    p.set_defaults(func=cmd_build)
