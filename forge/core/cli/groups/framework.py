"""forge framework — external framework import, detector I/O resolution, floorplan and ipbb."""

from __future__ import annotations

import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

def cmd_import(args):
    """Load and validate a Blobfish (or other) framework ABI + endpoint manifest."""
    from forge.integration.importer import load, FrameworkImportError

    abi_path = Path(args.abi)
    ep_path  = Path(args.endpoints)
    provider = args.provider

    try:
        fi = load(provider=provider, abi_path=abi_path, ep_path=ep_path)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except FrameworkImportError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Serialise the import summary
    summary = {
        "provider":    fi.provider,
        "project":     fi.project,
        "board":       fi.board,
        "module_name": fi.module_name,
        "abi_path":    str(fi.abi_path),
        "ep_path":     str(fi.ep_path),
        "port_count":  len(fi.ports),
        "endpoint_count": len(fi.endpoints),
        "rx_endpoints": [e.endpoint_id for e in fi.rx_endpoints()],
        "tx_endpoints": [e.endpoint_id for e in fi.tx_endpoints()],
    }
    out_file = out_dir / "framework_import.json"
    with out_file.open("w") as fh:
        json.dump(summary, fh, indent=2)
        fh.write("\n")

    print(
        f"Framework import OK: provider={fi.provider} board={fi.board} "
        f"ports={len(fi.ports)} endpoints={len(fi.endpoints)}"
    )
    print(f"Written: {out_file}")


