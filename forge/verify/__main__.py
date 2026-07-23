#!/usr/bin/env python3
"""Framework CLI entry point.

Provides ``python -m forge.verify <command>`` for any consumer.
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

    forge verify preflight verify.flow.yml
    forge verify run       verify.flow.yml --plugin my_plugin
    forge verify generate  design.verification.yml
    forge verify generate  design.verification.yml --flow hit_decoder_xsim --strict
    forge verify prepare   design.verification.yml
    forge verify init-plugin my_new_plugin
    forge verify doctor    plugins/my_plugin/forge/verify/design.verification.yml
    forge verify doctor    plugins/my_plugin/forge/verify/design.verification.yml --json
    forge verify --debug   doctor  plugins/my_plugin/forge/verify/design.verification.yml
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
import sys
import traceback
from pathlib import Path


def _debug_enabled() -> bool:
    return "--debug" in sys.argv or os.environ.get("FORGE_VERIFY_DEBUG", "0") == "1"


def _print_guided_error(
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


def _cmd_preflight(args: argparse.Namespace) -> int:
    """Implement the ``preflight`` sub-command."""
    # ── Resolve consumer root ──────────────────────────────────────────────
    consumer_root_str = args.consumer_root or os.environ.get("VERIFY_CONSUMER_ROOT")

    # ── Bootstrap the plugin ───────────────────────────────────────────────
    if not _bootstrap(args.plugin, args.flow_file):
        return 1

    # ── Load the flow ──────────────────────────────────────────────────────
    from forge.verify.flow_loader import load_generic_flow
    from forge.verify.exceptions import ForgeVerifyError

    try:
        cfg = load_generic_flow(
            Path(args.flow_file),
            Path(consumer_root_str).resolve() if consumer_root_str else None,
        )
    except ForgeVerifyError as exc:
        return _print_guided_error(
            "flow",
            exc,
            action="Check the verify.flow.yml path and consumer root, then re-run forge verify preflight.",
            extra=["Use --debug for traceback details if the message is still unclear."],
        )

    # ── Run preflight ──────────────────────────────────────────────────────
    from forge.verify.preflight import run_preflight

    result = run_preflight(cfg)
    result.print_summary()
    return 0 if result.ok else 1


# ── Helpers shared by _cmd_run ─────────────────────────────────────────────

def _bootstrap(plugin_id: str | None, flow_file: str) -> bool:
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
        from forge.verify.plugin_registry import bootstrap_plugin as _bp
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
                _print_guided_error(
                    "bootstrap",
                    exc,
                    action=(
                        "Ensure the plugin is registered and that tools/bootstrap.py is importable, "
                        "or pass --plugin with the correct plugin id."
                    ),
                )
                return False
    else:
        from forge.verify.plugin_registry import bootstrap_from_flow as _bff
        try:
            _bff(Path(flow_file))
        except (FileNotFoundError, LookupError) as exc:
            _print_guided_error(
                "bootstrap",
                exc,
                action=(
                    "Check that verify.flow.yml exists and declares flow.plugin, or provide --plugin explicitly."
                ),
            )
            return False
    return True


def _resolve_consumer_root(arg: str | None) -> Path | None:
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


def _resolve_xml_run_selection(args: argparse.Namespace, cfg) -> XmlRunSelection:
    """Resolve framework-standard XML dataset run selectors.

    Precedence:
      CLI args > environment variables > flow defaults.
    """
    event_id_raw = (
        args.event_id
        or os.environ.get("EVENT_ID")
        or getattr(cfg, "dataset_default_event_id", None)
    )
    event_list_raw = getattr(args, "event_list", None) or os.environ.get("EVENT_LIST")
    all_events = bool(
        getattr(args, "all_events", False)
        or (os.environ.get("ALL_EVENTS", "0") == "1")
    )
    all_dataset_parts = bool(
        getattr(args, "all_dataset_parts", False)
        or (os.environ.get("ALL_DATASET_PARTS", "0") == "1")
    )
    dataset_parts_glob = (
        getattr(args, "dataset_parts_glob", None)
        or os.environ.get("DATASET_PARTS_GLOB")
    )
    xml_input_raw = (
        args.xml_input
        or os.environ.get("XML_INPUT")
        or getattr(cfg, "dataset_xml", None)
    )
    probe_log = args.probe_log or (os.environ.get("PROBE_LOG", "0") == "1")
    return XmlRunSelection(
        event_id=int(event_id_raw) if event_id_raw is not None else None,
        event_list=str(event_list_raw).strip() if event_list_raw else None,
        all_events=all_events,
        all_dataset_parts=all_dataset_parts,
        dataset_xml=Path(xml_input_raw) if xml_input_raw else None,
        dataset_parts_glob=str(dataset_parts_glob).strip() if dataset_parts_glob else None,
        probe_log=probe_log,
    )


def _build_runtime_context(
    flow_path: Path,
    cfg,
    selection: XmlRunSelection,
    importlib_module,
    work_dir: Path | None = None,
) -> object:
    """Build framework-generic or plugin-specific runtime context.

    Framework base context carries only generic execution state. Plugin-owned
    runtime_context.py modules may extend it with plugin-specific fields.
    """
    extra_overrides = {}
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

    from forge.verify.runtime_context import RuntimeContext as FrameworkRuntimeContext

    return FrameworkRuntimeContext(
        work_dir=work_dir,
        probe_log=selection.probe_log,
        extra_overrides=extra_overrides,
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
    consumer_root = _resolve_consumer_root(args.consumer_root)

    # ── 2. Bootstrap ────────────────────────────────────────────────────────
    if not _bootstrap(args.plugin, args.flow_file):
        return 1

    flow_path = Path(args.flow_file)

    # ── 3. Load flow ────────────────────────────────────────────────────────
    # Prefer plugin's load_flow (richer type) over generic when available.
    try:
        import importlib as _il
        _bootstrap_mod = _il.import_module("bootstrap")
        _flow_mod = _il.import_module("flow_config")
        cfg = _flow_mod.load_flow(flow_path, consumer_root=consumer_root)
    except (ImportError, AttributeError):
        from forge.verify.flow_loader import load_generic_flow
        from forge.verify.exceptions import ForgeVerifyError
        try:
            cfg = load_generic_flow(flow_path, consumer_root)
        except ForgeVerifyError as exc:
            return _print_guided_error(
                "flow",
                exc,
                action="Fix the flow YAML or consumer-root path, then re-run forge verify run.",
            )

    # ── 4. Build runtime context ─────────────────────────────────────────────
    selection = _resolve_xml_run_selection(args, cfg)

    # ── 6. Get backend adapter ────────────────────────────────────────────────
    from forge.verify.backend_registry import get_adapter
    try:
        adapter = get_adapter(cfg.backend)
    except (KeyError, LookupError) as exc:
        return _print_guided_error(
            "backend",
            exc,
            action="Use a supported backend id in the flow file or bootstrap a plugin-specific backend override.",
        )

    strict = getattr(args, "strict", False)
    if selection.all_dataset_parts:
        return _run_dataset_parts(flow_path, cfg, selection, adapter, locals().get("_il"), strict=strict)

    ctx = _build_runtime_context(flow_path, cfg, selection, locals().get("_il"))
    return _run_one_loaded_flow(flow_path, cfg, selection, ctx, adapter, strict=strict)


def _run_one_loaded_flow(flow_path: Path, cfg, selection: XmlRunSelection, ctx, adapter, strict: bool = False) -> int:
    """Run one resolved XML selection through preflight, backend, and checker."""

    # ── 5. Preflight ─────────────────────────────────────────────────────────
    from forge.verify.preflight import run_preflight
    pre = run_preflight(cfg, xml_input=selection.dataset_xml)
    pre.print_summary()
    if not pre.ok:
        return 1

    # ── Strict-mode layout check ──────────────────────────────────────────────
    if strict:
        from forge.verify.layout import validate_layout
        _verify_root = flow_path.parent.parent
        _layout_errors = validate_layout(_verify_root)
        if _layout_errors:
            print("[strict] Layout violations:", file=sys.stderr)
            for le in _layout_errors:
                print(f"  {le}", file=sys.stderr)
            return 1

    # ── 7. Validate backend requirements ─────────────────────────────────────
    errors = adapter.validate_backend_requirements(cfg)
    if errors:
        for e in errors:
            print(f"ERROR (backend validation): {e}", file=sys.stderr)
        return 1

    # ── 8. Prepare inputs ────────────────────────────────────────────────────
    try:
        adapter.prepare_backend_inputs(cfg, ctx)
    except Exception as exc:  # noqa: BLE001
        return _print_guided_error(
            "prepare",
            exc,
            action="Re-run forge verify generate and ensure required DUT, testbench, and stimulus artifacts exist.",
        )

    # ── 9. Run backend ───────────────────────────────────────────────────────
    try:
        result = adapter.run_backend(cfg, ctx)
    except Exception as exc:  # noqa: BLE001
        return _print_guided_error(
            "run",
            exc,
            action="Check simulator logs, tool setup, and generated artifacts, then re-run forge verify run.",
        )

    if not result.success:
        print(f"FAIL  (backend exited {result.exit_code})", file=sys.stderr)

    # ── 10. Checker lifecycle ────────────────────────────────────────────────
    checker_ok: bool | None = None
    has_checker = getattr(cfg, "has_checker", False)
    if has_checker and result.success:
        run_checker = getattr(adapter, "run_checker", None)
        if callable(run_checker):
            try:
                checker_ok = run_checker(cfg, ctx, result)
            except Exception as exc:  # noqa: BLE001
                _print_guided_error(
                    "checker",
                    exc,
                    action="Inspect the checker configuration and observed log path in verify.flow.yml, then re-run.",
                )
                checker_ok = False
        else:
            # No plugin checker hook — treat as not-applicable.
            checker_ok = None

    # ── 11. Artifact summary ─────────────────────────────────────────────────
    outputs = adapter.describe_backend_outputs(cfg, ctx)
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


def _run_dataset_parts(flow_path: Path, cfg, selection: XmlRunSelection, adapter, importlib_module, strict: bool = False) -> int:
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
        ctx = _build_runtime_context(flow_path, cfg, part_selection, importlib_module, work_dir=work_dir)
        rc = _run_one_loaded_flow(flow_path, cfg, part_selection, ctx, adapter, strict=strict)
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


def _dataset_path_relative_to_consumer(design_path: Path, consumer_root: Path, raw_path: str | None) -> str | None:
    if not raw_path:
        return None
    path = Path(raw_path)
    abs_path = path if path.is_absolute() else (design_path.parent / path)
    try:
        return str(abs_path.resolve().relative_to(consumer_root.resolve()))
    except ValueError:
        return str(abs_path)


def _resolve_dataset_parts_glob(design_path: Path, consumer_root: Path, dataset_decl) -> str | None:
    return _dataset_path_relative_to_consumer(
        design_path,
        consumer_root,
        getattr(dataset_decl, "parts_glob", None) if dataset_decl else None,
    )


def _resolve_dataset_parts(design_path: Path, consumer_root: Path, dataset_decl) -> tuple[str, ...]:
    if dataset_decl is None:
        return ()
    return tuple(
        part for part in (
            _dataset_path_relative_to_consumer(design_path, consumer_root, raw_part)
            for raw_part in (getattr(dataset_decl, "parts", ()) or ())
        )
        if part
    )


def _cmd_generate(args: argparse.Namespace) -> int:
    """Implement the ``generate`` sub-command.

    Reads ``design.verification.yml``, iterates over declared flows, and generates
    ``verify.flow.yml``, ``tb_<module>.sv``, and ``wave.tcl`` for each matching flow.

    For xsim flows a ``port_map.yaml`` is required for port-aware TB generation.
    For hls_csim flows the TB is generated without DUT port connections (CSIM
    flows do not use an RTL testbench).
    """
    from forge.verify.design_contract import load_verify_design, VerifyDesignContract
    from forge.verify.gen_sim import generate_testbench, generate_verify_flow_yml
    from forge.verify.layout import canonical_flow_dir

    design_path = Path(args.design_file).resolve()
    if not design_path.exists():
        return _print_guided_error(
            "design",
            FileNotFoundError(f"design file not found: {design_path}"),
            action="Check the design.verification.yml path and re-run forge verify generate.",
        )

    try:
        contract: VerifyDesignContract = load_verify_design(design_path)
    except Exception as exc:  # noqa: BLE001
        return _print_guided_error(
            "design",
            exc,
            action="Fix design.verification.yml syntax or schema errors, then re-run forge verify generate.",
        )

    output_base = design_path.parent
    consumer_root = _resolve_consumer_root(args.consumer_root) or Path.cwd()

    # Deprecation warning for --use-kind-subdir
    if getattr(args, "use_kind_subdir", False):
        print(
            "WARNING: --use-kind-subdir is DEPRECATED (legacy/compatibility only).\n"
            "  This flag writes flows under <kind>/<flow_name>/ instead of the canonical\n"
            "  flat <flow_name>/ layout.  New plugins and new flows must use the flat layout.\n"
            "  forge verify doctor will report a layout violation for any kind-subdir trees.",
            file=sys.stderr,
        )

    # Resolve port map once (optional — only needed for xsim flows)
    cli_port_map: Path | None = None
    if args.port_map:
        cli_port_map = Path(args.port_map).resolve()
        if not cli_port_map.exists():
            print(f"ERROR: --port-map file not found: {cli_port_map}", file=sys.stderr)
            return 1

    # Filter flows
    flows = list(contract.flows)
    if args.flow:
        flows = [f for f in flows if f.name == args.flow]
        if not flows:
            names = [f.name for f in contract.flows]
            print(
                f"ERROR: flow {args.flow!r} not found in {design_path.name}.\n"
                f"  Available: {names}",
                file=sys.stderr,
            )
            return 1

    _CSIM_KINDS = {"hls_csim", "hls_cosim"}
    generated: list[str] = []
    errors: list[str] = []

    for flow_decl in flows:
        # Use canonical_flow_dir for the supported path.
        # --use-kind-subdir is legacy and writes to the non-canonical location.
        if getattr(args, "use_kind_subdir", False):
            flow_dir = output_base / flow_decl.kind / flow_decl.name
        else:
            flow_dir = canonical_flow_dir(output_base, flow_decl.name)

        # Resolve timing (flow overrides contract defaults)
        defaults = contract.defaults
        clk   = getattr(defaults, "clk_period_ns", 4.0)
        rst   = flow_decl.reset_cycles               or getattr(defaults, "reset_cycles", 4)
        idle  = flow_decl.idle_cycles_after_reset    or getattr(defaults, "idle_cycles_after_reset", 0)
        drain = flow_decl.post_stimulus_drain_cycles or getattr(defaults, "post_stimulus_drain_cycles", 8)

        # Resolve dataset XML path.
        # Paths in design.verification.yml are relative to the file's own
        # directory (the plugin's verify/ root), not the consumer/framework root.
        # This keeps plugins self-contained regardless of their mount point.
        ds = contract.get_dataset(flow_decl.dataset) if hasattr(contract, "get_dataset") else None
        dataset_xml_raw: str | None = getattr(ds, "xml", None) if ds else None
        if dataset_xml_raw and not Path(dataset_xml_raw).is_absolute():
            _abs_xml = (design_path.parent / dataset_xml_raw).resolve()
            try:
                dataset_xml_raw = str(_abs_xml.relative_to(consumer_root.resolve()))
            except ValueError:
                dataset_xml_raw = str(_abs_xml)

        # ── verify.flow.yml ───────────────────────────────────────────────
        flow_yml_path = flow_dir / "verify.flow.yml"
        # Derive dut_rtl path from dut_rtl_source if available
        dut_rtl_source = getattr(flow_decl, "dut_rtl_source", None)
        dut_rtl_rel: str | None = (
            f"{dut_rtl_source}/{flow_decl.top_module}.v" if dut_rtl_source else None
        )
        dut_manifest_rel: str | None = None
        dut_port_map_rel: str | None = None
        dut_tb_bindings_rel: str | None = None
        dut_port_signature_rel: str | None = None
        if dut_rtl_source:
            manifest_candidate = consumer_root / dut_rtl_source / "build_manifest.json"
            if manifest_candidate.exists():
                dut_manifest_rel = f"{dut_rtl_source}/build_manifest.json"
            port_map_candidate = consumer_root / dut_rtl_source / "port_map.yaml"
            if port_map_candidate.exists():
                dut_port_map_rel = f"{dut_rtl_source}/port_map.yaml"
            tb_bindings_candidate = consumer_root / dut_rtl_source / "tb_bindings.svh"
            if tb_bindings_candidate.exists():
                dut_tb_bindings_rel = f"{dut_rtl_source}/tb_bindings.svh"
            port_signature_candidate = consumer_root / dut_rtl_source / "port_signature.json"
            if port_signature_candidate.exists():
                dut_port_signature_rel = f"{dut_rtl_source}/port_signature.json"
        if args.dry_run:
            print(f"[dry-run] would write {flow_yml_path.relative_to(output_base)}")
        else:
            try:
                generate_verify_flow_yml(
                    flow_decl=flow_decl,
                    defaults=defaults,
                    output_path=flow_yml_path,
                    consumer_root=consumer_root,
                    plugin_id=getattr(contract, "plugin", "") or "",
                    dut_rtl=dut_rtl_rel,
                    dut_manifest=dut_manifest_rel,
                    dut_port_map=dut_port_map_rel,
                    dut_tb_bindings=dut_tb_bindings_rel,
                    dut_port_signature=dut_port_signature_rel,
                    dataset_xml=dataset_xml_raw,
                    dataset_parts_glob=_resolve_dataset_parts_glob(design_path, consumer_root, ds),
                    dataset_parts=_resolve_dataset_parts(design_path, consumer_root, ds),
                    extra_simulation_fields=getattr(defaults, "extra", ()),
                    source_path=str(design_path.name),
                )
                generated.append(str(flow_yml_path))
                print(f"  wrote {flow_yml_path.relative_to(output_base)}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{flow_decl.name}: verify.flow.yml: {exc}")
                continue

        # ── TB + wave (xsim flows only) ───────────────────────────────────
        if flow_decl.kind in _CSIM_KINDS:
            # CSIM flows run a native binary — no RTL testbench to generate.
            continue

        # Resolve port_map: CLI arg > per-flow discovery > root port_map > auto-derive from dut_rtl_source
        tb_port_map = cli_port_map
        if tb_port_map is None:
            candidate = flow_dir / "port_map.yaml"
            if candidate.exists():
                tb_port_map = candidate

        if tb_port_map is None:
            # Check consumer_root/port_map.yaml (generated by topgen gen-top).
            # Only use it if its top_module matches this flow's top_module.
            root_pm = consumer_root / "port_map.yaml"
            if root_pm.exists():
                try:
                    import yaml as _yaml_pm  # noqa: PLC0415
                    _raw = _yaml_pm.safe_load(root_pm.read_text()) or {}
                    if _raw.get("top_module") == flow_decl.top_module:
                        tb_port_map = root_pm
                        print(f"  using root port_map.yaml for {flow_decl.name}")
                except Exception:
                    pass

        if tb_port_map is None:
            # Check dut_rtl_source/port_map.yaml for gen-top style flows where
            # the generated DUT directory owns its own port map and manifest.
            dut_rtl_src = getattr(flow_decl, "dut_rtl_source", None)
            if dut_rtl_src:
                dut_pm = consumer_root / dut_rtl_src / "port_map.yaml"
                if dut_pm.exists():
                    try:
                        import yaml as _yaml_pm  # noqa: PLC0415
                        _raw = _yaml_pm.safe_load(dut_pm.read_text()) or {}
                        if _raw.get("top_module") == flow_decl.top_module:
                            tb_port_map = dut_pm
                            print(f"  using DUT port_map.yaml for {flow_decl.name}")
                    except Exception:
                        pass

        if tb_port_map is None:
            # Auto-derive from dut_rtl_source when the HLS Verilog exists.
            # dut_rtl_source is a required field in design.verification.yml so it is always set.
            dut_rtl_src = getattr(flow_decl, "dut_rtl_source", None)
            if dut_rtl_src:
                verilog_path = consumer_root / dut_rtl_src / f"{flow_decl.top_module}.v"
                if verilog_path.exists():
                    from forge.verify.rtl_introspection import (  # noqa: PLC0415
                        extract_and_write,
                        introspection_mode,
                        IntrospectionError,
                    )
                    pm_path = flow_dir / "port_map.yaml"
                    if args.dry_run:
                        _mode = introspection_mode()
                        print(
                            f"[dry-run] would derive {pm_path.relative_to(output_base)}"
                            f" from {verilog_path.name} [mode={_mode}]"
                        )
                    else:
                        try:
                            flow_dir.mkdir(parents=True, exist_ok=True)
                            ports = extract_and_write(verilog_path, pm_path)
                            _mode = introspection_mode()
                            print(
                                f"  derived {pm_path.relative_to(output_base)}"
                                f" ({len(ports)} ports from {verilog_path.name}"
                                f" via {_mode} mode)"
                            )
                        except IntrospectionError as exc:
                            errors.append(f"{flow_decl.name}: {exc}")
                            continue
                    tb_port_map = pm_path

        if tb_port_map is None:
            rtl_hint = (
                f"{getattr(flow_decl, 'dut_rtl_source', '?')}/{flow_decl.top_module}.v"
            )
            strict = getattr(args, "strict", False)
            if args.dry_run:
                print(
                    f"[dry-run] would skip TB/wave for {flow_decl.name}"
                    f" — RTL not available ({rtl_hint})"
                )
            elif strict:
                errors.append(
                    f"{flow_decl.name}: [strict] RTL not available and --strict is set.\n"
                    f"  RTL path: {rtl_hint}\n"
                    f"  → Run HLS synthesis first, then re-run forge verify generate"
                )
            else:
                errors.append(
                    f"{flow_decl.name}: skipped TB/wave — port_map.yaml not found and"
                    f" RTL not available ({rtl_hint})\n"
                    f"  → Run HLS synthesis first, then re-run forge verify generate"
                )
            continue

        generated_port_map = flow_dir / "port_map.yaml"
        if not args.dry_run and tb_port_map.resolve() != generated_port_map.resolve():
            import shutil as _shutil  # noqa: PLC0415
            flow_dir.mkdir(parents=True, exist_ok=True)
            _shutil.copyfile(tb_port_map, generated_port_map)
            generated.append(str(generated_port_map))
            tb_port_map = generated_port_map

        if args.dry_run:
            tb_name = f"{flow_decl.tb_module}.sv"
            print(f"[dry-run] would write {(flow_dir / tb_name).relative_to(output_base)}"
                  f" using {tb_port_map.name}")
            print(f"[dry-run] would write {(flow_dir / 'wave.tcl').relative_to(output_base)}")
            continue

        try:
            result = generate_testbench(
                flow_name=flow_decl.name,
                top_module=flow_decl.top_module,
                tb_module=flow_decl.tb_module,
                clk_period_ns=clk,
                reset_cycles=rst,
                idle_cycles_after_reset=idle,
                post_stimulus_drain_cycles=drain,
                output_dir=flow_dir,
                port_map_path=tb_port_map,
            )
            generated.append(str(result["tb"]))
            generated.append(str(result["wave"]))
            print(f"  wrote {result['tb'].relative_to(output_base)}")
            print(f"  wrote {result['wave'].relative_to(output_base)}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{flow_decl.name}: TB generation: {exc}")

    if errors:
        print("\nErrors:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 1

    if not args.dry_run:
        print(f"\nGenerated {len(generated)} file(s).")
    return 0


# ── prepare command ────────────────────────────────────────────────────────

def _cmd_prepare(args: argparse.Namespace) -> int:
    """Implement the ``prepare`` sub-command.

    Combines:
      1. forge verify generate  — write verify.flow.yml + TB + wave.tcl
      2. stimulus contract validation — check stimulus_current.svh for all
         generated xsim flows that already have a stimulus file
      3. layout validation — verify canonical flat layout
    """
    from forge.verify.design_contract import load_verify_design

    design_path = Path(args.design_file).resolve()
    if not design_path.exists():
        return _print_guided_error(
            "prepare",
            FileNotFoundError(f"design file not found: {design_path}"),
            action="Check the design.verification.yml path and re-run forge verify prepare.",
        )

    # ── Step 1: Generate artifacts ─────────────────────────────────────────
    # Reuse _cmd_generate by constructing a compatible Namespace.
    gen_args = argparse.Namespace(
        design_file=str(design_path),
        flow=getattr(args, "flow", None),
        consumer_root=getattr(args, "consumer_root", None),
        port_map=getattr(args, "port_map", None),
        dry_run=getattr(args, "dry_run", False),
        use_kind_subdir=False,      # prepare always uses canonical flat layout
        strict=getattr(args, "strict", False),
    )
    print("[prepare] Step 1/3: Generating artifacts …")
    rc = _cmd_generate(gen_args)
    if rc != 0:
        print("[prepare] Step 1/3 FAILED — stopping.", file=sys.stderr)
        return rc
    print("[prepare] Step 1/3 OK")

    if getattr(args, "dry_run", False):
        print("[prepare] dry-run: skipping stimulus and layout checks.")
        return 0

    # ── Step 2: Stimulus contract validation ───────────────────────────────
    from forge.verify.stimulus_contract import validate_stimulus

    try:
        contract = load_verify_design(design_path)
    except Exception as exc:  # noqa: BLE001
        return _print_guided_error(
            "prepare/contract",
            exc,
            action="Fix design.verification.yml before running forge verify prepare again.",
        )

    output_base = design_path.parent
    flow_filter = getattr(args, "flow", None)
    _CSIM_KINDS = {"hls_csim", "hls_cosim"}

    print("[prepare] Step 2/3: Validating stimulus contracts …")
    stim_errors: list[str] = []
    for flow_decl in contract.flows:
        if flow_filter and flow_decl.name != flow_filter:
            continue
        if flow_decl.kind in _CSIM_KINDS:
            continue  # No SVH for csim flows
        flow_dir  = output_base / flow_decl.name
        stim_path = flow_dir / "stimulus_current.svh"
        if not stim_path.exists():
            # Not an error at prepare time — warn only; user still needs to run gen_stimulus.py
            print(
                f"  [prepare] WARNING: {flow_decl.name}: stimulus not yet generated: "
                f"{stim_path.name}"
            )
            continue
        sc = validate_stimulus(stim_path)
        if sc.ok:
            print(f"  [prepare] {flow_decl.name}: stimulus OK")
        else:
            for e in sc.errors:
                stim_errors.append(f"{flow_decl.name}: {e}")
    if stim_errors:
        print("[prepare] Step 2/3 FAILED:", file=sys.stderr)
        for e in stim_errors:
            print(f"  {e}", file=sys.stderr)
        return 1
    print("[prepare] Step 2/3 OK")

    # ── Step 3: Layout validation ──────────────────────────────────────────
    from forge.verify.layout import validate_layout

    print("[prepare] Step 3/3: Checking canonical layout …")
    layout_errors = validate_layout(output_base)
    if layout_errors:
        print("[prepare] Step 3/3 layout violations:", file=sys.stderr)
        for e in layout_errors:
            print(f"  {e}", file=sys.stderr)
        return 1
    print("[prepare] Step 3/3 OK")

    print("[prepare] All checks passed.")
    return 0


# ── init-plugin command ────────────────────────────────────────────────────

_BOOTSTRAP_TEMPLATE = '''\
#!/usr/bin/env python3
"""{plugin_id} plugin registration — connects {plugin_id} to forge verify.

