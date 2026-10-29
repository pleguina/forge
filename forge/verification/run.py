"""Running a verification flow: bootstrap its plugin, load its
``verify.flow.yml``, then preflight, simulate and check.

``forge dev verify run`` runs one selection through a single simulation;
``forge test`` runs one simulation per event with :func:`run_flow_events`
and reports each event's result.
"""
from __future__ import annotations

import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


def _debug_enabled() -> bool:
    return "--debug" in sys.argv or os.environ.get("FORGE_VERIFY_DEBUG", "0") == "1"


def print_guided_error(
    scope: str,
    exc: Exception,
    *,
    action: str,
    extra: list[str] | None = None,
) -> int:
    print(f"ERROR ({scope}): {exc}", file=sys.stderr)
    print(f"  → {action}", file=sys.stderr)
    for line in extra or []:
        print(f"  {line}", file=sys.stderr)
    if _debug_enabled() and exc.__traceback__ is not None:
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
    return 1


def bootstrap_for_flow(plugin_id: str | None, flow_file: str) -> bool:
    """Bootstrap a plugin by explicit id or by reading flow.plugin.
    Returns True on success, False on error (already printed to stderr).
    """
    # Derive the probable tools/ directory from the flow file path so that
    # import_module("bootstrap") can find bootstrap.py without the caller
    # needing to set sys.path manually.
    # Layout: <verify_root>/<flow_name>/verify.flow.yml → tools/ at
    #         <verify_root>/tools/
    _tools_candidate = str(Path(flow_file).resolve().parent.parent / "tools")
    if _tools_candidate not in sys.path:
        sys.path.insert(0, _tools_candidate)

    if plugin_id:
        from forge.verification.plugin_registry import bootstrap_plugin as _bp
        try:
            _bp(plugin_id)
        except LookupError:
            import importlib as _il
            try:
                _il.import_module("bootstrap")
            except ImportError:
                pass
            try:
                _bp(plugin_id)
            except LookupError as exc:
                print_guided_error(
                    "bootstrap",
                    exc,
                    action=(
                        "Ensure the plugin is registered and that tools/bootstrap.py is importable, "
                        "or pass --plugin with the correct plugin id."
                    ),
                )
                return False
    else:
        from forge.verification.plugin_registry import bootstrap_from_flow as _bff
        try:
            _bff(Path(flow_file))
        except (FileNotFoundError, LookupError) as exc:
            print_guided_error(
                "bootstrap",
                exc,
                action=(
                    "Check that verify.flow.yml exists and declares flow.plugin, or provide --plugin explicitly."
                ),
            )
            return False
    return True


def resolve_consumer_root(arg: "str | Path | None") -> Path | None:
    s = arg or os.environ.get("VERIFY_CONSUMER_ROOT")
    return Path(s).resolve() if s else None


@dataclass(frozen=True)
class XmlRunSelection:
    """Framework-standard run selection for XML-backed verification flows.

    v1.0 of the framework verification contract standardizes XML-backed datasets
    and event-oriented run selection across framework launchers and backends.
    Plugins may still extend RuntimeContext, but these selectors are now an
    explicit framework surface rather than an implicit plugin-local convention.
    """

    event_id: int | None
    event_list: str | None
    all_events: bool
    all_dataset_parts: bool
    dataset_xml: Path | None
    dataset_parts_glob: str | None
    probe_log: bool


