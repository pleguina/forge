"""Generating a flow's testbench files from ``design.verification.yml``.

:func:`generate_flows` writes ``verify.flow.yml``, ``tb_<module>.sv`` and
``wave.tcl`` for each declared flow; :func:`prepare_flows` adds the stimulus
contract and layout checks that make a flow ready to run.
"""
from __future__ import annotations

import sys
from pathlib import Path

from forge.verification.run import print_guided_error, resolve_consumer_root


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


def generate_flows(
    design_file: "str | Path",
    *,
    flow: "str | None" = None,
    consumer_root: "str | Path | None" = None,
    port_map: "str | Path | None" = None,
    dry_run: bool = False,
    use_kind_subdir: bool = False,
    strict: bool = False,
) -> int:
    """Read ``design.verification.yml``, iterates over declared flows, and generates
    ``verify.flow.yml``, ``tb_<module>.sv``, and ``wave.tcl`` for each matching flow.

    For xsim flows a ``port_map.yaml`` is required for port-aware TB generation.
    For hls_csim flows the TB is generated without DUT port connections (CSIM
    flows do not use an RTL testbench).
    """
    from forge.verification.design_contract import load_verify_design, VerifyDesignContract
    from forge.verification.gen_sim import generate_testbench, generate_verify_flow_yml
    from forge.verification.layout import canonical_flow_dir

    design_path = Path(design_file).resolve()
    if not design_path.exists():
        return print_guided_error(
            "design",
            FileNotFoundError(f"design file not found: {design_path}"),
            action="Check the design.verification.yml path and re-run forge dev verify generate.",
        )

    try:
        contract: VerifyDesignContract = load_verify_design(design_path)
    except Exception as exc:  # noqa: BLE001
        return print_guided_error(
            "design",
            exc,
            action="Fix design.verification.yml syntax or schema errors, then re-run forge dev verify generate.",
        )

    output_base = design_path.parent
    consumer_root = resolve_consumer_root(consumer_root) or Path.cwd()

    # Deprecation warning for --use-kind-subdir
    if use_kind_subdir:
        print(
            "WARNING: --use-kind-subdir is DEPRECATED (legacy/compatibility only).\n"
            "  This flag writes flows under <kind>/<flow_name>/ instead of the canonical\n"
            "  flat <flow_name>/ layout.  New plugins and new flows must use the flat layout.\n"
            "  forge dev verify doctor will report a layout violation for any kind-subdir trees.",
            file=sys.stderr,
        )

    # Resolve port map once (optional — only needed for xsim flows)
    cli_port_map: Path | None = None
    if port_map:
        cli_port_map = Path(port_map).resolve()
        if not cli_port_map.exists():
            print(f"ERROR: --port-map file not found: {cli_port_map}", file=sys.stderr)
            return 1

    # Filter flows
    flows = list(contract.flows)
    if flow:
        flows = [f for f in flows if f.name == flow]
        if not flows:
            names = [f.name for f in contract.flows]
            print(
                f"ERROR: flow {flow!r} not found in {design_path.name}.\n"
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
        if use_kind_subdir:
            flow_dir = output_base / flow_decl.kind / flow_decl.name
        else:
            flow_dir = canonical_flow_dir(output_base, flow_decl.name)

        # Resolve timing (flow overrides contract defaults)
        defaults = contract.defaults
        clk   = flow_decl.clk_period_ns              or defaults.clk_period_ns
        rst   = flow_decl.reset_cycles               or defaults.reset_cycles
        idle  = flow_decl.idle_cycles_after_reset    or defaults.idle_cycles_after_reset
        drain = flow_decl.post_stimulus_drain_cycles or defaults.post_stimulus_drain_cycles
        # Multi-clock-domain designs — empty dict for every pre-existing
        # single-clock flow.
        extra_clocks = getattr(flow_decl, "extra_clocks", None) or getattr(defaults, "extra_clocks", {}) or {}
        extra_resets = getattr(flow_decl, "extra_resets", None) or getattr(defaults, "extra_resets", {}) or {}

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
        if dry_run:
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
            # Check consumer_root/port_map.yaml (generated by forge dev topgen gen-top).
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
                    from forge.verification.rtl_introspection import (  # noqa: PLC0415
                        extract_and_write,
                        introspection_mode,
                        IntrospectionError,
                    )
                    pm_path = flow_dir / "port_map.yaml"
                    if dry_run:
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
            if dry_run:
                print(
                    f"[dry-run] would skip TB/wave for {flow_decl.name}"
                    f" — RTL not available ({rtl_hint})"
                )
            elif strict:
                errors.append(
                    f"{flow_decl.name}: [strict] RTL not available and --strict is set.\n"
                    f"  RTL path: {rtl_hint}\n"
                    f"  → Run HLS synthesis first, then re-run forge dev verify generate"
                )
            else:
                errors.append(
                    f"{flow_decl.name}: skipped TB/wave — port_map.yaml not found and"
                    f" RTL not available ({rtl_hint})\n"
                    f"  → Run HLS synthesis first, then re-run forge dev verify generate"
                )
            continue

        generated_port_map = flow_dir / "port_map.yaml"
        if not dry_run and tb_port_map.resolve() != generated_port_map.resolve():
            import shutil as _shutil  # noqa: PLC0415
            flow_dir.mkdir(parents=True, exist_ok=True)
            _shutil.copyfile(tb_port_map, generated_port_map)
            generated.append(str(generated_port_map))
            tb_port_map = generated_port_map

        if dry_run:
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
                extra_clocks=extra_clocks,
                extra_resets=extra_resets,
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

    if not dry_run:
        print(f"\nGenerated {len(generated)} file(s).")
    return 0


def prepare_flows(
    design_file: "str | Path",
    *,
    flow: "str | None" = None,
    consumer_root: "str | Path | None" = None,
    port_map: "str | Path | None" = None,
    dry_run: bool = False,
    strict: bool = False,
) -> int:
    """Make the flows of *design_file* ready to run:
      1. forge dev verify generate  — write verify.flow.yml + TB + wave.tcl
      2. stimulus contract validation — check stimulus_current.svh for all
         generated xsim flows that already have a stimulus file
      3. layout validation — verify canonical flat layout
    """
    from forge.verification.design_contract import load_verify_design

    design_path = Path(design_file).resolve()
    if not design_path.exists():
        return print_guided_error(
            "prepare",
            FileNotFoundError(f"design file not found: {design_path}"),
            action="Check the design.verification.yml path and re-run forge dev verify prepare.",
        )

    # ── Step 1: Generate artifacts ─────────────────────────────────────────
    print("[prepare] Step 1/3: Generating artifacts …")
    rc = generate_flows(
        design_path, flow=flow, consumer_root=consumer_root, port_map=port_map,
        dry_run=dry_run, strict=strict,
    )
    if rc != 0:
        print("[prepare] Step 1/3 FAILED — stopping.", file=sys.stderr)
        return rc
    print("[prepare] Step 1/3 OK")

    if dry_run:
        print("[prepare] dry-run: skipping stimulus and layout checks.")
        return 0

    # ── Step 2: Stimulus contract validation ───────────────────────────────
    from forge.verification.stimulus_contract import validate_stimulus

    try:
        contract = load_verify_design(design_path)
    except Exception as exc:  # noqa: BLE001
        return print_guided_error(
            "prepare/contract",
            exc,
            action="Fix design.verification.yml before running forge dev verify prepare again.",
        )

    output_base = design_path.parent
    flow_filter = flow
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
    from forge.verification.layout import validate_layout

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