After importing this module, {plugin_id} is known to the framework and
forge verify generate / run will work for declared flows.
"""
from __future__ import annotations

import sys
from pathlib import Path

# ── Ensure framework and tools/ are importable ──────────────────────────
_HERE      = Path(__file__).resolve().parent
_FW_PYTHON = _HERE.parents[3] / "framework" / "verify" / "python"
for _p in [str(_FW_PYTHON), str(_HERE)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── Plugin identity ──────────────────────────────────────────────────────
PLUGIN_ID = "{plugin_id}"

_FLOW_KINDS: list[str] = [
    "hls_csim",
    "single_module_rtl",
]

# ── Self-declare with the framework ────────────────────────────────────
from forge.verify.plugin_registry import declare_plugin_bootstrap as _declare
_declare(PLUGIN_ID, __name__)


def bootstrap() -> None:
    """Declare {plugin_id} with the framework.  Idempotent."""
    from forge.verify.plugin_registry import is_plugin_bootstrapped, mark_bootstrapped
    if is_plugin_bootstrapped(PLUGIN_ID):
        return
    mark_bootstrapped(
        PLUGIN_ID,
        backends=["csim", "xsim"],
        flow_kinds=_FLOW_KINDS,
    )
'''

_GEN_STIMULUS_TEMPLATE = '''\
#!/usr/bin/env python3
"""{plugin_id} stimulus generator.