def resolve_run_selection(
    cfg,
    *,
    event_id: "int | str | None" = None,
    event_list: "str | None" = None,
    all_events: bool = False,
    all_dataset_parts: bool = False,
    dataset_parts_glob: "str | None" = None,
    xml_input: "str | None" = None,
    probe_log: bool = False,
) -> XmlRunSelection:
    """Resolve framework-standard XML dataset run selectors.

    Precedence: value given > environment variables > flow defaults.
    """
    event_id_raw = (
        event_id
        or os.environ.get("EVENT_ID")
        or getattr(cfg, "dataset_default_event_id", None)
    )
    event_list_raw = event_list or os.environ.get("EVENT_LIST")
    _all_events = bool(all_events or (os.environ.get("ALL_EVENTS", "0") == "1"))
    _all_dataset_parts = bool(
        all_dataset_parts or (os.environ.get("ALL_DATASET_PARTS", "0") == "1")
    )
    _dataset_parts_glob = dataset_parts_glob or os.environ.get("DATASET_PARTS_GLOB")
    xml_input_raw = (
        xml_input
        or os.environ.get("XML_INPUT")
        or getattr(cfg, "dataset_xml", None)
    )
    _probe_log = probe_log or (os.environ.get("PROBE_LOG", "0") == "1")
    return XmlRunSelection(
        event_id=int(event_id_raw) if event_id_raw is not None else None,
        event_list=str(event_list_raw).strip() if event_list_raw else None,
        all_events=_all_events,
        all_dataset_parts=_all_dataset_parts,
        dataset_xml=Path(xml_input_raw) if xml_input_raw else None,
        dataset_parts_glob=str(_dataset_parts_glob).strip() if _dataset_parts_glob else None,
        probe_log=_probe_log,
    )


def load_flow_config(flow_path: Path, consumer_root: "Path | None"):
    """Load *flow_path* with the bootstrapped plugin's own ``flow_config``
    loader when it has one, else the generic loader.

    Returns ``(cfg, importlib)``; the module is what
    :func:`build_runtime_context` looks up the plugin's ``runtime_context``
    through.
    """
    import importlib

    try:
        importlib.import_module("bootstrap")
        flow_mod = importlib.import_module("flow_config")
        return flow_mod.load_flow(flow_path, consumer_root=consumer_root), importlib
    except (ImportError, AttributeError):
        from forge.verification.flow_loader import load_generic_flow

        return load_generic_flow(flow_path, consumer_root), importlib


def build_runtime_context(
    flow_path: Path,
    cfg,
    selection: XmlRunSelection,
    importlib_module,
    work_dir: Path | None = None,
) -> Any:
    """Build framework-generic or plugin-specific runtime context.

    Framework base context carries only generic execution state. Plugin-owned
    runtime_context.py modules may extend it with plugin-specific fields.
    """
    extra_overrides: Dict[str, Any] = {}
    if selection.event_list:
        extra_overrides["event_list"] = selection.event_list
    if selection.all_events:
        extra_overrides["all_events"] = True

    work_dir = work_dir or (flow_path.parent / "xsim_work")

    plugin_ctx_type = None
    if importlib_module is not None:
        try:
            plugin_ctx_type = importlib_module.import_module("runtime_context").RuntimeContext
        except (ImportError, AttributeError):
            plugin_ctx_type = None

    if plugin_ctx_type is not None:
        return plugin_ctx_type(
            work_dir=work_dir,
            probe_log=selection.probe_log,
            event_id=selection.event_id or 0,
            xml_input=selection.dataset_xml,
            extra_overrides=extra_overrides,
        )

    from forge.verification.runtime_context import RuntimeContext as FrameworkRuntimeContext

    return FrameworkRuntimeContext(
        work_dir=work_dir,
        probe_log=selection.probe_log,
        extra_overrides=extra_overrides,
    )


