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
import json
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
    from forge.project.config import ForgeConfig
    from forge.project.paths import find_project_root
    from forge.project.resolve import resolve_project

    try:
        root = find_project_root(Path.cwd())
        if root is not None:
            config = ForgeConfig.load(root)
            if (config.verification_spec or {}).get("target") == "existing":
                # The design is simulated as written: nothing to derive or generate.
                return existing_project(config)
        return resolve_project(Path.cwd())
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(_guided_run_failure(str(exc), json_mode=json_mode))


def existing_project(config):
    """The parts of a project ``forge test`` and ``forge report`` use when the
    project's own top is what gets simulated."""
    from types import SimpleNamespace

    paths = config.paths()
    return SimpleNamespace(root=config.root, config=config, paths=paths, verification=None,
                           top_level=lambda *a, **k: paths.top_level(*a, **k))


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


_STATUS_ICON = {"VERIFIED": "✅", "FAILED": "❌", "PARTIAL": "⚠️ ", "NOT_VERIFIED_NO_ORACLE": "⚠️ ",
                "BLOCKED_MISSING_TOOL": "⏭️ ", "SKIPPED": "⏭️ "}


def _targets(args) -> Optional[List[str]]:
    names = [n.strip() for raw in getattr(args, "target", None) or [] for n in raw.split(",") if n.strip()]
    return names or None


def _events(args) -> Optional[List[str]]:
    if getattr(args, "event_id", None):
        return [str(args.event_id)]
    if getattr(args, "event_list", None):
        return [e.strip() for e in args.event_list.split(",") if e.strip()]
    return None


def _build_subsystems(project, spec, select, json_mode: bool) -> None:
    """Generate the top level of each subsystem target from the design, and
    refuse one whose connections are not exactly the design's."""
    import yaml

    from forge.core.cli._shared import run_forge_command
    from forge.project.resolve import design_registry
    from forge.project.subsystem import subsystem_design, subsystem_difference, subsystems
    from forge.verification import auto

    for name, instances in subsystems(project, spec, select).items():
        gen = auto.subsystem_dir(project, name)
        gen.mkdir(parents=True, exist_ok=True)
        design = gen.parent / "design.yml"
        design.write_text(yaml.safe_dump(subsystem_design(project, instances), sort_keys=False))
        argv = ["build", str(design), "--mode", "verilog", "--top-name", f"{name.replace('.', '_').replace('-', '_')}_top",
                "--output", str(gen / f"{name}_top.v"), "--consumer-root", str(project.root)]
        registry = design_registry(design)
        if registry is not None:
            argv += ["--contracts-from", str(registry)]
        for option, value in project.build_options().items():
            if option not in ("mode", "top_name"):
                argv += [f"--{option.replace('_', '-')}", value]
        code, output = run_forge_command(argv, quiet=True)
        if code != 0:
            if output:
                print(output, file=sys.stderr)
            sys.exit(_guided_run_failure(f"subsystem {name} did not build", json_mode=json_mode))
        difference = subsystem_difference(project, name, instances)
        if difference:
            sys.exit(_guided_run_failure(
                f"subsystem {name} is not wired as the design is: " + "; ".join(difference),
                json_mode=json_mode,
                action=f"Declare the connections of {', '.join(instances)} under connections: in the design"))