Generates ``stimulus_current.svh`` for each declared xsim flow.

Usage::

    python3 gen_stimulus.py --flow <flow_name> [--event-id <n>]

Adapt ``generate_for_flow()`` below to match your DUT's port semantics:
  1. Replace signal names (``data_in``, ``result_out``) with your top-function ports.
  2. Adjust widths to match the HLS-generated RTL.
  3. Use ``em.drive_valid()`` for valid/ready-style interfaces.
  4. Use ``with em.event_block(label)`` to group logically-related stimulus.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ── Ensure framework is importable ────────────────────────────────────
_HERE = Path(__file__).resolve().parent
_FW_PYTHON = _HERE.parents[3] / "framework" / "verify" / "python"
if str(_FW_PYTHON) not in sys.path:
    sys.path.insert(0, str(_FW_PYTHON))

from forge.verify.stimulus_helpers import StimulusEmitter, write_run_stimulus_svh


def generate_for_flow(flow_name: str, event_id: int, out_path: Path) -> None:
    """Generate stimulus for *flow_name* event *event_id*.

    The framework guarantees that the testbench provides:
      - ``ap_clk``  — DUT clock (driven by TB)
      - ``ap_rst``  — synchronous active-high reset (driven by TB)

    Everything else is driven/checked here.
    """
    em = StimulusEmitter()

    # ── Reset phase ──────────────────────────────────────────────────
    with em.event_block("reset"):
        em.drive("ap_rst", 1, width=1)
        em.tick(cycles=4)
        em.drive("ap_rst", 0, width=1)
        em.tick()

    # ── Stimulus event ───────────────────────────────────────────────
    with em.event_block(f"event_{{event_id}}"):
        # TODO: replace signal names + values with your DUT ports:
        em.drive("data_in",    0xABCD, width=32)   # <-- change me
        em.drive("data_valid", 1,      width=1)
        # Or, for valid/ready-style interfaces:
        # em.drive_valid("data_in", 0xABCD, width=32, valid_signal="data_valid")
        em.tick(cycles=4)  # wait enough cycles for DUT pipeline to complete
        em.deassert_valid("data_valid")
        em.tick(cycles=2)

    # ── Output checks ────────────────────────────────────────────────
    with em.event_block("checks"):
        # TODO: replace with your expected output values:
        em.check("result_out", 0x1234, width=16, label="result_check")  # <-- change me

    write_run_stimulus_svh(em, out_path, header_comment=f"flow={{flow_name}} event={{event_id}}")
    print(f"  wrote {{out_path}}")