def run_loaded_flow(
    flow_path: Path, cfg, selection: XmlRunSelection, ctx, adapter, strict: bool = False,
    capture: "dict | None" = None,
) -> int:
    """Run one resolved XML selection through preflight, backend, and checker.

    ``capture``, when given a dict, is populated with the richer data this
    function already computes internally but otherwise only returns as a
    bare ``int`` — ``capture["result"]`` (an ``ExecutionResult``, including
    a synthetic one for pre-execution failures so callers always get a
    real object), ``capture["checker_ok"]``, ``capture["outputs"]``.
    Existing callers that don't pass ``capture`` see no behavior change.
    """
    from forge.verification.backend_base import ExecutionResult
    from forge.verification.execution_stage import ExecutionStage

    # ── 5. Preflight ─────────────────────────────────────────────────────────
    from forge.verification.preflight import run_preflight
    pre = run_preflight(cfg, xml_input=selection.dataset_xml)
    pre.print_summary()
    if not pre.ok:
        if capture is not None:
            capture["result"] = ExecutionResult(
                success=False, stage=ExecutionStage.PREFLIGHT, backend_id=adapter.backend_id,
            )
        return 1

    # ── Strict-mode layout check ──────────────────────────────────────────────
    if strict:
        from forge.verification.layout import validate_layout
        _verify_root = flow_path.parent.parent
        _layout_errors = validate_layout(_verify_root)
        if _layout_errors:
            print("[strict] Layout violations:", file=sys.stderr)
            for le in _layout_errors:
                print(f"  {le}", file=sys.stderr)
            if capture is not None:
                capture["result"] = ExecutionResult(
                    success=False, stage=ExecutionStage.PREFLIGHT, backend_id=adapter.backend_id,
                )
            return 1

    # ── 7. Validate backend requirements ─────────────────────────────────────
    errors = adapter.validate_backend_requirements(cfg)
    if errors:
        for e in errors:
            print(f"ERROR (backend validation): {e}", file=sys.stderr)
        if capture is not None:
            capture["result"] = ExecutionResult(
                success=False, stage=ExecutionStage.PREFLIGHT, backend_id=adapter.backend_id,
            )
        return 1

    # ── 8. Prepare inputs ────────────────────────────────────────────────────
    try:
        adapter.prepare_backend_inputs(cfg, ctx)
    except Exception as exc:  # noqa: BLE001
        if capture is not None:
            capture["result"] = ExecutionResult(
                success=False, stage=ExecutionStage.PREFLIGHT, backend_id=adapter.backend_id,
            )
        return print_guided_error(
            "prepare",
            exc,
            action="Re-run forge dev verify generate and ensure required DUT, testbench, and stimulus artifacts exist.",
        )

    from forge.verification.preflight import validate_stimulus_handoff
    stim_errors = validate_stimulus_handoff(cfg)
    if stim_errors:
        for e in stim_errors:
            print(f"ERROR (stimulus): {e}", file=sys.stderr)
        if capture is not None:
            capture["result"] = ExecutionResult(
                success=False, stage=ExecutionStage.PREFLIGHT, backend_id=adapter.backend_id,
            )
        return 1

    # ── 9. Run backend ───────────────────────────────────────────────────────
    try:
        result = adapter.run_backend(cfg, ctx)
    except Exception as exc:  # noqa: BLE001
        if capture is not None:
            # Stage genuinely unknown — the backend threw before returning a
            # result, so no real ExecutionResult exists to report one from.
            capture["result"] = ExecutionResult(
                success=False, stage=None, backend_id=adapter.backend_id,
            )
        return print_guided_error(
            "run",
            exc,
            action="Check simulator logs, tool setup, and generated artifacts, then re-run forge dev verify run.",
        )

    if capture is not None:
        capture["result"] = result

    if not result.success:
        print(f"FAIL  (backend exited {result.exit_code})", file=sys.stderr)

    # ── 10. Checker lifecycle ────────────────────────────────────────────────
    # Must not gate on `cfg.has_checker` (a declared `checker:` YAML
    # block): the default `checker_mode: log_scan` needs no `checker:`
    # section at all, so gating on it would skip log scanning for
    # `$fatal`/`FAIL:` markers on every flow that relies on the default —
    # every reference flow in this repo. A simulator process commonly
    # exits 0 even after `$fatal` fires inside the simulated design (the
    # whole reason a log-scan checker mode exists), so skipping the scan
    # would report PASS for a failed check. The checker must run whenever
    # the adapter defines one and the backend itself succeeded.
    checker_ok: bool | None = None
    if result.success:
        run_checker = getattr(adapter, "run_checker", None)
        if callable(run_checker):
            try:
                checker_ok = run_checker(cfg, ctx, result)
            except Exception as exc:  # noqa: BLE001
                print_guided_error(
                    "checker",
                    exc,
                    action="Inspect the checker configuration and observed log path in verify.flow.yml, then re-run.",
                )
                checker_ok = False
        else:
            # No plugin checker hook — treat as not-applicable.
            checker_ok = None

    if capture is not None:
        capture["checker_ok"] = checker_ok

    # ── 11. Artifact summary ─────────────────────────────────────────────────
    outputs = adapter.describe_backend_outputs(cfg, ctx)
    if capture is not None:
        capture["outputs"] = outputs
    print()
    print(f"[run] flow:    {getattr(cfg, 'flow_name', flow_path.name)}")
    print(f"[run] backend: {adapter.backend_id}")
    if selection.event_list:
        print(f"[run] events:  {selection.event_list}")
    elif selection.all_events:
        print("[run] events:  all")
    elif selection.event_id is not None:
        print(f"[run] event:   {selection.event_id}")
    print(f"[run] probe:   {getattr(ctx, 'probe_log', False)}")
    print()
    for name, path in outputs.items():
        if path is not None:
            print(f"  {name:<22} {path}")
    if checker_ok is True:
        print("\nScoreboard check: PASS")
    elif checker_ok is False:
        print("\nScoreboard check: FAIL", file=sys.stderr)

    if checker_ok is False:
        return 1
    return 0 if result.success else 1


