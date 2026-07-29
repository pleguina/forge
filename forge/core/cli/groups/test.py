"""forge test — wraps verification preparation and execution (release-plan
Phase 6, §6.4).

Three modes:

- ``forge test check-only`` — generate + validate stimulus + validate
  layout, no execution. A fixed-mode call into `forge verify prepare`'s
  own machinery (`_cmd_prepare`).
- ``forge test prepare`` — the same underlying call, with `forge verify
  prepare`'s full knobs exposed (`--dry-run`/`--strict`/`--port-map`) —
  an unchanged delegate, not a reimplementation.
- ``forge test run`` — the new work: resolves the set of selected events
  (reusing `_resolve_xml_run_selection`'s precedence logic) into
  individual event IDs, then *loops* the existing single-event
  `_build_runtime_context`/`_run_one_loaded_flow` machinery once per
  event — real per-event pass/fail, built by reusing the existing
  single-run path unchanged, not by inventing new backend/checker logic.
  `--all-dataset-parts` falls back to today's aggregate
  `_run_dataset_parts` behavior unchanged (no per-event breakdown for
  part-file-level datasets — see the Honest deferral list in
  docs/development/release-readiness.md's Phase 6 section).

Every mode adopts the shared `CommandEnvelope` (release-plan Phase 6,
§6.0/§6.7).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional


@contextlib.contextmanager
def _maybe_quiet_stdout(json_mode: bool):
    """Swallow a delegated call's narrative `print()`s when *json_mode* —
    `_cmd_prepare`/`_run_one_loaded_flow`/`_run_dataset_parts` always
    print human progress text unconditionally (no `emit_progress`-style
    flag, unlike `compute_gen_top_plan`), so without this, `--json` output
    would have narrative text before the JSON blob, breaking JSON parsing
    (the same "JSON mode's stdout is one clean document" convention every
    other envelope-adopting command already follows). stderr is left
    alone — it never carries structured envelope data.
    """
    if not json_mode:
        yield
        return
    with contextlib.redirect_stdout(io.StringIO()):
        yield


# ── check-only / prepare ────────────────────────────────────────────────────

def _run_prepare(args, *, mode: str) -> None:
    from forge.core.cli.envelope import CommandEnvelope, emit
    from forge.verify.__main__ import _cmd_prepare

    json_mode = getattr(args, "json", False)
    prepare_ns = argparse.Namespace(
        design_file=args.design_file,
        flow=getattr(args, "flow", None),
        consumer_root=getattr(args, "consumer_root", None),
        port_map=getattr(args, "port_map", None),
        dry_run=getattr(args, "dry_run", False),
        strict=getattr(args, "strict", False),
    )
    with _maybe_quiet_stdout(json_mode):
        rc = _cmd_prepare(prepare_ns)

    envelope = CommandEnvelope(
        status="pass" if rc == 0 else "fail",
        metrics={"mode": mode},
    )
    sys.exit(emit(envelope, json_mode=json_mode))


def cmd_check_only(args) -> None:
    """`forge test check-only` — generate + validate stimulus + validate
    layout, no execution (a fixed, simple call — see `forge test prepare`
    for the fully configurable delegate)."""
    args.dry_run = False
    args.strict = False
    args.port_map = None
    _run_prepare(args, mode="check-only")


def cmd_prepare(args) -> None:
    """`forge test prepare` — an unchanged delegate to `forge verify
    prepare`'s `_cmd_prepare`, exposed under the `forge test` golden path
    for discoverability."""
    _run_prepare(args, mode="prepare")


# ── run ──────────────────────────────────────────────────────────────────

def _parse_event_list(event_list: str) -> List[int]:
    """Parse ``"1,7,10-12"``-style event-list syntax (the same syntax
    `forge verify run --event-list`'s help text already documents) into a
    sorted, de-duplicated list of individual event IDs."""
    ids: List[int] = []
    for part in event_list.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            ids.extend(range(int(lo), int(hi) + 1))
        else:
            ids.append(int(part))
    return sorted(set(ids))


def _enumerate_xml_event_ids(xml_path: Path) -> List[int]:
    """Every ``<event id="...">`` in *xml_path*, tag-name-agnostic on the
    root element (passthrough_demo's root is `<passthrough_events>`,
    trigger_demo's is `<trigger_events>` — both real reference plugins
    use plain `<event id="N">` children, confirmed by inspection)."""
    tree = ET.parse(xml_path)
    ids = {
        int(elem.get("id")) for elem in tree.getroot().iter("event")
        if elem.get("id") is not None
    }
    return sorted(ids)


def _resolve_event_ids(selection) -> List[int]:
    """The set of individual event IDs `forge test run` will simulate
    once each — event_list/event_id take precedence (matching
    `_resolve_xml_run_selection`'s own CLI-args precedence), otherwise
    every event in the dataset XML is enumerated and run individually
    (unlike `--all-events`, which feeds every event through *one*
    simulation — see `verify/__main__.py`'s `p_run` registration; this
    command deliberately runs one simulation *per* event instead, since
    that's the only way to get a genuine per-event pass/fail)."""
    if selection.event_list:
        return _parse_event_list(selection.event_list)
    if selection.event_id is not None:
        return [selection.event_id]
    if not selection.dataset_xml:
        raise ValueError(
            "no dataset XML resolved — pass --xml-input or check the flow's dataset.xml"
        )
    return _enumerate_xml_event_ids(selection.dataset_xml)


def _tail_of_log(log_path: Any, *, lines: int = 20) -> Optional[str]:
    """Return the last *lines* of *log_path*, or None.

    Takes the real stage-correct log path directly (typically
    ``ExecutionResult.log_path``, slice 7.0) rather than always reading a
    hardcoded ``outputs["simulate_log"]`` — the direct fix for a compile
    failure's diagnostic message being read from the wrong (empty/stale)
    log file."""
    if log_path is None or not Path(log_path).exists():
        return None
    text = Path(log_path).read_text(errors="replace")
    tail = "\n".join(text.splitlines()[-lines:])
    return tail or None


def _build_event_artifacts(exec_result: Any, outputs: Dict[str, Any]) -> List[Any]:
    """Build this event's ``ArtifactRef`` list from the backend's declared
    outputs plus its waveform (if any), tagging the one log that matches
    ``exec_result.log_path`` with the real stage that produced it — the
    rest carry ``stage=None`` (honestly unknown at this granularity, never
    guessed)."""
    from forge.verify.results import ArtifactRef

    refs: List[ArtifactRef] = []
    result_log = getattr(exec_result, "log_path", None) if exec_result is not None else None
    result_stage = getattr(exec_result, "stage", None) if exec_result is not None else None

    for path in outputs.values():
        if path is None:
            continue
        stage = result_stage if (result_log is not None and Path(path) == result_log) else None
        refs.append(ArtifactRef(path=str(path), stage=stage, kind="log"))

    waveform = getattr(exec_result, "waveform_path", None) if exec_result is not None else None
    if waveform is not None:
        refs.append(ArtifactRef(path=str(waveform), stage=result_stage, kind="waveform"))

    return refs


def _parse_event_checks(outputs: Dict[str, Any]) -> List[Any]:
    """Scan the event's real ``simulate_log`` for ``FORGE_CHECK|`` records
    (slice 7.3) — always ``simulate_log`` specifically, since that's where
    the SV stimulus (and its checks) actually runs, never whichever log
    happened to be the one a compile/elaborate failure returned."""
    from forge.verify.results import parse_forge_check_lines

    log_path = outputs.get("simulate_log")
    if log_path is None or not Path(log_path).exists():
        return []
    return parse_forge_check_lines(Path(log_path).read_text(errors="replace"))


def _regenerate_stimulus_for_event(flow_name: str, event_id: int, flow_dir: Path) -> bool:
    """Call the plugin's own `tools/gen_stimulus.py::generate_for_flow`
    for *event_id*, regenerating `stimulus_current.svh` before that
    event's simulation.

    Without this, looping `_run_one_loaded_flow` per event would silently
    re-simulate whichever event's stimulus happened to already be on
    disk regardless of *event_id* — `forge verify prepare`'s own step 2
    only *checks* that a stimulus file exists, it never generates one
    (generation is a separate, plugin-owned step per the framework's
    documented contract; confirmed by reading `_cmd_prepare`'s own
    comment). `forge test run`'s whole value proposition is *real*
    per-event results, so this regeneration is not optional here — found
    and fixed during this slice's own real-xsim testing (an event-1 run
    was silently re-checking event-0's golden data before this fix).

    Returns True if regeneration happened, False if the plugin has no
    `gen_stimulus` module on `sys.path` (e.g. a csim-only flow) — in
    which case the caller proceeds with whatever stimulus is already
    there, same as `forge verify run` always has.
    """
    import importlib

    try:
        gen_stimulus_mod = importlib.import_module("gen_stimulus")
        generate_for_flow = gen_stimulus_mod.generate_for_flow
    except (ImportError, AttributeError):
        return False
    generate_for_flow(flow_name, event_id, flow_dir / "stimulus_current.svh")
    return True


def _load_flow_cfg(flow_path: Path, consumer_root):
    """Same plugin-specific-preferred / generic-fallback flow loading
    `_cmd_run` already does — mirrored here (not imported, since
    `_cmd_run` inlines this rather than factoring it out) so `forge test
    run` gets the identical `cfg` shape."""
    import importlib as _il

    try:
        _il.import_module("bootstrap")
        _flow_mod = _il.import_module("flow_config")
        cfg = _flow_mod.load_flow(flow_path, consumer_root=consumer_root)
        return cfg, _il
    except (ImportError, AttributeError):
        from forge.verify.flow_loader import load_generic_flow

        cfg = load_generic_flow(flow_path, consumer_root)
        return cfg, None


def cmd_run(args) -> None:
    from forge.core.cli.envelope import CommandEnvelope, emit
    from forge.verify.__main__ import (
        XmlRunSelection,
        _bootstrap,
        _build_runtime_context,
        _print_guided_error,
        _resolve_consumer_root,
        _resolve_xml_run_selection_from_values,
        _run_dataset_parts,
        _run_one_loaded_flow,
    )
    from forge.verify.backend_registry import get_adapter
    from forge.verify.design_contract import load_verify_design
    from forge.verify.layout import canonical_flow_dir

    json_mode = getattr(args, "json", False)
    strict = getattr(args, "strict", False)
    design_path = Path(args.design_file).resolve()

    if not design_path.exists():
        code = _guided_run_failure(f"design file not found: {design_path}", json_mode=json_mode)
        sys.exit(code)

    try:
        contract = load_verify_design(design_path)
    except Exception as exc:  # noqa: BLE001
        _print_guided_error(
            "test/contract", exc,
            action="Fix design.verification.yml before running forge test run again.",
        )
        sys.exit(_guided_run_failure(str(exc), json_mode=json_mode))
        return

    flow_name = getattr(args, "flow", None)
    flow_decl = next((f for f in contract.flows if f.name == flow_name), None)
    if flow_decl is None:
        available = ", ".join(f.name for f in contract.flows)
        sys.exit(_guided_run_failure(
            f"flow {flow_name!r} not found in {design_path} (available: {available})",
            json_mode=json_mode,
        ))

    verify_root = design_path.parent
    flow_dir = canonical_flow_dir(verify_root, flow_name)
    flow_path = flow_dir / "verify.flow.yml"
    if not flow_path.exists():
        sys.exit(_guided_run_failure(
            f"verify.flow.yml not found: {flow_path}",
            json_mode=json_mode,
            action=f"Run: forge test prepare {design_path} --flow {flow_name}",
        ))

    if not _bootstrap(getattr(args, "plugin", None), str(flow_path)):
        sys.exit(_guided_run_failure("plugin bootstrap failed", json_mode=json_mode))

    consumer_root = _resolve_consumer_root(getattr(args, "consumer_root", None))
    cfg, importlib_module = _load_flow_cfg(flow_path, consumer_root)

    selection = _resolve_xml_run_selection_from_values(
        cfg,
        event_id=getattr(args, "event_id", None),
        event_list=getattr(args, "event_list", None),
        all_events=getattr(args, "all_events", False),
        all_dataset_parts=getattr(args, "all_dataset_parts", False),
        dataset_parts_glob=getattr(args, "dataset_parts_glob", None),
        xml_input=getattr(args, "xml_input", None),
        probe_log=getattr(args, "probe_log", False),
    )

    try:
        adapter = get_adapter(cfg.backend)
    except (KeyError, LookupError) as exc:
        sys.exit(_guided_run_failure(str(exc), json_mode=json_mode))
        return

    junit_xml_path = getattr(args, "junit_xml", None)

    if selection.all_dataset_parts:
        # Honest deferral (release-plan Phase 6, §6.4): part-file-level
        # datasets aren't individually event-addressable, so this falls
        # back to today's aggregate-only reporting unchanged — no JUnit
        # XML (there is no per-event data to report) and no per-event
        # metrics/diagnostics breakdown.
        with _maybe_quiet_stdout(json_mode):
            rc = _run_dataset_parts(flow_path, cfg, selection, adapter, importlib_module, strict=strict)
        envelope = CommandEnvelope(
            status="pass" if rc == 0 else "fail",
            metrics={"mode": "all_dataset_parts"},
            next_actions=(
                [] if rc == 0 else
                ["Per-event breakdown is not available for --all-dataset-parts flows"]
            ),
        )
        sys.exit(emit(envelope, json_mode=json_mode, strict=strict))

    try:
        event_ids = _resolve_event_ids(selection)
    except ValueError as exc:
        sys.exit(_guided_run_failure(str(exc), json_mode=json_mode))
        return

    from forge.verify.results import RESULTS_SCHEMA, EventResult, FlowResult, diagnostic_for_event_failure

    # ── Phase 7 slice 7.5: stimulus_mode == "readmemh" ──────────────────────
    # Fixed-shape, non-recompiling stimulus: the plugin's gen_stimulus.py
    # writes the .mem file + a content-stable stimulus_current.svh ONCE
    # (below, before the loop) instead of _regenerate_stimulus_for_event's
    # default per-event regenerate-and-recompile; the backend itself skips
    # xvlog/xelab (or Verilator's build step) on every event after the
    # first, real event selection happening only via the +EVENT_INDEX
    # plusarg (never a recompile). Flows on the default svh_include
    # mechanism are completely unaffected — this whole block is a no-op
    # for them.
    stimulus_mode = getattr(cfg, "stimulus_mode", "svh_include")
    readmemh_mode = stimulus_mode == "readmemh"
    id_to_index: Dict[str, int] = {}
    if readmemh_mode:
        try:
            import importlib as _il
            _il.import_module("bootstrap")
            gen_stimulus_mod = _il.import_module("gen_stimulus")
            generate_readmemh_stimulus = gen_stimulus_mod.generate_readmemh_stimulus
        except (ImportError, AttributeError) as exc:
            sys.exit(_guided_run_failure(
                f"flow.stimulus_mode is 'readmemh' but the plugin's gen_stimulus.py "
                f"has no generate_readmemh_stimulus(): {exc}",
                json_mode=json_mode,
                action="Implement generate_readmemh_stimulus(flow_name, flow_dir) in "
                       "the plugin's tools/gen_stimulus.py (see plugins/passthrough_demo "
                       "for a real reference implementation).",
            ))
            return
        with _maybe_quiet_stdout(json_mode):
            index_to_id = generate_readmemh_stimulus(flow_name, flow_path.parent)
        id_to_index = {v: k for k, v in index_to_id.items()}

    readmemh_work_dir = flow_path.parent / "xsim_work" / "readmemh_shared"

    event_results: List[EventResult] = []
    for event_id in event_ids:
        part_selection = XmlRunSelection(
            event_id=event_id,
            event_list=None,
            all_events=False,
            all_dataset_parts=False,
            dataset_xml=selection.dataset_xml,
            dataset_parts_glob=None,
            probe_log=selection.probe_log,
        )
        event_id_str = str(event_id)
        event_index: "int | None" = None

        if readmemh_mode:
            if event_id_str not in id_to_index:
                sys.exit(_guided_run_failure(
                    f"event id {event_id_str!r} not found in the readmemh dataset "
                    f"(known: {sorted(id_to_index)})",
                    json_mode=json_mode,
                ))
                return
            event_index = id_to_index[event_id_str]
            work_dir = readmemh_work_dir
        else:
            with _maybe_quiet_stdout(json_mode):
                _regenerate_stimulus_for_event(flow_name, event_id, flow_path.parent)
            work_dir = flow_path.parent / "xsim_work" / "per_event" / str(event_id)

        ctx = _build_runtime_context(
            flow_path, cfg, part_selection, importlib_module, work_dir=work_dir,
        )
        if readmemh_mode:
            ctx.event_index = event_index

        capture: Dict[str, Any] = {}
        with _maybe_quiet_stdout(json_mode):
            rc = _run_one_loaded_flow(
                flow_path, cfg, part_selection, ctx, adapter, strict=strict, capture=capture,
            )
        success = rc == 0
        exec_result = capture.get("result")
        outputs = capture.get("outputs", {}) or {}
        checker_ok = capture.get("checker_ok")

        message = None if success else _tail_of_log(
            getattr(exec_result, "log_path", None) if exec_result is not None else None
        )

        ev = EventResult(
            event_id=event_id_str,
            # Real event_index for readmemh-mode flows (the dataset
            # position the +EVENT_INDEX plusarg actually selected);
            # honestly None for svh_include flows, which have no such
            # concept — never fabricated from loop iteration order.
            event_index=event_index,
            success=success,
            backend_id=adapter.backend_id,
            duration_s=getattr(exec_result, "duration_s", None) if exec_result is not None else None,
            artifacts=_build_event_artifacts(exec_result, outputs),
            checks=_parse_event_checks(outputs),
        )
        if not success:
            ev.diagnostics = [diagnostic_for_event_failure(
                event_id=event_id_str,
                flow_name=getattr(cfg, "flow_name", flow_name),
                stage=getattr(exec_result, "stage", None) if exec_result is not None else None,
                checker_ok=checker_ok,
                backend_success=getattr(exec_result, "success", False) if exec_result is not None else False,
                log_path=str(exec_result.log_path) if exec_result is not None and exec_result.log_path else None,
                log_tail=message,
            )]
        event_results.append(ev)

    flow_result = FlowResult(
        schema=RESULTS_SCHEMA,
        flow_name=getattr(cfg, "flow_name", flow_name),
        backend_id=adapter.backend_id,
        events=event_results,
    )

    events_passed = sum(1 for e in event_results if e.success)
    events_failed = len(event_results) - events_passed

    artifacts: List[str] = []
    if junit_xml_path:
        from forge.verify.junit_xml import write_junit_xml

        junit_path = Path(junit_xml_path).expanduser().resolve()
        write_junit_xml(
            junit_path,
            suite_name=flow_result.flow_name,
            results=[
                {
                    "name": f"event_{e.event_id}",
                    "success": e.success,
                    "message": (
                        None if e.success else
                        (e.diagnostics[0].context.get("log_tail") or e.diagnostics[0].message)
                        if e.diagnostics else None
                    ),
                    "time": e.duration_s,
                }
                for e in event_results
            ],
        )
        artifacts.append(str(junit_path))

    results_json_path = getattr(args, "results_json", None)
    if results_json_path:
        import json

        results_path = Path(results_json_path).expanduser().resolve()
        results_path.parent.mkdir(parents=True, exist_ok=True)
        results_path.write_text(json.dumps(flow_result.to_dict(), indent=2) + "\n")
        artifacts.append(str(results_path))

    artifacts.extend(a.path for a in flow_result.artifacts)

    diagnostics = [d.to_dict() for e in event_results for d in e.diagnostics]

    envelope = CommandEnvelope(
        status="fail" if events_failed else "pass",
        diagnostics=diagnostics,
        artifacts=artifacts,
        metrics={
            "events_run": len(event_results),
            "events_passed": events_passed,
            "events_failed": events_failed,
            "duration_s": flow_result.duration_s,
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

def _add_common_prepare_args(p) -> None:
    p.add_argument("design_file", help="Path to design.verification.yml")
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
        help="Wrap verification preparation and execution (check-only | prepare | run)",
    )
    test_sub = p_test.add_subparsers(dest="test_command", required=True, metavar="MODE")

    p_check = test_sub.add_parser(
        "check-only",
        help="Generate + validate stimulus + validate layout, no execution",
    )
    _add_common_prepare_args(p_check)
    p_check.set_defaults(func=cmd_check_only)

    p_prepare = test_sub.add_parser(
        "prepare",
        help="Generate + validate + check layout (full forge verify prepare delegate)",
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
    p_run.add_argument("design_file", help="Path to design.verification.yml")
    p_run.add_argument("--flow", required=True, help="Flow name to run (see design.verification.yml)")
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
        help="Comma-separated event IDs or ranges (e.g. 1,7,10-12), one simulation per event",
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
        "--strict", action="store_true", default=False,
        help="Also enforce canonical layout (same as forge verify run --strict)",
    )
    p_run.add_argument("--json", action="store_true", default=False, help="Machine-readable JSON output")
    p_run.set_defaults(func=cmd_run)