def main() -> None:
    ap = argparse.ArgumentParser(description="{plugin_id} stimulus generator")
    ap.add_argument("--flow",     required=True, help="Flow name from design.verification.yml")
    ap.add_argument("--event-id", dest="event_id", type=int, default=1)
    args = ap.parse_args()

    verify_root = Path(__file__).resolve().parents[1]
    flow_dir    = verify_root / args.flow
    out_path    = flow_dir / "stimulus_current.svh"
    flow_dir.mkdir(parents=True, exist_ok=True)

    generate_for_flow(args.flow, args.event_id, out_path)


if __name__ == "__main__":
    main()
'''

_DESIGN_VERIFICATION_TEMPLATE = '''\
# ═══════════════════════════════════════════════════════════════════════
# {plugin_id} — Verification Design Contract
# ═══════════════════════════════════════════════════════════════════════
#
# Edit this file, then run:
#   forge verify generate <path/to/this/file>    # generate TB + port_map
#   forge verify doctor   <path/to/this/file>    # health-check
#   forge verify run      <flow/verify.flow.yml> # simulate
# ───────────────────────────────────────────────────────────────────────
plugin: {plugin_id}

# ─── Datasets ─────────────────────────────────────────────────────────
datasets:
  {plugin_id}_golden:
    xml: plugins/{plugin_id}/verify/schemas/data/{plugin_id}_golden.xml
    description: Golden reference dataset for {plugin_id}