def _resolve_dataset_part_paths(cfg, selection: XmlRunSelection) -> list[Path]:
    if selection.dataset_parts_glob:
        pattern = Path(selection.dataset_parts_glob)
        if pattern.is_absolute():
            return sorted(path.resolve() for path in pattern.parent.glob(pattern.name))
        root = Path(getattr(cfg, "consumer_root", Path.cwd()))
        return sorted(path.resolve() for path in root.glob(str(pattern)))
    return [Path(path).resolve() for path in (getattr(cfg, "dataset_parts", ()) or ())]


def run_dataset_parts(flow_path: Path, cfg, selection: XmlRunSelection, adapter, importlib_module, strict: bool = False) -> int:
    part_paths = _resolve_dataset_part_paths(cfg, selection)
    if not part_paths:
        print(
            "ERROR: no dataset parts resolved. Add dataset.parts_glob/parts to verify.flow.yml "
            "or pass --dataset-parts-glob.",
            file=sys.stderr,
        )
        return 1

    print(f"[dataset-parts] Running {len(part_paths)} XML part file(s)")
    results: list[tuple[Path, int]] = []
    for index, xml_path in enumerate(part_paths):
        print()
        print("━" * 59)
        print(f"[dataset-parts] [{index + 1}/{len(part_paths)}] {xml_path.name}")
        print("━" * 59)
        part_selection = XmlRunSelection(
            event_id=selection.event_id,
            event_list=selection.event_list,
            all_events=(selection.event_list is None),
            all_dataset_parts=False,
            dataset_xml=xml_path,
            dataset_parts_glob=None,
            probe_log=selection.probe_log,
        )
        work_dir = flow_path.parent / "xsim_work" / "dataset_parts" / xml_path.stem
        ctx = build_runtime_context(flow_path, cfg, part_selection, importlib_module, work_dir=work_dir)
        rc = run_loaded_flow(flow_path, cfg, part_selection, ctx, adapter, strict=strict)
        results.append((xml_path, rc))
        print(f"[dataset-parts] {xml_path.name}: {'PASS' if rc == 0 else f'FAIL (exit {rc})'}")

    print()
    print("━" * 59)
    print("[dataset-parts] SUMMARY")
    print("━" * 59)
    overall = 0
    for xml_path, rc in results:
        status = "PASS" if rc == 0 else f"FAIL (exit {rc})"
        print(f"  {xml_path.name:<28} {status}")
        if rc != 0:
            overall = 1
    return overall


# ── one simulation per event ─────────────────────────────────────────────


def parse_event_list(event_list: str) -> List[str]:
    """Parse ``"1,7,10-12"`` into a sorted, de-duplicated list of events.

    A token qualified with a colon (``"4:2"``, an event and the processor it
    ran on) names one event the plugin resolves further, and is kept whole."""
    events: List[str] = []
    for part in event_list.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part and "-" in part:
            lo, hi = part.split("-", 1)
            events.extend(str(i) for i in range(int(lo), int(hi) + 1))
        else:
            int(part.split(":", 1)[0])
            events.append(part)
    return sorted(set(events), key=lambda e: (int(e.split(":", 1)[0]), e))


