"""forge test — prepares and runs verification flows.

``forge test`` on its own tests the ``forge.yml`` project around the working
directory: it builds the design, then prepares and runs every flow of the
``design.verification.yml`` that ``verification.config`` names, writing each
flow's results under ``.forge/reports/test/`` for ``forge report``. A flow
whose simulator is not installed is reported as skipped, never as passed.

Three modes give direct access to one verification file:

- ``forge test check-only`` — generate the flow files and check the stimulus
  and layout, without simulating.
- ``forge test prepare`` — the same, with ``--port-map``, ``--dry-run`` and
  ``--strict``.
- ``forge test run`` — one simulation per selected event, each with its own
  result; ``--all-dataset-parts`` runs each dataset part file instead and
  reports only the aggregate.

The work is done by :mod:`forge.verification.prepare` and
:mod:`forge.verification.run`; this module resolves arguments and reports.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


@contextlib.contextmanager
def _maybe_quiet_stdout(json_mode: bool):
    """Keep the library's progress text out of stdout in JSON mode, where
    stdout is one JSON document."""
    if not json_mode:
        yield
        return
    with contextlib.redirect_stdout(io.StringIO()):
        yield


# ── check-only / prepare ────────────────────────────────────────────────────

def _run_prepare(args, *, mode: str) -> None:
    from forge.core.cli.envelope import CommandEnvelope, emit
    from forge.verification.prepare import prepare_flows

    json_mode = getattr(args, "json", False)
    if args.design_file is None:
        project = _project_or_exit(json_mode)
        args.design_file = str(_verification_or_exit(project, json_mode))
        if args.consumer_root is None:
            args.consumer_root = str(project.root)
    with _maybe_quiet_stdout(json_mode):
        rc = prepare_flows(
            args.design_file,
            flow=getattr(args, "flow", None),
            consumer_root=getattr(args, "consumer_root", None),
            port_map=getattr(args, "port_map", None),
            dry_run=getattr(args, "dry_run", False),
            strict=getattr(args, "strict", False),
        )

    envelope = CommandEnvelope(
        status="pass" if rc == 0 else "fail",
        metrics={"mode": mode},
    )
    sys.exit(emit(envelope, json_mode=json_mode))


def cmd_check_only(args) -> None:
    """`forge test check-only`: prepare with the default options."""
    args.dry_run = False
    args.strict = False
    args.port_map = None
    _run_prepare(args, mode="check-only")


def cmd_prepare(args) -> None:
    _run_prepare(args, mode="prepare")


# ── the forge.yml project ────────────────────────────────────────────────

def _project_or_exit(json_mode: bool):
    from forge.project.resolve import resolve_project

    try:
        return resolve_project(Path.cwd())
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(_guided_run_failure(str(exc), json_mode=json_mode))


def _verification_or_exit(project, json_mode: bool) -> Path:
    design = project.verification
    if design is None:
        sys.exit(_guided_run_failure(
            "forge.yml names no verification config, so there is nothing to run",
            json_mode=json_mode,
            action="Set verification.config in forge.yml to your design.verification.yml",
        ))
    if not design.is_file():
        sys.exit(_guided_run_failure(
            f"verification.config in forge.yml names {design}, which does not exist",
            json_mode=json_mode,
            action="Correct verification.config in forge.yml",
        ))
    return design


def _flow_build_argv(project, verification: Path, flow) -> List[str]:
    """`forge build` of a flow's own design into its dut_rtl_source, with the
    project's build options and the design's own registry."""
    from forge.project.resolve import design_registry

    design = (verification.parent / flow.design).resolve()
    argv = [
        "build", str(design), "--mode", "verilog", "--top-name", flow.top_module,
        "--output", str(project.root / flow.dut_rtl_source / f"{flow.top_module}.v"),
        "--consumer-root", str(project.root),
    ]
    registry = design_registry(design)
    if registry is not None:
        argv += ["--contracts-from", str(registry)]
    for name, value in project.build_options().items():
        if name not in ("mode", "top_name"):
            argv += [f"--{name.replace('_', '-')}", value]
    return argv


def cmd_project_test(args) -> None:
    """`forge test` with no design file: build the project, then prepare and
    run each flow of its verification config, one line per step."""
    from forge.core.cli._shared import run_forge_command
    from forge.core.cli.envelope import CommandEnvelope, emit
    from forge.core.toolchain_versions import tool_present
    from forge.verification.backend_registry import get_adapter
    from forge.verification.design_contract import load_verify_design

    json_mode = getattr(args, "json", False)
    project = _project_or_exit(json_mode)
    design = _verification_or_exit(project, json_mode)
    try:
        contract = load_verify_design(design)
    except Exception as exc:  # noqa: BLE001
        sys.exit(_guided_run_failure(str(exc), json_mode=json_mode))

    flow_name = getattr(args, "flow", None)
    flows = [f for f in contract.flows if flow_name in (None, f.name)]
    if not flows:
        available = ", ".join(f.name for f in contract.flows) or "(none)"
        sys.exit(_guided_run_failure(
            f"flow {flow_name!r} not found in {design} (available: {available})",
            json_mode=json_mode,
        ))

    def say(line: str) -> None:
        if not json_mode:
            print(line)

    def failed_output(output: str) -> None:
        if output:
            print(output, end="" if output.endswith("\n") else "\n", file=sys.stderr)

    code, output = run_forge_command(["build"], quiet=True)
    if code != 0:
        failed_output(output)
        sys.exit(_guided_run_failure("the design did not build", json_mode=json_mode))
    say(f"  ✅ built {project.paths.relative(project.top_level())}")

    results_dir = project.paths.report_root / "test"
    results_dir.mkdir(parents=True, exist_ok=True)
    # The verification file names its plugin; bootstrapping it is what puts
    # the plugin's tools/ (gen_stimulus, runtime_context) on the import path.
    plugin = getattr(args, "plugin", None) or contract.plugin

    passed: List[str] = []
    failed: List[str] = []
    skipped: List[str] = []
    diagnostics: List[Dict[str, Any]] = []
    artifacts: List[str] = []
    for flow in flows:
        results_json = results_dir / f"{flow.name}.results.json"
        junit_xml = results_dir / f"{flow.name}.junit.xml"
        for stale in (results_json, junit_xml):
            stale.unlink(missing_ok=True)

        try:
            required = get_adapter(flow.backend).required_tools
        except (ValueError, ImportError, AttributeError, TypeError) as exc:
            failed.append(flow.name)
            diagnostics.append({"severity": "error", "message": f"flow {flow.name!r}: {exc}"})
            say(f"  ❌ {flow.name}: {exc}")
            continue
        missing = [tool for tool in required if not tool_present(tool)]
        if missing:
            skipped.append(flow.name)
            diagnostics.append({
                "severity": "warning",
                "message": (
                    f"flow {flow.name!r} not simulated — the {flow.backend} backend "
                    f"needs {', '.join(missing)} on PATH (run `forge doctor`)"
                ),
            })
            say(f"  ⏭️  {flow.name}: skipped, {', '.join(missing)} not on PATH")
            continue

        if flow.design:
            code, output = run_forge_command(
                _flow_build_argv(project, design, flow), quiet=True,
            )
            if code != 0:
                failed.append(flow.name)
                failed_output(output)
                diagnostics.append({
                    "severity": "error",
                    "message": f"flow {flow.name!r}: its design {flow.design} did not build",
                })
                say(f"  ❌ {flow.name}: {flow.design} did not build")
                continue
            say(f"  ✅ built {flow.dut_rtl_source}/{flow.top_module}.v from {flow.design}")

        ok, output = _prepare_and_run(
            design, flow.name, args,
            plugin=plugin, consumer_root=project.root,
            results_json=results_json, junit_xml=junit_xml,
        )
        if not ok:
            failed.append(flow.name)
            failed_output(output)
            diagnostics.append({"severity": "error", "message": f"flow {flow.name!r} failed"})
            say(f"  ❌ {flow.name}: failed")
            continue
        passed.append(flow.name)
        artifacts += [str(p) for p in (results_json, junit_xml) if p.exists()]
        say(f"  ✅ {flow.name}: passed")

    if failed:
        status = "fail"
    elif skipped:
        status = "warn"
    else:
        status = "pass"
    envelope = CommandEnvelope(
        status=status,
        diagnostics=diagnostics,
        artifacts=artifacts,
        metrics={"flows_passed": passed, "flows_failed": failed, "flows_skipped": skipped},
        next_actions=["Run `forge report` to collect these results"] if passed else [],
    )
    sys.exit(emit(envelope, json_mode=json_mode))


def _prepare_and_run(
    design: Path, flow_name: str, args, *, plugin, consumer_root: Path,
    results_json: Path, junit_xml: Path,
) -> "tuple[bool, str]":
    """Prepare and run one flow of the project, capturing its progress text
    for the caller to show if it fails."""
    from forge.core.cli._shared import report_command_error
    from forge.verification.prepare import prepare_flows
    from forge.verification.run import (
        RunRefused, open_flow, resolve_run_selection, run_flow_events, write_flow_reports,
    )

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        try:
            if prepare_flows(design, flow=flow_name, consumer_root=consumer_root) != 0:
                return False, buffer.getvalue()
            flow = open_flow(design, flow_name, plugin=plugin, consumer_root=consumer_root)
            selection = resolve_run_selection(
                flow.cfg,
                event_id=getattr(args, "event_id", None),
                event_list=getattr(args, "event_list", None),
                all_events=getattr(args, "all_events", False),
            )
            result = run_flow_events(flow, selection)
            write_flow_reports(
                result, flow.flow_dir, junit_xml=junit_xml, results_json=results_json,
            )
        except RunRefused as exc:
            print(f"error: {exc}")
            if exc.action:
                print(f"  → {exc.action}")
            return False, buffer.getvalue()
        except Exception as exc:  # noqa: BLE001
            report_command_error(exc)
            return False, buffer.getvalue()
    return all(e.success for e in result.events), buffer.getvalue()


# ── run ──────────────────────────────────────────────────────────────────

def cmd_run(args) -> None:
    from forge.core.cli.envelope import CommandEnvelope, emit
    from forge.verification.run import (
        RunRefused, open_flow, resolve_run_selection, run_dataset_parts, run_flow_events,
        write_flow_reports,
    )

    json_mode = getattr(args, "json", False)
    strict = getattr(args, "strict", False)
    if args.design_file is None:
        cmd_project_test(args)
        return
    if getattr(args, "flow", None) is None:
        sys.exit(_guided_run_failure(
            "--flow is required with a design file",
            json_mode=json_mode,
            action=f"Name one of the flows in {args.design_file}",
        ))

    try:
        flow = open_flow(
            Path(args.design_file), args.flow,
            plugin=getattr(args, "plugin", None),
            consumer_root=getattr(args, "consumer_root", None),
        )
        selection = resolve_run_selection(
            flow.cfg,
            event_id=getattr(args, "event_id", None),
            event_list=getattr(args, "event_list", None),
            all_events=getattr(args, "all_events", False),
            all_dataset_parts=getattr(args, "all_dataset_parts", False),
            dataset_parts_glob=getattr(args, "dataset_parts_glob", None),
            xml_input=getattr(args, "xml_input", None),
            probe_log=getattr(args, "probe_log", False),
        )
        if selection.all_dataset_parts:
            # Part files are not addressable event by event, so there is no
            # per-event breakdown and no JUnit report.
            with _maybe_quiet_stdout(json_mode):
                rc = run_dataset_parts(
                    flow.flow_path, flow.cfg, selection, flow.adapter, flow.importlib_module,
                    strict=strict,
                )
            envelope = CommandEnvelope(
                status="pass" if rc == 0 else "fail",
                metrics={"mode": "all_dataset_parts"},
                next_actions=(
                    [] if rc == 0 else
                    ["Per-event breakdown is not available for --all-dataset-parts flows"]
                ),
            )
            sys.exit(emit(envelope, json_mode=json_mode, strict=strict))
        with _maybe_quiet_stdout(json_mode):
            result = run_flow_events(flow, selection, strict=strict)
    except RunRefused as exc:
        sys.exit(_guided_run_failure(str(exc), json_mode=json_mode, action=exc.action))

    artifacts = write_flow_reports(
        result, flow.flow_dir,
        junit_xml=getattr(args, "junit_xml", None),
        results_json=getattr(args, "results_json", None),
        golden_comparison_json=getattr(args, "golden_comparison_json", None),
    )
    artifacts.extend(a.path for a in result.artifacts)

    events_passed = sum(1 for e in result.events if e.success)
    events_failed = len(result.events) - events_passed
    envelope = CommandEnvelope(
        status="fail" if events_failed else "pass",
        diagnostics=[d.to_dict() for e in result.events for d in e.diagnostics],
        artifacts=artifacts,
        metrics={
            "events_run": len(result.events),
            "events_passed": events_passed,
            "events_failed": events_failed,
            "duration_s": result.duration_s,
        },
    )
    sys.exit(emit(envelope, json_mode=json_mode, strict=strict))


def _guided_run_failure(msg: str, *, json_mode: bool, action: Optional[str] = None) -> int:
    from forge.core.cli.envelope import CommandEnvelope, emit

    diag: Dict[str, Any] = {"severity": "error", "message": msg}
    if action:
        diag["action"] = action
    envelope = CommandEnvelope(status="fail", diagnostics=[diag])
    return emit(envelope, json_mode=json_mode)


# ── registration ─────────────────────────────────────────────────────────

_DESIGN_FILE_HELP = (
    "Path to design.verification.yml (default: verification.config of the "
    "forge.yml project around the current directory)"
)


def _add_common_prepare_args(p) -> None:
    p.add_argument("design_file", nargs="?", default=None, help=_DESIGN_FILE_HELP)
    p.add_argument("--flow", help="Restrict to one flow by name (default: all flows)")
    p.add_argument(
        "--consumer-root",
        help="Repo root for path resolution (default: VERIFY_CONSUMER_ROOT env var)",
    )
    p.add_argument("--json", action="store_true", default=False, help="Machine-readable JSON output")


def register(sub) -> None:
    """Register the top-level ``forge test`` command group."""
    p_test = sub.add_parser(
        "test",
        help="Build and simulate the forge.yml project (or: check-only | prepare | run)",
    )
    p_test.add_argument("--flow", help="Run only this flow")
    p_test.add_argument("--event-id", dest="event_id", default=None, help="Run exactly this one event")
    p_test.add_argument(
        "--event-list", dest="event_list", default=None,
        help="Comma-separated event IDs or ranges (e.g. 1,7,10-12); a plugin may qualify one "
             "(e.g. event:processor, 4:2)",
    )
    p_test.add_argument(
        "--all-events", dest="all_events", action="store_true", default=False,
        help="Run every event in the dataset",
    )
    p_test.add_argument("--plugin", help="Plugin ID to bootstrap (when flow.plugin is absent from YAML)")
    p_test.add_argument("--json", action="store_true", default=False, help="Machine-readable JSON output")
    p_test.set_defaults(func=cmd_project_test)
    test_sub = p_test.add_subparsers(dest="test_command", metavar="MODE")

    p_check = test_sub.add_parser(
        "check-only",
        help="Generate + validate stimulus + validate layout, no execution",
    )
    _add_common_prepare_args(p_check)
    p_check.set_defaults(func=cmd_check_only)

    p_prepare = test_sub.add_parser(
        "prepare",
        help="Generate + validate + check layout (full forge dev verify prepare delegate)",
    )
    _add_common_prepare_args(p_prepare)
    p_prepare.add_argument("--port-map", help="Pre-generated port_map.yaml to reuse (xsim flows)")
    p_prepare.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Preview without writing generated artifacts",
    )
    p_prepare.add_argument(
        "--strict", action="store_true", default=False,
        help="Fail on non-canonical layout",
    )
    p_prepare.set_defaults(func=cmd_prepare)

    p_run = test_sub.add_parser(
        "run",
        help="Run one flow's selected event(s), one simulation per event, with real per-event results",
    )
    p_run.add_argument("design_file", nargs="?", default=None, help=_DESIGN_FILE_HELP)
    p_run.add_argument(
        "--flow",
        help="Flow name to run (required with a design file; default: every flow of the project)",
    )
    p_run.add_argument(
        "--consumer-root",
        help="Repo root for path resolution (default: VERIFY_CONSUMER_ROOT env var)",
    )
    p_run.add_argument("--plugin", help="Plugin ID to bootstrap (use when flow.plugin is absent from YAML)")
    p_run.add_argument("--event-id", dest="event_id", default=None, help="Run exactly this one event")
    p_run.add_argument(
        "--all-events", dest="all_events", action="store_true", default=False,
        help="Run every event in the dataset, one simulation per event (per-event results)",
    )
    p_run.add_argument(
        "--event-list", dest="event_list", default=None,
        help="Comma-separated event IDs or ranges (e.g. 1,7,10-12), one simulation per event; "
             "a plugin may qualify one (e.g. event:processor, 4:2)",
    )
    p_run.add_argument("--xml-input", dest="xml_input", default=None, help="Override dataset XML path")
    p_run.add_argument(
        "--all-dataset-parts", dest="all_dataset_parts", action="store_true", default=False,
        help="Run every XML part declared by dataset.parts_glob/parts (aggregate only, no per-event breakdown)",
    )
    p_run.add_argument("--dataset-parts-glob", dest="dataset_parts_glob", default=None)
    p_run.add_argument(
        "--probe-log", dest="probe_log", action="store_true", default=False,
        help="Enable Tier 2 probe CSV capture",
    )
    p_run.add_argument("--junit-xml", dest="junit_xml", default=None, help="Write a JUnit XML report to this path")
    p_run.add_argument(
        "--results-json", dest="results_json", default=None,
        help="Write the full versioned FlowResult (schema, per-event backend id, "
             "duration, artifacts, diagnostics) as JSON to this path",
    )
    p_run.add_argument(
        "--golden-comparison-json", dest="golden_comparison_json", default=None,
        help="Write a forge.golden_comparison_result.v1 artifact to this path "
             "(only when the plugin's gen_stimulus.py wrote a "
             "golden_model_provenance.json sidecar next to this flow)",
    )
    p_run.add_argument(
        "--strict", action="store_true", default=False,
        help="Also enforce canonical layout (same as forge dev verify run --strict)",
    )
    p_run.add_argument("--json", action="store_true", default=False, help="Machine-readable JSON output")
    p_run.set_defaults(func=cmd_run)