# ─── Simulation Defaults ──────────────────────────────────────────────
defaults:
  clk_period_ns:              4.0      # Clock period in ns
  reset_cycles:               4        # How many cycles ap_rst is asserted
  idle_cycles_after_reset:    0        # Idle cycles between reset and first event
  post_stimulus_drain_cycles: 4        # Drain cycles after last event

# ─── Flows ────────────────────────────────────────────────────────────
# Supported (kind, backend) pairs:
#   hls_csim       → csim   (HLS C-simulation — fastest, no RTL needed)
#   single_module_rtl → xsim (RTL simulation of one HLS-generated module)
flows:

  # ── HLS C-simulation (always works after HLS synthesis) ─────────────
  - name:             {plugin_id}_csim
    kind:             hls_csim
    backend:          csim
    top_module:       {plugin_id}          # TODO: your HLS top function name
    tb_module:        tb_{plugin_id}       # HLS-generated testbench binary name
    dut_rtl_source:   build_hls/{plugin_id}/solution1  # TODO: verify HLS output path
    dataset:          {plugin_id}_golden
    default_event_id: 1
    coverage_intent:  functional

  # ── RTL simulation via Vivado XSIM ──────────────────────────────────
  # Uncomment once you have run HLS synthesis (produces the RTL files).
  # - name:             {plugin_id}_xsim
  #   kind:             single_module_rtl
  #   backend:          xsim
  #   top_module:       {plugin_id}          # TODO: your HLS top function name
  #   tb_module:        tb_{plugin_id}_xsim  # Framework-generated testbench name
  #   dut_rtl_source:   build_hls/{plugin_id}/solution1/syn/verilog  # TODO: verify
  #   dataset:          {plugin_id}_golden
  #   default_event_id: 1
  #   stimulus_mode:    svh_include        # Framework default — do not change
  #   wave_mode:        tcl               # Waveform dump via wave.tcl
  #   rtl_source_type:  hls               # Port extraction style (hls or generic)
  #   checker_mode:     log_scan          # How PASS/FAIL is detected
  #   coverage_intent:  functional