def _enumerate_xml_event_ids(xml_path: Path) -> List[int]:
    """Every event id in the dataset XML at *xml_path*."""
    from forge.verification.dataset_adapter import DatasetSource
    from forge.verification.dataset_service import DatasetService

    serialized = DatasetService().load(DatasetSource(raw_path=xml_path))
    return sorted(int(eid) for eid in serialized.metadata.event_ids)


def resolve_event_ids(selection) -> List[str]:
    """The events to simulate one at a time: the ones selected, else every
    event of the dataset. ``--all-events`` on ``forge dev verify run`` feeds
    them all through one simulation instead, which gives no per-event
    result."""
    if selection.event_list:
        return parse_event_list(selection.event_list)
    if selection.event_id is not None:
        return [str(selection.event_id)]
    if not selection.dataset_xml:
        raise ValueError(
            "no dataset XML resolved — pass --xml-input or check the flow's dataset.xml"
        )
    return [str(eid) for eid in _enumerate_xml_event_ids(selection.dataset_xml)]


def _tail_of_log(log_path: Any, *, lines: int = 20) -> Optional[str]:
    """The last *lines* of *log_path*, or None.

    The caller passes the log of the stage that failed, so a compile error
    is not reported from an empty simulate log."""
    if log_path is None or not Path(log_path).exists():
        return None
    text = Path(log_path).read_text(errors="replace")
    tail = "\n".join(text.splitlines()[-lines:])
    return tail or None


def _build_event_artifacts(exec_result: Any, outputs: Dict[str, Any]) -> List[Any]:
    """The event's ``ArtifactRef`` list: the backend's declared outputs and
    its waveform. Only the log the result points at is tagged with a stage;
    the backend does not say which stage wrote the others."""
    from forge.verification.results import ArtifactRef

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
    """``FORGE_CHECK|`` records from the event's ``simulate_log``, where the
    stimulus and its checks run, whichever log a failure points at."""
    from forge.verification.results import parse_forge_check_lines

    log_path = outputs.get("simulate_log")
    if log_path is None or not Path(log_path).exists():
        return []
    return parse_forge_check_lines(Path(log_path).read_text(errors="replace"))


def _regenerate_stimulus_for_event(
    flow_name: str, event_id: int, flow_dir: Path, dataset_xml: "Path | None"
) -> bool:
    """Rewrite ``stimulus_current.svh`` for *event_id* with the plugin's
    ``gen_stimulus.generate_for_flow``, so each event simulates its own
    stimulus rather than whatever the previous one left on disk.

    Returns False, leaving the stimulus as it is, when there is no dataset
    XML or the plugin has no ``gen_stimulus`` module (csim-only flows).

    ``generate_for_flow`` comes in three shapes: ``(flow_name, event_id,
    out_path)`` as ``forge dev verify init-plugin`` scaffolds it, the same
    with a ``dataset_path`` keyword (passthrough_demo), and ``(flow_name,
    event_id, xml_path=, verify_root=)``, which derives the output path
    itself. The signature decides which call is made.
    """
    import importlib
    import inspect

    if dataset_xml is None:
        return False
    try:
        gen_stimulus_mod = importlib.import_module("gen_stimulus")
        generate_for_flow = gen_stimulus_mod.generate_for_flow
    except (ImportError, AttributeError):
        return False
    out_path = flow_dir / "stimulus_current.svh"
    params = inspect.signature(generate_for_flow).parameters
    if "xml_path" in params and "verify_root" in params:
        # This plugin shape (as used by one production consumer's
        # verify/tools/gen_stimulus.py) takes the dataset path and verify/
        # root explicitly rather than a pre-computed out_path -- it derives
        # out_path itself as verify_root/flow_name/"stimulus_current.svh",
        # matching flow_dir here exactly (flow_dir IS verify_root/flow_name).
        generate_for_flow(flow_name, event_id, xml_path=dataset_xml, verify_root=flow_dir.parent)
    elif "dataset_path" in params:
        generate_for_flow(flow_name, event_id, out_path, dataset_path=dataset_xml)
    else:
        generate_for_flow(flow_name, event_id, out_path)
    return True