def cmd_io_resolve(args):
    """Resolve detector_io.yml against a framework import."""
    from forge.integration.importer import load, FrameworkImportError
    from forge.integration.io_resolver import resolve, write_resolved, DetectorIOError

    # --- Load framework import ---
    import_path = Path(args.framework) / "framework_import.json"
    if not import_path.exists():
        print(f"ERROR: framework_import.json not found in {args.framework}", file=sys.stderr)
        sys.exit(1)

    with import_path.open() as fh:
        imp_meta = json.load(fh)

    try:
        fi = load(
            provider = imp_meta["provider"],
            abi_path = Path(imp_meta["abi_path"]),
            ep_path  = Path(imp_meta["ep_path"]),
        )
    except (FrameworkImportError, FileNotFoundError) as exc:
        print(f"ERROR loading framework: {exc}", file=sys.stderr)
        sys.exit(1)

    # --- Optional: load module registry for frontend validation ---
    registry_modules: set | None = None
    if args.plugin:
        modules_yml = Path(args.plugin) / "modules.yml"
        if modules_yml.exists():
            try:
                import yaml
                with modules_yml.open() as fh:
                    reg = yaml.safe_load(fh)
                registry_modules = {m["name"] for m in reg.get("modules", [])}
            except Exception as exc:
                print(f"WARNING: could not load modules.yml: {exc}", file=sys.stderr)

    detector_io_path = Path(args.detector_io)
    try:
        resolved = resolve(
            detector_io_path = detector_io_path,
            framework        = fi,
            registry_modules = registry_modules,
        )
    except (DetectorIOError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "detector_io.resolved.json"
    write_resolved(resolved, out_file)

    n_in  = len(resolved.inputs)
    n_out = len(resolved.outputs)
    print(f"Detector I/O resolved: {n_in} inputs, {n_out} outputs")
    print(f"Written: {out_file}")


def cmd_emit_payload(args):
    """Generate a Blobfish-compatible payload.v from resolved I/O."""
    import json as _json
    from forge.integration.importer import load, FrameworkImportError
    from forge.integration.payload_generator import (
        generate_payload_verilog, ControlPolicies,
    )

    # Load framework import
    import_path = Path(args.framework) / "framework_import.json"
    if not import_path.exists():
        print(f"ERROR: framework_import.json not found in {args.framework}", file=sys.stderr)
        sys.exit(1)

    with import_path.open() as fh:
        imp_meta = _json.load(fh)

    try:
        fi = load(
            provider = imp_meta["provider"],
            abi_path = Path(imp_meta["abi_path"]),
            ep_path  = Path(imp_meta["ep_path"]),
        )
    except (FrameworkImportError, FileNotFoundError) as exc:
        print(f"ERROR loading framework: {exc}", file=sys.stderr)
        sys.exit(1)

    # Load resolved I/O (optional — produces dummy stub if absent)
    resolved_io = None
    if args.detector_io:
        resolved_io_path = Path(args.detector_io)
        if not resolved_io_path.exists():
            print(f"ERROR: detector_io.resolved.json not found: {resolved_io_path}",
                  file=sys.stderr)
            sys.exit(1)
        # Reconstruct a minimal DetectorIOResolved from the JSON for port wiring
        from forge.integration.io_resolver import (
            DetectorIOResolved, ResolvedInput, ResolvedOutput
        )
        with resolved_io_path.open() as fh:
            rio_data = _json.load(fh)

        def _ep_from_dict(d):
            from forge.integration.importer import Endpoint
            # The framework's own endpoint carries what a resolved file
            # written before `protocol` was recorded does not.
            known = fi.get_endpoint(d["endpoint_id"])
            if known is not None:
                return known
            return Endpoint(
                endpoint_id          = d["endpoint_id"],
                direction            = d["direction"],
                slr                  = d["slr"],
                gt_site              = d["gt_site"],
                lane                 = d["lane"],
                lane_width           = d["lane_width"],
                protocol             = d.get("protocol", ""),
                quad_type            = "",
                cage                 = None,
                fiber                = None,
                polarity             = 0,
                link_function        = d.get("link_function", ""),
                endpoint_role        = d.get("endpoint_role", ""),
                include_in_framework = True,
                abi_port_prefix      = d.get("abi_port_prefix"),
            )

        inputs = [
            ResolvedInput(
                name              = r["name"],
                detector          = r["detector"],
                blobfish_endpoint = r["blobfish_endpoint"],
                endpoint          = _ep_from_dict(r["endpoint"]),
                detector_object   = r.get("detector_object", {}),
                frontend_module   = r["frontend"]["module"],
                frontend_instance = r["frontend"]["instance"],
                frontend_params   = r["frontend"]["parameters"],
                output_wiring     = r.get("output", {}),
            )
            for r in rio_data.get("detector_inputs", [])
        ]
        outputs = [
            ResolvedOutput(
                name              = r["name"],
                blobfish_endpoint = r["blobfish_endpoint"],
                endpoint          = _ep_from_dict(r["endpoint"]),
                source_instance   = r["source"]["instance"],
                source_port       = r["source"]["port"],
                wiring_kind       = r.get("wiring_kind", ""),
            )
            for r in rio_data.get("trigger_outputs", [])
        ]
        resolved_io = DetectorIOResolved(inputs=inputs, outputs=outputs)

    # Load policies
    policies = ControlPolicies()
    if args.policies:
        try:
            import yaml
            with open(args.policies) as fh:
                pd = yaml.safe_load(fh)
            policies = ControlPolicies.from_dict(
                pd.get("control_policies", {}), base=Path(args.policies).parent,
            )
        except Exception as exc:
            print(f"ERROR: could not load policies file {args.policies}: {exc}", file=sys.stderr)
            sys.exit(1)

    out_path = Path(args.out) if args.out else Path("build/generated/payload.v")
    try:
        content = generate_payload_verilog(
            framework   = fi,
            resolved_io = resolved_io,
            algo_module = args.algo_module,
            policies    = policies,
            output_path = out_path,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Generated payload wrapper: {out_path}")
    print(f"  ports: {len(fi.ports)}")
    raw = policies.raw_entries()
    if any(raw.values()):
        print(f"  unchecked by FORGE: {raw['drive']} drive entries, {raw['verilog']} Verilog blocks")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def cmd_ipbb_component(args):
    """Write a build's compile list as an ipbb component."""
    from forge.integration.ipbb import write_component

    try:
        dep = write_component(Path(args.manifest), Path(args.out), args.dep)
    except (OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Written: {dep}")


def cmd_ipbb_xsim(args):
    """Write the XSim compile commands for an ipbb dep tree."""
    from forge.integration.ipbb import xsim_compile_script

    try:
        script = xsim_compile_script(Path(args.src_dir), args.component, args.dep)
    except (ImportError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    Path(args.out).write_text(script)
    print(f"Written: {args.out}")


def cmd_floorplan(args):
    """Write the SLR assignment of a block-design top level."""
    import yaml
    from forge.integration.floorplan import cell_modules, floorplan, write_xdc
    from forge.integration.floorplan_report import check_tcl, hls_resources, proposal

    try:
        spec = (yaml.safe_load(Path(args.spec).read_text()) or {}).get("floorplan") or {}
        if "root" not in spec:
            raise ValueError(f"{args.spec}: floorplan.root (the block-design instance in the netlist) is required")
        res, cells, adj, registered = floorplan(Path(args.block_design), Path(args.ir), Path(args.detector_io),
                                                spec, Path(args.crossings) if args.crossings else None)
        report = None
        if args.report:
            resources = None
            if args.resources:
                resources = hls_resources(json.loads(Path(args.resources).read_text()),
                                          cell_modules(Path(args.block_design).read_text()))
            report = proposal(res, cells, adj, spec, registered=registered, resources=resources)
    except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(write_xdc(res, root=spec["root"], pblock=spec.get("pblock")))
    per_slr = {s: sum(1 for v in res.values() if v == s) for s in sorted(set(res.values()))}
    print(f"Written: {out} ({len(res)} of {len(cells)} cells: "
          + ", ".join(f"SLR{s} {n}" for s, n in per_slr.items()) + ")")
    if report is not None:
        Path(args.report).write_text(json.dumps(report, indent=2) + "\n")
        check = Path(args.report).with_suffix(".check.tcl")
        check.write_text(check_tcl(res, root=spec["root"], out_csv="floorplan_placed.csv"))
        print(f"Written: {args.report} (placement proposal), {check} (run after place_design)")
        for issue in report["issues"]:
            print(f"  {issue['level'].upper()}: {issue['message']}")
        if any(i["level"] == "error" for i in report["issues"]):
            sys.exit(1)


def cmd_floorplan_compare(args):
    """Compare a floorplan proposal with the placed design and its timing reports."""
    from forge.integration.floorplan_report import compare

    try:
        report = json.loads(Path(args.report).read_text())
        result = compare(report, Path(args.placed),
                         summary=Path(args.timing_summary).read_text() if args.timing_summary else None,
                         paths=Path(args.timing_paths).read_text() if args.timing_paths else None)
    except (OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    line = f"{result['placed_as_proposed']} of {result['cells']} cells placed as proposed"
    if "timing" in result:
        line += f", WNS {result['timing']['wns_ns']} ns, TNS {result['timing']['tns_ns']} ns"
    print(f"Written: {args.out} ({line})")


def cmd_emp_payload(args):
    """Write emp_payload.vhd for an emp-fwk checkout from a spec."""
    from forge.integration.emp_payload import write

    try:
        release = write(Path(args.spec), Path(args.emp_fwk), Path(args.ir), Path(args.out))
    except (OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Written: {args.out} (emp-fwk {release.version or 'checkout without a release tag'})")


def cmd_registers(args):
    """Export a register map: IPbus address table, Blobfish payload services, Markdown."""
    import yaml
    from forge.integration.registers import RegisterMap, to_blobfish, to_ipbus_xml, to_markdown

    try:
        regs = RegisterMap.from_dict(yaml.safe_load(Path(args.map).read_text()) or {})
        outputs = {}
        if args.ipbus:
            outputs[args.ipbus] = to_ipbus_xml(regs, top=args.top)
        if args.blobfish:
            if args.slr is None:
                raise ValueError("--blobfish needs --slr")
            outputs[args.blobfish] = ("# Written by forge dev framework registers; do not edit.\n"
                                      + yaml.safe_dump(to_blobfish(regs, slr=args.slr), sort_keys=False))
        if args.doc:
            outputs[args.doc] = to_markdown(regs)
    except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    if not outputs:
        print("ERROR: nothing to write: give --ipbus, --blobfish or --doc", file=sys.stderr)
        sys.exit(1)
    for path, text in outputs.items():
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text)
        print(f"Written: {path}")


def cmd_link_map(args):
    """Write the link map and build identity of a payload."""
    import yaml
    from forge.integration.link_map import build_identity, link_map
    from forge.integration.registers import RegisterMap

    try:
        files = {}
        for item in args.file or []:
            label, sep, path = item.partition("=")
            if not sep:
                raise ValueError(f"--file {item}: want label=path")
            files[label] = Path(path)
        files.setdefault("detector_io", Path(args.detector_io))
        identity = build_identity(files, Path(args.repo) if args.repo else None)
        regs = RegisterMap.from_dict(yaml.safe_load(Path(args.registers).read_text()) or {}) if args.registers else None
        doc = link_map(json.loads(Path(args.detector_io).read_text()), identity, clock=args.clock, registers=regs)
    except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(doc, indent=2) + "\n")
    print(f"Written: {args.out} ({len(doc['links'])} links, build {identity['build_id32']})")


def register(sub):
    """Register the 'framework' command group."""
    grp = sub.add_parser(
        "framework",
        help="External frameworks: ABI import, detector I/O, payloads, floorplan, ipbb components",
    )
    grp_sub = grp.add_subparsers(
        dest="framework_cmd",
        required=True,
        metavar="COMMAND",
        help="framework sub-command",
    )

    # ── framework import ──────────────────────────────────────────────────
    p_import = grp_sub.add_parser(
        "import",
        help="Load and validate a framework ABI + endpoint manifest",
    )
    p_import.add_argument(
        "--provider", required=True,
        help="Framework provider name (e.g. blobfish)",
    )
    p_import.add_argument(
        "--abi", required=True,
        help="Path to payload_abi.json",
    )
    p_import.add_argument(
        "--endpoints", required=True,
        help="Path to payload_endpoints.json",
    )
    p_import.add_argument(
        "--out", required=True,
        help="Output directory for framework_import.json",
    )
    p_import.set_defaults(func=cmd_import)

    # ── framework io-resolve ──────────────────────────────────────────────
    p_resolve = grp_sub.add_parser(
        "io-resolve",
        help="Resolve detector_io.yml against a framework import",
    )
    p_resolve.add_argument(
        "--framework", required=True,
        help="Directory containing framework_import.json (output of 'framework import')",
    )
    p_resolve.add_argument(
        "--detector-io", required=True,
        help="Path to detector_io.yml",
    )
    p_resolve.add_argument(
        "--plugin", default=None,
        help="Path to plugin directory (for modules.yml registry validation)",
    )
    p_resolve.add_argument(
        "--out", required=True,
        help="Output directory for detector_io.resolved.json",
    )
    p_resolve.set_defaults(func=cmd_io_resolve)

    # ── framework emit-payload ────────────────────────────────────────────
    p_emit = grp_sub.add_parser(
        "emit-payload",
        help="Generate a Blobfish-compatible payload.v",
    )
    p_emit.add_argument(
        "--framework", required=True,
        help="Directory containing framework_import.json",
    )
    p_emit.add_argument(
        "--detector-io", default=None,
        help="Path to detector_io.resolved.json (omit for dummy stub)",
    )
    p_emit.add_argument(
        "--algo-module", default="arc_algo_top",
        help="Algorithm top module name to instantiate",
    )
    p_emit.add_argument(
        "--policies", default=None,
        help="Path to YAML file with control_policies",
    )
    p_emit.add_argument(
        "--out", default=None,
        help="Output path for payload.v (default: build/generated/payload.v)",
    )
    p_emit.set_defaults(func=cmd_emit_payload)

    # ── framework ipbb-component ──────────────────────────────────────────
    p_ipbb = grp_sub.add_parser(
        "ipbb-component",
        help="Copy a build's sources into an ipbb component with its .dep file",
    )
    p_ipbb.add_argument(
        "--manifest", required=True,
        help="build_manifest.json written next to the generated top",
    )
    p_ipbb.add_argument(
        "--out", required=True,
        help="Component directory (gets firmware/hdl and firmware/cfg)",
    )
    p_ipbb.add_argument(
        "--dep", default=None,
        help="Dep file name (default: <top>.dep)",
    )
    p_ipbb.set_defaults(func=cmd_ipbb_component)

    # ── framework ipbb-xsim ───────────────────────────────────────────────
    p_xsim = grp_sub.add_parser(
        "ipbb-xsim",
        help="Write xvhdl/xvlog commands compiling an ipbb dep tree (needs ipbb)",
    )
    p_xsim.add_argument(
        "--src-dir", required=True,
        help="The ipbb work area's src/ directory",
    )
    p_xsim.add_argument(
        "--component", required=True,
        help="package:component holding the top-level dep file",
    )
    p_xsim.add_argument(
        "--dep", required=True,
        help="Top-level dep file name",
    )
    p_xsim.add_argument(
        "--out", required=True,
        help="Shell script to write",
    )
    p_xsim.set_defaults(func=cmd_ipbb_xsim)

    # ── framework floorplan ───────────────────────────────────────────────
    p_fp = grp_sub.add_parser(
        "floorplan",
        help="SLR assignment of a block-design top level from the links and a floorplan spec",
    )
    p_fp.add_argument("--block-design", required=True, help="block_design.tcl from forge build --mode bd")
    p_fp.add_argument("--ir", required=True, help="design.ir.json written next to it")
    p_fp.add_argument("--crossings", default=None,
                      help="block_design.crossings.json; its crossings are left to the placer")
    p_fp.add_argument("--detector-io", required=True, help="detector_io.resolved.json")
    p_fp.add_argument("--spec", required=True,
                      help="YAML with floorplan: {root, pblock, reserved, slr: {<slr>: [cell patterns]}}")
    p_fp.add_argument("--out", required=True, help="XDC to write")
    p_fp.add_argument("--report", default=None,
                      help="JSON report of the proposal (per-SLR load, crossings, spec errors) to write, "
                           "with a .check.tcl next to it for after place_design")
    p_fp.add_argument("--resources", default=None,
                      help="HLS metrics JSON (modules: {name: {resources: {used}}}) for the per-SLR load")
    p_fp.set_defaults(func=cmd_floorplan)

    p_fpc = grp_sub.add_parser(
        "floorplan-compare",
        help="A floorplan proposal against the placed design and Vivado's timing reports",
    )
    p_fpc.add_argument("--report", required=True, help="The proposal's JSON report (floorplan --report)")
    p_fpc.add_argument("--placed", required=True, help="floorplan_placed.csv written by the .check.tcl")
    p_fpc.add_argument("--timing-summary", default=None, help="report_timing_summary output")
    p_fpc.add_argument("--timing-paths", default=None, help="report_timing output (many paths)")
    p_fpc.add_argument("--out", required=True, help="JSON comparison to write")
    p_fpc.set_defaults(func=cmd_floorplan_compare)

    # ── framework emp-payload ─────────────────────────────────────────────
    p_emp = grp_sub.add_parser(
        "emp-payload",
        help="emp_payload.vhd around the generated top, for an emp-fwk checkout",
    )
    p_emp.add_argument("--spec", required=True, help="YAML with an emp_payload mapping (see forge.integration.emp_payload)")
    p_emp.add_argument("--emp-fwk", required=True, help="emp-fwk checkout whose payload entity is used")
    p_emp.add_argument("--ir", required=True, help="design.ir.json of the generated top")
    p_emp.add_argument("--out", required=True, help="VHDL file to write")
    p_emp.set_defaults(func=cmd_emp_payload)

    # ── framework registers ───────────────────────────────────────────────
    p_reg = grp_sub.add_parser(
        "registers",
        help="Export a register map: IPbus address table, Blobfish payload services, Markdown",
    )
    p_reg.add_argument("--map", required=True, help="YAML register map (see forge.integration.registers)")
    p_reg.add_argument("--ipbus", default=None, help="uHAL address table XML to write")
    p_reg.add_argument("--top", default="payload", help="Top node id of the address table")
    p_reg.add_argument("--blobfish", default=None,
                       help="YAML with the control_policies placing the map in an SLR's config/readback words")
    p_reg.add_argument("--slr", type=int, default=None, help="SLR whose words --blobfish uses")
    p_reg.add_argument("--doc", default=None, help="Markdown register table to write")
    p_reg.set_defaults(func=cmd_registers)

    # ── framework link-map ────────────────────────────────────────────────
    p_lm = grp_sub.add_parser(
        "link-map",
        help="JSON of every link, its algorithm port and the register layout, with the build's identity",
    )
    p_lm.add_argument("--detector-io", required=True, help="detector_io.resolved.json")
    p_lm.add_argument("--file", action="append", metavar="LABEL=PATH",
                      help="A file the build is made of (IR, payload, bitstream, dataset, mapping); repeatable")
    p_lm.add_argument("--repo", default=None, help="Source repository whose commit identifies the build")
    p_lm.add_argument("--registers", default=None, help="Register map YAML whose layout to include")
    p_lm.add_argument("--clock", default=None, help="Clock the link words are handed over in")
    p_lm.add_argument("--out", required=True, help="JSON to write")
    p_lm.set_defaults(func=cmd_link_map)