'''


def _cmd_init_plugin(args: argparse.Namespace) -> int:
    """Implement the ``init-plugin`` sub-command.

    Scaffolds a new plugin skeleton under ``<plugins_root>/<plugin_id>/verify/``.
    """
    plugin_id   = args.plugin_id
    plugins_root = Path(args.plugins_root)
    verify_root  = plugins_root / plugin_id / "verify"
    tools_dir    = verify_root / "tools"
    schemas_dir  = verify_root / "schemas" / "data"
    tests_dir    = verify_root / "tests"

    files_to_create = {
        verify_root / "design.verification.yml":
            _DESIGN_VERIFICATION_TEMPLATE.format(plugin_id=plugin_id),
        tools_dir / "bootstrap.py":
            _BOOTSTRAP_TEMPLATE.format(plugin_id=plugin_id),
        tools_dir / "gen_stimulus.py":
            _GEN_STIMULUS_TEMPLATE.format(plugin_id=plugin_id),
        tests_dir / "__init__.py": "",
        tests_dir / f"test_{plugin_id}_verify.py": (
            f'"""Placeholder tests for {plugin_id} verification flows."""\n\n'
            f"def test_placeholder() -> None:\n"
            f"    pass\n"
        ),
    }

    if args.dry_run:
        print(f"[init-plugin] Would create plugin skeleton for {plugin_id!r}:")
        for path in sorted(files_to_create):
            print(f"  {path}")
        return 0

    created: list[Path] = []
    skipped: list[Path] = []
    for dest_path, content in files_to_create.items():
        if dest_path.exists():
            skipped.append(dest_path)
            continue
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text(content)
        created.append(dest_path)

    # Create stub XML dataset file so the contract loader doesn't fail immediately
    stub_xml = schemas_dir / f"{plugin_id}_golden.xml"
    if not stub_xml.exists():
        schemas_dir.mkdir(parents=True, exist_ok=True)
        stub_xml.write_text(
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<!-- {plugin_id} golden dataset — replace with real data -->\n'
            f'<dataset plugin="{plugin_id}">\n'
            f'  <event id="1">\n'
            f'    <!-- TODO: add event data here -->\n'
            f'  </event>\n'
            f'</dataset>\n'
        )
        created.append(stub_xml)

    print(f"[init-plugin] Plugin {plugin_id!r} scaffolded:")
    for p in created:
        print(f"  created  {p}")
    for p in skipped:
        print(f"  skipped  {p}  (already exists)")

    print(f"\nNext steps:")
    print(f"  1. Edit {verify_root / 'design.verification.yml'}")
    print(f"  2. Implement {tools_dir / 'gen_stimulus.py'}")
    print(f"  3. Run: forge verify generate {verify_root / 'design.verification.yml'}")
    print(f"  4. Run: forge verify doctor   {verify_root / 'design.verification.yml'}")
    return 0


# ── doctor command ─────────────────────────────────────────────────────────

def _cmd_doctor(args: argparse.Namespace) -> int:
    """Implement the ``doctor`` sub-command.

    Runs a comprehensive, READ-ONLY health-check on a plugin's verify setup:
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

    Pass ``--json`` to receive machine-readable output (DiagnosticReport).
    Pass ``--strict`` to fail on warnings as well as errors.
    """
    import shutil as _shutil
    from forge.verify.diagnostics import DiagnosticReport, Severity

    design_path   = Path(args.design_file).resolve()
    consumer_root = _resolve_consumer_root(
        getattr(args, "consumer_root", None)
    ) or Path.cwd()
    strict        = getattr(args, "strict", False)
    json_mode     = getattr(args, "json", False)

    report = DiagnosticReport(label=design_path.name)

    # ── 1. File exists ─────────────────────────────────────────────────────
    if not design_path.exists():
        report.error(
            "FWV015",
            f"design.verification.yml not found: {design_path}",
            action="Create the file or check the path",
            path=str(design_path),
        )
        return _doctor_emit(report, strict=strict, json_mode=json_mode)

    # ── 2. Load contract ───────────────────────────────────────────────────
    from forge.verify.design_contract import load_verify_design
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
            action="Create with: forge verify init-plugin <plugin_id>",
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
                f"Create at {gen_stimulus_py} (see forge verify init-plugin output)"
            ),
            path=str(gen_stimulus_py),
        )

    # ── 4. RTL introspection mode ──────────────────────────────────────────
    from forge.verify.rtl_introspection import _pyverilog_available, introspection_mode  # noqa: PLC0415
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
                "Install with: pip install -e \"framework/verify/python/[parser]\""
            ),
        )

    # ── 5. Simulator tool availability ─────────────────────────────────────
    for tool in ("xvlog", "xelab", "xsim"):
        if _shutil.which(tool):
            report.note("FWV000", f"Tool on PATH: {tool}")
        else:
            report.warn(
                "FWV012",
                f"XSIM tool not on PATH: {tool!r}",
                action=f"Source your Vivado setup script.  Required for xsim flows.",
            )

    # ── 6. Supported matrix + per-flow artifact checks ─────────────────────
    if contract is not None:
        from forge.verify.stimulus_contract import validate_stimulus
        from forge.verify.layout import resolve_generated_files
        from forge.verify.supported_matrix import validate_flow_matrix
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
                    action=f"forge verify generate {design_path} --flow {fn}",
                    flow=fn,
                    path=str(flow_yml),
                )

            # ── 6c. csim-specific: binary check ─────────────────────────
            if flow_decl.kind in _CSIM_KINDS:
                from forge.verify.backend_csim import find_tb_binary
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
                    action=f"forge verify generate {design_path} --flow {fn}",
                    flow=fn,
                    path=str(gen_files.tb_sv) if gen_files.tb_sv else "",
                )

            # port_map
            if gen_files.port_map.exists():
                try:
                    import yaml as _yaml
                    from forge.verify.gen_sim import _flatten_ports  # noqa: PLC0415
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
                    action=f"forge verify generate {design_path} --flow {fn}",
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
    from forge.verify.layout import validate_layout
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
    from forge.verify.supported_path_validator import validate_supported_path
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
            from forge.verify.stale_artifact import check_flow_staleness
            for flow_decl in contract.flows:
                stale_report = check_flow_staleness(
                    verify_root, flow_decl, contract, consumer_root
                )
                for s in stale_report.stale_artifacts:
                    report.warn(
                        "FWV016",
                        s.message(),
                        action=(
                            f"Re-run: forge verify generate {design_path} "
                            f"--flow {flow_decl.name}"
                        ),
                        flow=flow_decl.name,
                        path=str(s.artifact_path),
                    )
        except Exception:  # noqa: BLE001
            # Stale detection is advisory — never block on its failure
            pass

    return _doctor_emit(report, strict=strict, json_mode=json_mode)


def _doctor_emit(
    report: "DiagnosticReport",
    *,
    strict: bool,
    json_mode: bool,
) -> int:
    """Emit the doctor report and return an exit code."""
    if json_mode:
        print(report.to_json(strict=strict))
        return 0 if (report.ok_strict if strict else report.ok) else 1

    # Text mode — notes go to stdout, warnings/errors go to stderr
    report.print_console()
    print()
    print(report.summary_line(strict=strict))

    if strict and not report.ok_strict:
        print(
            f"\n[doctor --strict] {len(report.warnings)} warning(s) treated as "
            "errors because --strict is set.",
            file=sys.stderr,
        )
        return 1

    return 0 if report.ok else 1


# ── release-check command ──────────────────────────────────────────────────

def _cmd_release_check(args: argparse.Namespace) -> int:
    """Implement the ``release-check`` sub-command.

    Executes all RC-01…RC-10 release-readiness criteria in a single call and
    emits either a human-readable summary (default) or machine-readable JSON.

    Exit codes:
      0  All criteria pass.
      1  One or more criteria failed (or any warning when ``--strict``).
      2  Unexpected internal error.
    """
    from forge.verify.release_check import run_release_check

    design_path   = Path(args.design_file).resolve()
    consumer_root = _resolve_consumer_root(getattr(args, "consumer_root", None))
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


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="forge verify",
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
        from forge.verify.exceptions import ForgeVerifyError
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