class RunRefused(Exception):
    """A flow cannot be run as asked; *action* says what to change."""

    def __init__(self, message: str, *, action: Optional[str] = None) -> None:
        super().__init__(message)
        self.action = action


@dataclass
class LoadedFlow:
    """One flow of a ``design.verification.yml``, bootstrapped and loaded."""

    name: str
    flow_path: Path
    cfg: Any
    importlib_module: Any
    adapter: Any

    @property
    def flow_dir(self) -> Path:
        return self.flow_path.parent


def open_flow(
    design_path: Path,
    flow_name: str,
    *,
    plugin: Optional[str] = None,
    consumer_root: "str | Path | None" = None,
) -> LoadedFlow:
    """Bootstrap the plugin of flow *flow_name* in *design_path* and load
    the ``verify.flow.yml`` that ``prepare`` wrote for it."""
    from forge.verification.backend_registry import get_adapter
    from forge.verification.design_contract import load_verify_design
    from forge.verification.exceptions import ForgeVerifyError
    from forge.verification.layout import canonical_flow_dir

    design_path = Path(design_path).resolve()
    if not design_path.exists():
        raise RunRefused(f"design file not found: {design_path}")
    try:
        contract = load_verify_design(design_path)
    except Exception as exc:  # noqa: BLE001
        raise RunRefused(str(exc), action="Fix design.verification.yml") from exc
    if not any(f.name == flow_name for f in contract.flows):
        available = ", ".join(f.name for f in contract.flows)
        raise RunRefused(f"flow {flow_name!r} not found in {design_path} (available: {available})")

    flow_path = canonical_flow_dir(design_path.parent, flow_name) / "verify.flow.yml"
    if not flow_path.exists():
        raise RunRefused(
            f"verify.flow.yml not found: {flow_path}",
            action=f"Run: forge test prepare {design_path} --flow {flow_name}",
        )
    if not bootstrap_for_flow(plugin, str(flow_path)):
        raise RunRefused("plugin bootstrap failed")
    try:
        cfg, importlib_module = load_flow_config(flow_path, resolve_consumer_root(consumer_root))
    except ForgeVerifyError as exc:
        raise RunRefused(str(exc), action="Fix the flow YAML or the consumer root") from exc
    try:
        adapter = get_adapter(cfg.backend)
    except (KeyError, LookupError) as exc:
        raise RunRefused(str(exc)) from exc
    return LoadedFlow(flow_name, flow_path, cfg, importlib_module, adapter)


