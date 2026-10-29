#!/usr/bin/env python3
"""Framework CLI entry point.

Provides ``python -m forge.verification <command>`` for any consumer.
When installed as a package, also available as ``forge verify <command>``.

Commands
--------
preflight <flow.yml>
    Run artifact existence and port-signature consistency checks.

run <flow.yml>
    Execute the full verification lifecycle (prepare → simulate → check).
    Pass ``--strict`` to fail on any deviation from the canonical path.
    For v1.0 verification, the framework-standard dataset contract is XML-backed,
    with framework-standard run selectors such as ``--xml-input``, ``--event-id``,
    ``--event-list``, and ``--all-events``.

generate <design.verification.yml>
    Generate verify.flow.yml, tb_*.sv, and wave.tcl for declared flows.
    Pass ``--strict`` to fail when required RTL is absent rather than skipping.

prepare <design.verification.yml>
    Combined generate + stimulus contract validation + layout check in one step.
    Intended as the normal "make everything ready to run" entrypoint.

init-plugin <plugin_id>
    Scaffold a new plugin skeleton under plugins/<plugin_id>/forge/verify/.

doctor <design.verification.yml>
    Run a comprehensive health-check: bootstrap importability, tool
    availability, artifact completeness, stimulus validity, layout compliance.
    Pass ``--json`` for machine-readable output suitable for CI consumption.

Global flags
------------
``--debug``  Print full Python tracebacks and exception chains on failure
             (in addition to the normal concise error message).

Usage
-----
::

    forge dev verify preflight verify.flow.yml
    forge dev verify run       verify.flow.yml --plugin my_plugin
    forge dev verify generate  design.verification.yml
    forge dev verify generate  design.verification.yml --flow hit_decoder_xsim --strict
    forge dev verify prepare   design.verification.yml
    forge dev verify init-plugin my_new_plugin
    forge dev verify doctor    plugins/my_plugin/forge/verify/design.verification.yml
    forge dev verify doctor    plugins/my_plugin/forge/verify/design.verification.yml --json
    forge verify --debug   doctor  plugins/my_plugin/forge/verify/design.verification.yml
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

from forge.verification.run import (
    XmlRunSelection,
    bootstrap_for_flow,
    build_runtime_context,
    load_flow_config,
    print_guided_error,
    resolve_consumer_root,
    resolve_run_selection,
    run_dataset_parts,
    run_loaded_flow,
)

if TYPE_CHECKING:
    from forge.verification.diagnostics import DiagnosticReport


def _cmd_preflight(args: argparse.Namespace) -> int:
    """Implement the ``preflight`` sub-command."""
    # ── Resolve consumer root ──────────────────────────────────────────────
    consumer_root_str = args.consumer_root or os.environ.get("VERIFY_CONSUMER_ROOT")

    # ── Bootstrap the plugin ───────────────────────────────────────────────
    if not bootstrap_for_flow(args.plugin, args.flow_file):
        return 1

    # ── Load the flow ──────────────────────────────────────────────────────
    from forge.verification.flow_loader import load_generic_flow
    from forge.verification.exceptions import ForgeVerifyError

    try:
        cfg = load_generic_flow(
            Path(args.flow_file),
            Path(consumer_root_str).resolve() if consumer_root_str else None,
        )
    except ForgeVerifyError as exc:
        return print_guided_error(
            "flow",
            exc,
            action="Check the verify.flow.yml path and consumer root, then re-run forge dev verify preflight.",
            extra=["Use --debug for traceback details if the message is still unclear."],
        )

    # ── Run preflight ──────────────────────────────────────────────────────
    from forge.verification.preflight import run_preflight

    result = run_preflight(cfg)
    result.print_summary()
    return 0 if result.ok else 1


def _resolve_xml_run_selection(args: argparse.Namespace, cfg) -> XmlRunSelection:
    """Resolve framework-standard XML dataset run selectors from CLI args.

    Precedence:
      CLI args > environment variables > flow defaults.
    """
    return resolve_run_selection(
        cfg,
        event_id=args.event_id,
        event_list=getattr(args, "event_list", None),
        all_events=getattr(args, "all_events", False),
        all_dataset_parts=getattr(args, "all_dataset_parts", False),
        dataset_parts_glob=getattr(args, "dataset_parts_glob", None),
        xml_input=args.xml_input,
        probe_log=args.probe_log,
    )


def _cmd_run(args: argparse.Namespace) -> int:
    """Implement the ``run`` sub-command.

    Full lifecycle:
      1.  resolve consumer root
      2.  bootstrap plugin
      3.  load flow (plugin-specific subclass via importlib if available)
      4.  build runtime context (CLI > env > flow defaults)
      5.  preflight
      6.  get backend adapter
      7.  validate backend requirements
      8.  prepare_backend_inputs()
      9.  run_backend()
      10. run_checker() if configured and backend succeeded
      11. artifact summary
    """
    consumer_root = resolve_consumer_root(args.consumer_root)

    # ── 2. Bootstrap ────────────────────────────────────────────────────────
    if not bootstrap_for_flow(args.plugin, args.flow_file):
        return 1

    flow_path = Path(args.flow_file)

    # ── 3. Load flow ────────────────────────────────────────────────────────
    from forge.verification.exceptions import ForgeVerifyError
    try:
        cfg, importlib_module = load_flow_config(flow_path, consumer_root)
    except ForgeVerifyError as exc:
        return print_guided_error(
            "flow",
            exc,
            action="Fix the flow YAML or consumer-root path, then re-run forge dev verify run.",
        )

    # ── 4. Build runtime context ─────────────────────────────────────────────
    selection = _resolve_xml_run_selection(args, cfg)

    # ── 6. Get backend adapter ────────────────────────────────────────────────
    from forge.verification.backend_registry import get_adapter
    try:
        adapter = get_adapter(cfg.backend)
    except (KeyError, LookupError) as exc:
        return print_guided_error(
            "backend",
            exc,
            action="Use a supported backend id in the flow file or bootstrap a plugin-specific backend override.",
        )

    strict = getattr(args, "strict", False)
    if selection.all_dataset_parts:
        return run_dataset_parts(flow_path, cfg, selection, adapter, importlib_module, strict=strict)

    ctx = build_runtime_context(flow_path, cfg, selection, importlib_module)
    return run_loaded_flow(flow_path, cfg, selection, ctx, adapter, strict=strict)


def _cmd_generate(args: argparse.Namespace) -> int:
    from forge.verification.prepare import generate_flows

    return generate_flows(
        args.design_file,
        flow=args.flow,
        consumer_root=args.consumer_root,
        port_map=args.port_map,
        dry_run=args.dry_run,
        use_kind_subdir=getattr(args, "use_kind_subdir", False),
        strict=getattr(args, "strict", False),
    )


def _cmd_prepare(args: argparse.Namespace) -> int:
    from forge.verification.prepare import prepare_flows

    return prepare_flows(
        args.design_file,
        flow=getattr(args, "flow", None),
        consumer_root=getattr(args, "consumer_root", None),
        port_map=getattr(args, "port_map", None),
        dry_run=getattr(args, "dry_run", False),
        strict=getattr(args, "strict", False),
    )


def _cmd_init_plugin(args: argparse.Namespace) -> int:
    from forge.verification.plugin_scaffold import scaffold_plugin

    return scaffold_plugin(args.plugin_id, args.plugins_root, dry_run=args.dry_run)


def _cmd_doctor(args: argparse.Namespace) -> int:
    from forge.verification.doctor import doctor_report

    report = doctor_report(args.design_file, getattr(args, "consumer_root", None))
    return _doctor_emit(
        report, strict=getattr(args, "strict", False), json_mode=getattr(args, "json", False),
    )


def _doctor_emit(
    report: "DiagnosticReport",
    *,
    strict: bool,
    json_mode: bool,
) -> int:
    """Build a `CommandEnvelope` from *report* and emit it.

    Fixes a latent bug: `DiagnosticReport.to_dict()` only ever emitted
    `"pass"`/`"fail"`, even when warnings (no errors) were present —
    `from_diagnostic_report` derives the real 3-way status instead. This
    wraps `DiagnosticReport`, it does not replace it — `FWVxxxx` codes and
    the class itself are unchanged.
    """
    from forge.core.cli.envelope import emit, from_diagnostic_report

    envelope = from_diagnostic_report(report, strict=strict)
    return emit(envelope, json_mode=json_mode, strict=strict)


def _cmd_release_check(args: argparse.Namespace) -> int:
    """Implement the ``release-check`` sub-command.

    Executes all RC-01…RC-10 release-readiness criteria in a single call and
    emits either a human-readable summary (default) or machine-readable JSON.

    Exit codes:
      0  All criteria pass.
      1  One or more criteria failed (or any warning when ``--strict``).
      2  Unexpected internal error.
    """
    from forge.verification.release_check import run_release_check

    design_path   = Path(args.design_file).resolve()
    consumer_root = resolve_consumer_root(getattr(args, "consumer_root", None))
    strict        = getattr(args, "strict", False)
    json_mode     = getattr(args, "json", False)
    check_tools   = getattr(args, "check_tools", False)
    check_stale   = getattr(args, "check_stale", True)

    result = run_release_check(
        design_path,
        consumer_root,
        check_tools=check_tools,
        check_stale=check_stale,
    )

    return _doctor_emit(result.report, strict=strict, json_mode=json_mode)


def build_parser() -> argparse.ArgumentParser:
    """Construct the ``forge dev verify`` argument parser (all sub-commands registered).

    Factored out of `main()` so tests can introspect the real, live command
    tree instead of duplicating it by hand.
    """
    parser = argparse.ArgumentParser(
        prog="forge dev verify",
        description="Framework verification utilities.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── preflight ────────────────────────────────────────────────────────────
    p_pre = sub.add_parser("preflight", help="Run DUT artifact preflight checks.")
    p_pre.add_argument("flow_file", help="Path to verify.flow.yml")
    p_pre.add_argument(
        "--consumer-root",
        help="Repo root for path resolution "
             "(default: VERIFY_CONSUMER_ROOT env var)",
    )
    p_pre.add_argument(
        "--plugin",
        help="Plugin ID to bootstrap before loading the flow "
             "(use when flow.plugin is not declared in the YAML)",
    )

    # ── run ──────────────────────────────────────────────────────────────────
    p_run = sub.add_parser(
        "run",
        help="Execute the full verification lifecycle (prepare → simulate → check).",
    )
    p_run.add_argument("flow_file", help="Path to verify.flow.yml")
    p_run.add_argument(
        "--consumer-root",
        help="Repo root for path resolution (default: VERIFY_CONSUMER_ROOT env var)",
    )
    p_run.add_argument(
        "--plugin",
        help="Plugin ID to bootstrap (use when flow.plugin is absent from YAML)",
    )
    p_run.add_argument(
        "--event-id",
        dest="event_id",
        default=None,
        help=(
            "Framework-standard XML dataset event selector: choose one event "
            "to simulate (overrides EVENT_ID env var and flow dataset.default_event_id)"
        ),
    )
    p_run.add_argument(
        "--all-events",
        dest="all_events",
        action="store_true",
        default=False,
        help=(
            "Framework-standard XML dataset selector: feed every available event "
            "sequentially in one simulation (overrides ALL_EVENTS=1)"
        ),
    )
    p_run.add_argument(
        "--event-list",
        dest="event_list",
        default=None,
        help=(
            "Framework-standard XML dataset selector: comma-separated event IDs "
            "or ranges (for example: 1,7,10-12)"
        ),
    )
    p_run.add_argument(
        "--xml-input",
        dest="xml_input",
        default=None,
        help=(
            "Framework-standard dataset override: path to XML stimulus file "
            "(overrides XML_INPUT env var and flow dataset.xml)"
        ),
    )
    p_run.add_argument(
        "--all-dataset-parts",
        dest="all_dataset_parts",
        action="store_true",
        default=False,
        help=(
            "Framework-standard XML dataset selector: run every XML part declared "
            "by dataset.parts_glob/parts, aggregating pass/fail"
        ),
    )
    p_run.add_argument(
        "--dataset-parts-glob",
        dest="dataset_parts_glob",
        default=None,
        help=(
            "Override XML dataset part glob for --all-dataset-parts; relative paths "
            "are resolved from consumer-root"
        ),
    )
    p_run.add_argument(
        "--probe-log",
        dest="probe_log",
        action="store_true",
        default=False,
        help="Enable Tier 2 probe CSV capture (overrides PROBE_LOG env var)",
    )

    # ── generate ─────────────────────────────────────────────────────────────
    p_gen = sub.add_parser(
        "generate",
        help="Generate verify.flow.yml, tb_*.sv, and wave.tcl from design.verification.yml.",
    )
    p_gen.add_argument(
        "design_file",
        help="Path to design.verification.yml",
    )
    p_gen.add_argument(
        "--flow",
        default=None,
        help="Generate only this named flow (default: all flows)",
    )
    p_gen.add_argument(
        "--consumer-root",
        default=None,
        help="Repo root for path resolution (default: VERIFY_CONSUMER_ROOT env var)",
    )
    p_gen.add_argument(
        "--port-map",
        default=None,
        help="Path to port_map.yaml for TB port connections "
             "(auto-discovered per flow dir if absent)",
    )
    p_gen.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=False,
        help="Print what would be generated without writing files",
    )
    p_gen.add_argument(
        "--use-kind-subdir",
        dest="use_kind_subdir",
        action="store_true",
        default=False,
        help="Write flow artifacts under <kind>/<flow_name>/ instead of the "
             "default flat <flow_name>/ layout",
    )
    p_gen.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help=(
            "Strict mode: fail (exit 1) when RTL is absent for xsim flows rather "
            "than skipping, and reject any non-canonical layout deviation."
        ),
    )

    # ── prepare ──────────────────────────────────────────────────────────────
    p_prep = sub.add_parser(
        "prepare",
        help=(
            "Generate artifacts + validate stimulus + check layout in one step.  "
            "The normal 'make everything ready to run' entrypoint.  "
            "Writes files — use doctor for a read-only health check."
        ),
    )
    p_prep.add_argument("design_file", help="Path to design.verification.yml")
    p_prep.add_argument(
        "--flow", default=None,
        help="Prepare only this named flow (default: all flows)",
    )
    p_prep.add_argument(
        "--consumer-root", default=None,
        help="Repo root for path resolution (default: VERIFY_CONSUMER_ROOT env var)",
    )
    p_prep.add_argument(
        "--port-map", default=None,
        help="Path to port_map.yaml for TB port connections",
    )
    p_prep.add_argument(
        "--dry-run", dest="dry_run",
        action="store_true", default=False,
        help="Print what would be done without writing files",
    )
    p_prep.add_argument(
        "--strict",
        action="store_true", default=False,
        help=(
            "Strict mode: fail on missing RTL for xsim flows (instead of skipping), "
            "any layout violation, and any stimulus contract error."
        ),
    )

    # ── init-plugin ──────────────────────────────────────────────────────────
    p_init = sub.add_parser(
        "init-plugin",
        help="Scaffold a new plugin skeleton under plugins/<plugin_id>/forge/verify/.",
    )
    p_init.add_argument(
        "plugin_id",
        help="Plugin identifier (e.g. 'my_trigger')",
    )
    p_init.add_argument(
        "--plugins-root", default="plugins",
        help="Root directory under which to create the plugin (default: plugins/)",
    )
    p_init.add_argument(
        "--dry-run", dest="dry_run",
        action="store_true", default=False,
        help="Print what would be created without writing files",
    )

    # ── doctor ───────────────────────────────────────────────────────────────
    p_doc = sub.add_parser(
        "doctor",
        help=(
            "Run a comprehensive health-check on a plugin's verify setup.  "
            "READ-ONLY — never writes any files."
        ),
    )
    p_doc.add_argument(
        "design_file",
        help="Path to design.verification.yml",
    )
    p_doc.add_argument(
        "--consumer-root", default=None,
        help="Repo root for path resolution (default: VERIFY_CONSUMER_ROOT env var)",
    )
    p_doc.add_argument(
        "--strict",
        action="store_true", default=False,
        help=(
            "Strict mode: return non-zero exit code if there are ANY warnings "
            "(in addition to errors).  Use this in CI to enforce a warning-free setup."
        ),
    )
    p_doc.add_argument(
        "--json",
        dest="json",
        action="store_true",
        default=False,
        help=(
            "Machine-readable JSON output.  Emits a DiagnosticReport as JSON "
            "to stdout.  Suitable for CI and automated support tooling."
        ),
    )

    # ── release-check ─────────────────────────────────────────────────────
    p_rc = sub.add_parser(
        "release-check",
        help=(
            "Run the release-readiness gate — all RC-01…RC-10 criteria in one "
            "command.  READ-ONLY — never writes any files."
        ),
    )
    p_rc.add_argument(
        "design_file",
        help="Path to design.verification.yml",
    )
    p_rc.add_argument(
        "--consumer-root", default=None,
        help="Repo root for path resolution (default: VERIFY_CONSUMER_ROOT env var)",
    )
    p_rc.add_argument(
        "--strict",
        action="store_true", default=False,
        help="Treat warnings as errors (RC gate fails on any WARNING).",
    )
    p_rc.add_argument(
        "--json",
        dest="json",
        action="store_true",
        default=False,
        help="Machine-readable JSON output (DiagnosticReport).",
    )
    p_rc.add_argument(
        "--check-tools",
        action="store_true", default=False,
        help=(
            "Also verify simulator binaries (xvlog, xelab, xsim) are on PATH.  "
            "Disabled by default so the gate runs in tool-less CI containers."
        ),
    )
    p_rc.add_argument(
        "--no-stale",
        dest="check_stale",
        action="store_false", default=True,
        help="Skip the RC-10 stale-artifact detection.",
    )

    # ── run ──────────────────────────────────────────────────────────────────
    # (add --strict to existing run parser)
    p_run.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help=(
            "Strict mode: fail if any legacy hand-authored artifacts are detected "
            "where framework generation is expected, or if the canonical layout "
            "is violated."
        ),
    )

    return parser


def main() -> None:
    parser = build_parser()

    # ── Global --debug flag (must be parsed before subcommand dispatch) ──────
    # We do a pre-parse to extract --debug before handing off to subparsers.
    # argparse does not support true global flags before the subcommand, so we
    # check sys.argv directly.
    _debug_mode = "--debug" in sys.argv
    _argv = [a for a in sys.argv[1:] if a != "--debug"]

    args = parser.parse_args(_argv)

    try:
        if args.command == "preflight":
            sys.exit(_cmd_preflight(args))
        elif args.command == "run":
            sys.exit(_cmd_run(args))
        elif args.command == "generate":
            sys.exit(_cmd_generate(args))
        elif args.command == "prepare":
            sys.exit(_cmd_prepare(args))
        elif args.command == "init-plugin":
            sys.exit(_cmd_init_plugin(args))
        elif args.command == "doctor":
            sys.exit(_cmd_doctor(args))
        elif args.command == "release-check":
            sys.exit(_cmd_release_check(args))
        else:
            parser.print_help()
            sys.exit(1)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        # Top-level exception handler: concise by default, full trace in --debug
        from forge.verification.exceptions import ForgeVerifyError
        if isinstance(exc, ForgeVerifyError):
            print(exc.format_for_cli(debug=_debug_mode), file=sys.stderr)
        else:
            category = type(exc).__name__
            print(f"[{category}] Unexpected error: {exc}", file=sys.stderr)
            if _debug_mode:
                traceback.print_exc(file=sys.stderr)
            else:
                print(
                    "  Run with --debug for full traceback and context.",
                    file=sys.stderr,
                )
        sys.exit(2)


if __name__ == "__main__":
    main()