def cmd_auto_test(args, project) -> None:
    """`forge test` without a verification config: plan from the design and
    forge.yml's verification keys, simulate every level, record evidence."""
    from forge.core.cli._shared import run_forge_command
    from forge.core.cli.envelope import CommandEnvelope, emit
    from forge.verification import auto
    from forge.verification.junit_xml import write_junit_xml

    json_mode = getattr(args, "json", False)
    spec = dict(project.config.verification_spec)
    select, events = _targets(args), _events(args)
    if getattr(args, "level", None) and spec.get("targets"):
        sys.exit(_guided_run_failure("--level selects the design or modules; this project names its targets",
                                     json_mode=json_mode, action="Select targets with --target"))
    for key in ("backend", "level"):
        value = getattr(args, key, None)
        if value:
            spec[f"{key}s"] = [value]
    if getattr(args, "record_modules", False):
        spec["record_modules"] = True
    if getattr(args, "compare_mode", None):
        spec["compare"] = {**(spec.get("compare") or {}), "mode": args.compare_mode}
    if getattr(args, "latency", None):
        lo, _, hi = args.latency.partition(":")
        spec["compare"] = {**(spec.get("compare") or {}),
                           "latency": {"min": int(lo), "max": int(hi or lo)}}

    def ref(value, fmt):
        if not value:
            return None
        return {"path": str(Path(value).resolve()), **({"format": fmt} if fmt else {})}

    try:
        if spec.get("target") != "existing":
            code, output = run_forge_command(["build"], quiet=True)
            if code != 0:
                if output:
                    print(output, file=sys.stderr)
                sys.exit(_guided_run_failure("the design did not build", json_mode=json_mode))
            if not getattr(args, "plan", False):
                _build_subsystems(project, spec, select, json_mode)
        plan = auto.plan(project, spec=spec, input_override=ref(args.input, args.input_format),
                         expected_override=ref(args.expected, args.expected_format), select=select, events=events)
    except (OSError, ValueError, KeyError) as exc:
        sys.exit(_guided_run_failure(str(exc), json_mode=json_mode,
                                     action="Declare the interfaces under verification.protocols in forge.yml"
                                     if isinstance(exc, KeyError) else None))
    if getattr(args, "plan", False):
        if json_mode:
            print(json.dumps(plan.to_dict(), indent=2))
        else:
            import yaml
            print(yaml.safe_dump(plan.to_dict(), sort_keys=False))
        sys.exit(0)

    from datetime import datetime, timezone

    from forge.project.verification_state import evidence_dir, runs_dir

    out_dir = evidence_dir(project.paths.report_root)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = runs_dir(project.paths.report_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        evidence = auto.run_plan(plan, project.paths.state_root / "verify", seed=args.seed,
                                 stall=args.stall, backpressure=args.backpressure)
    except (OSError, ValueError, KeyError) as exc:
        sys.exit(_guided_run_failure(str(exc), json_mode=json_mode))
    # A run of some events is kept with its run; the latest results stay those of every event.
    focused = bool(events)
    if not focused:
        ran = {e.level.replace(":", "_") for e in evidence}
        for old in out_dir.glob("*.evidence.json"):
            if select is None or old.name[: -len(".evidence.json")].rpartition(".")[0] in ran:
                old.unlink()
    artifacts, diagnostics, results = [], [], []
    for e in evidence:
        path = out_dir / f"{e.level.replace(':', '_')}.{e.backend}.evidence.json"
        if not focused:
            e.write(path)
            artifacts.append(str(path))
        e.write(run_dir / path.name)
        reason = e.doc.get("reason", "")
        detail = reason or ", ".join(
            f"{r['interface']} {r['matched']}/{r['expected']}" if r["expected"] else f"{r['interface']} {r['observed']} words"
            for r in e.doc.get("interfaces", []))
        required = e.doc.get("required", True)
        if not json_mode:
            print(f"  {_STATUS_ICON[e.status]} {e.level} [{e.backend}]: {e.status}" + (f" ({detail})" if detail else "")
                  + ("" if required else ", not required"))
        if e.status == "FAILED":
            diagnostics.append({"severity": "error" if required else "warning",
                                "message": f"{e.level} [{e.backend}]: {detail}"})
        elif e.status != "VERIFIED" and required:
            diagnostics.append({"severity": "warning", "message": f"{e.level} [{e.backend}]: {e.status}"
                                + (f", {reason}" if reason else "")})
        results.append({"name": f"{e.level}[{e.backend}]", "success": e.status != "FAILED" or not required,
                        "skipped": e.status not in ("VERIFIED", "FAILED"), "message": detail})
    junit = out_dir / "junit.xml" if not focused else run_dir / "junit.xml"
    write_junit_xml(junit, suite_name=project.config.project, results=results)
    artifacts.append(str(junit))
    overall = auto.summary(evidence)
    targets = _target_summary(evidence)
    (run_dir / "run.json").write_text(json.dumps({
        "run": run_id, "verification": overall,
        "levels": {f"{e.level}[{e.backend}]": e.status for e in evidence},
        "targets": targets,
        "evidence": {f"{e.level}[{e.backend}]": e.doc["evidence_id"] for e in evidence},
        **({"events": events} if events else {}),
    }, indent=2) + "\n")
    artifacts.append(str(run_dir))
    if not json_mode:
        print(_target_sentence(targets))
    status = {"VERIFIED": "pass", "FAILED": "fail"}.get(overall, "warn")
    if status == "warn" and getattr(args, "require_verified", False):
        status = "fail"
        diagnostics.append({"severity": "error",
                            "message": f"--require-verified: verification is {overall}, not VERIFIED"})
    metrics: Dict[str, Any] = {"verification": overall, "targets": targets,
               "levels": {f"{e.level}[{e.backend}]": e.status for e in evidence}}
    if focused:
        metrics["events"] = events
    envelope = CommandEnvelope(
        status=status,
        diagnostics=diagnostics,
        artifacts=artifacts,
        metrics=metrics,
        next_actions=_auto_next_actions([e for e in evidence if e.doc.get("required", True)]),
    )
    sys.exit(emit(envelope, json_mode=json_mode))


_REFERENCE_WORDS = {"functional": "reference comparison", "derived": "replay of recorded traffic",
                    "none": "no reference supplied"}


def _target_summary(evidence) -> dict:
    """target -> {scope, required, reference, status}, its status combined over
    the simulators it ran on."""
    from forge.verification import scoreboard as sb

    out: dict = {}
    for e in evidence:
        entry = out.setdefault(e.level, {"scope": e.doc.get("scope", e.level),
                                         "required": e.doc.get("required", True),
                                         "reference": e.doc.get("reference", "none"), "statuses": []})
        entry["statuses"].append(e.status)
    for entry in out.values():
        statuses = entry.pop("statuses")
        ran = [s for s in statuses if s != "SKIPPED"]
        entry["status"] = sb.combine(ran) if ran else "SKIPPED"
    return out


def _target_sentence(targets: dict) -> str:
    """``Required: payload VERIFIED (reference comparison). Not required:
    module:x (no reference supplied).``"""
    def say(name, t):
        return f"{name} {t['status']} ({_REFERENCE_WORDS.get(t['reference'], t['reference'])})"

    required = [say(n, t) for n, t in targets.items() if t["required"]]
    optional = [say(n, t) for n, t in targets.items() if not t["required"]]
    return "\n" + "; ".join(filter(None, [
        "Required: " + ", ".join(required) if required else "",
        "not required: " + ", ".join(optional) if optional else ""])) + "."


def _auto_next_actions(evidence) -> List[str]:
    """What would turn each unverified level into a verified one, from its status."""
    from forge.verification import auto

    actions: List[str] = []
    blocked = sorted({t for e in evidence if e.status == "BLOCKED_MISSING_TOOL"
                      for t in auto.missing_tools(e.backend)})
    if blocked:
        ran = sorted({e.backend for e in evidence if e.status != "BLOCKED_MISSING_TOOL"
                      and e.status != "SKIPPED"})
        actions.append(f"Put {', '.join(blocked)} on PATH (`forge doctor` shows what is found)"
                       + (f", or run only {' / '.join(ran)} with --backend" if ran else ""))
    failed = [e for e in evidence if e.status == "FAILED"]
    for e in failed:
        actions.append(f"Fix {e.level} [{e.backend}]: " + (e.doc.get("reason") or "see its evidence file"))
    skipped = [e for e in evidence if e.status == "SKIPPED" and e.doc.get("reason")]
    for e in skipped:
        actions.append(f"{e.level} [{e.backend}] was not simulated: {e.doc['reason']}")
    no_oracle = sorted({e.level for e in evidence if e.status == "NOT_VERIFIED_NO_ORACLE"
                        or (e.status == "BLOCKED_MISSING_TOOL" and not e.doc.get("expected"))})
    if no_oracle:
        design = [lv for lv in no_oracle if lv == "design" or lv.startswith("existing:")]
        modules = [lv.split(":", 1)[1] for lv in no_oracle if lv.startswith("module:")]
        named = [lv for lv in no_oracle if lv not in design and not lv.startswith("module:")]
        where = (["verification.expected (or --expected)"] if design else []) + (
            [f"verification.modules.{m}.expected" for m in modules]) + (
            [f"verification.targets.{t}.expected" for t in named])
        actions.append(f"Give an expected output: {', '.join(where)} in forge.yml")
    if any(e.status == "PARTIAL" for e in evidence):
        actions.append("Some interfaces have no expected output; add them to the expected file")
    return actions


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
    if project.verification is None or getattr(args, "input", None) or getattr(args, "plan", False):
        cmd_auto_test(args, project)
        return
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
        status = "fail" if getattr(args, "require_verified", False) else "warn"
        if status == "fail":
            diagnostics.append({"severity": "error",
                                "message": f"--require-verified: {len(skipped)} flow(s) not simulated"})
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
    auto = p_test.add_argument_group(
        "without a verification config",
        "The design's interfaces are found from its ports and driven by a generated testbench",
    )
    auto.add_argument("--input", default=None, help="Input transactions (JSON, EMP or APx pattern file)")
    auto.add_argument("--input-format", dest="input_format", default=None,
                      help="A registered dataset format, e.g. json, empv2, apx, apx-2021 "
                           "(default: from the file)")
    auto.add_argument("--expected", default=None, help="Expected output transactions")
    auto.add_argument("--expected-format", dest="expected_format", default=None)
    auto.add_argument("--compare-mode", dest="compare_mode", default=None,
                      choices=("transaction", "exact_cycle", "fixed_latency", "latency_window"))
    auto.add_argument("--latency", default=None, help="Cycles, or MIN:MAX, for the latency modes")
    auto.add_argument("--backend", default=None, choices=("xsim", "verilator"), help="Only this simulator")
    auto.add_argument("--level", default=None, choices=("design", "modules"), help="Only this level")
    auto.add_argument("--target", action="append", default=None,
                      help="Only this target (repeatable, or comma-separated): a name under verification.targets, "
                           "or design / module:<name>. --event-id/--event-list replay some events of its data")
    auto.add_argument("--seed", type=int, default=1, help="Seed of the stall and backpressure patterns")
    auto.add_argument("--stall", type=int, default=0, help="Percent of cycles a ready_valid input withholds a word")
    auto.add_argument("--backpressure", type=int, default=0,
                      help="Percent of cycles a ready_valid output's ready is low")
    auto.add_argument("--record-modules", dest="record_modules", action="store_true", default=False,
                      help="Record each module instance's inputs and outputs during the design-level run, "
                           "as data a module level can be checked against (derived, not independent)")
    auto.add_argument("--plan", action="store_true", default=False,
                      help="Print what would be verified, and what was inferred, without simulating")
    p_test.add_argument(
        "--require-verified", dest="require_verified", action="store_true", default=False,
        help="Exit 1 unless everything was verified: a missing simulator, a missing expected "
             "output or a skipped flow fails instead of warning (for CI)",
    )
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