def run_flow_events(flow: LoadedFlow, selection: XmlRunSelection, *, strict: bool = False):
    """Simulate each selected event of *flow* on its own and return the
    :class:`~forge.verification.results.FlowResult`.

    A flow with ``stimulus_mode: readmemh`` writes its stimulus once and
    selects each event with the ``+EVENT_INDEX`` plusarg, so the simulator
    compiles once; every other flow regenerates and recompiles per event.
    """
    from forge.verification.results import (
        RESULTS_SCHEMA,
        EventResult,
        FlowResult,
        diagnostic_for_event_failure,
    )

    try:
        event_ids = resolve_event_ids(selection)
    except ValueError as exc:
        raise RunRefused(str(exc)) from exc

    cfg, adapter = flow.cfg, flow.adapter
    flow_name = getattr(cfg, "flow_name", flow.name)
    readmemh_mode = getattr(cfg, "stimulus_mode", "svh_include") == "readmemh"
    id_to_index: Dict[str, int] = {}
    if readmemh_mode:
        try:
            import importlib

            importlib.import_module("bootstrap")
            generate_readmemh_stimulus = importlib.import_module("gen_stimulus").generate_readmemh_stimulus
        except (ImportError, AttributeError) as exc:
            raise RunRefused(
                "flow.stimulus_mode is 'readmemh' but the plugin's gen_stimulus.py "
                f"has no generate_readmemh_stimulus(): {exc}",
                action="Implement generate_readmemh_stimulus(flow_name, flow_dir) in the plugin's "
                       "tools/gen_stimulus.py (plugins/passthrough_demo has one).",
            ) from exc
        index_to_id = generate_readmemh_stimulus(flow.name, flow.flow_dir)
        id_to_index = {v: k for k, v in index_to_id.items()}

    events: List[EventResult] = []
    for event_id_str in event_ids:
        event_id = int(event_id_str.split(":", 1)[0])
        event_selection = XmlRunSelection(
            event_id=event_id,
            event_list=event_id_str if ":" in event_id_str else None,
            all_events=False,
            all_dataset_parts=False,
            dataset_xml=selection.dataset_xml,
            dataset_parts_glob=None,
            probe_log=selection.probe_log,
        )
        event_index: Optional[int] = None
        if readmemh_mode:
            if event_id_str not in id_to_index:
                raise RunRefused(
                    f"event id {event_id_str!r} not found in the readmemh dataset "
                    f"(known: {sorted(id_to_index)})"
                )
            event_index = id_to_index[event_id_str]
            work_dir = flow.flow_dir / "xsim_work" / "readmemh_shared"
        else:
            _regenerate_stimulus_for_event(flow.name, event_id, flow.flow_dir, selection.dataset_xml)
            work_dir = flow.flow_dir / "xsim_work" / "per_event" / event_id_str.replace(":", "_")

        ctx = build_runtime_context(
            flow.flow_path, cfg, event_selection, flow.importlib_module, work_dir=work_dir,
        )
        if readmemh_mode:
            ctx.event_index = event_index

        capture: Dict[str, Any] = {}
        rc = run_loaded_flow(
            flow.flow_path, cfg, event_selection, ctx, adapter, strict=strict, capture=capture,
        )
        exec_result = capture.get("result")
        outputs = capture.get("outputs", {}) or {}
        log_path = getattr(exec_result, "log_path", None)
        event = EventResult(
            event_id=event_id_str,
            # Only readmemh flows address events by dataset position.
            event_index=event_index,
            success=rc == 0,
            backend_id=adapter.backend_id,
            duration_s=getattr(exec_result, "duration_s", None),
            artifacts=_build_event_artifacts(exec_result, outputs),
            checks=_parse_event_checks(outputs),
        )
        if rc != 0:
            event.diagnostics = [diagnostic_for_event_failure(
                event_id=event_id_str,
                flow_name=flow_name,
                stage=getattr(exec_result, "stage", None),
                checker_ok=capture.get("checker_ok"),
                backend_success=getattr(exec_result, "success", False),
                log_path=str(log_path) if log_path else None,
                log_tail=_tail_of_log(log_path),
            )]
        events.append(event)

    return FlowResult(
        schema=RESULTS_SCHEMA, flow_name=flow_name, backend_id=adapter.backend_id, events=events,
    )


def write_flow_reports(
    result,
    flow_dir: Path,
    *,
    junit_xml: "str | Path | None" = None,
    results_json: "str | Path | None" = None,
    golden_comparison_json: "str | Path | None" = None,
) -> List[str]:
    """Write the requested reports of *result* and return their paths.

    The golden comparison needs the ``golden_model_provenance.json`` the
    plugin's ``gen_stimulus`` writes next to the flow; without it none is
    written.
    """
    import json

    written: List[str] = []
    if junit_xml:
        from forge.verification.junit_xml import write_junit_xml

        path = Path(junit_xml).expanduser().resolve()
        write_junit_xml(
            path,
            suite_name=result.flow_name,
            results=[
                {
                    "name": f"event_{e.event_id}",
                    "success": e.success,
                    "message": (
                        (e.diagnostics[0].context.get("log_tail") or e.diagnostics[0].message)
                        if not e.success and e.diagnostics else None
                    ),
                    "time": e.duration_s,
                }
                for e in result.events
            ],
        )
        written.append(str(path))
    if results_json:
        path = Path(results_json).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result.to_dict(), indent=2) + "\n")
        written.append(str(path))
    sidecar = flow_dir / "golden_model_provenance.json"
    if golden_comparison_json and sidecar.exists():
        from forge.verification.golden_comparison_result import build_golden_comparison_result

        path = Path(golden_comparison_json).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        comparison = build_golden_comparison_result(result, sidecar)
        path.write_text(json.dumps(comparison.to_dict(), indent=2) + "\n")
        written.append(str(path))
    return written
