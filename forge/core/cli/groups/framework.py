"""forge framework — external framework import, detector I/O resolution and ipbb."""

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


def register(sub):
    """Register the 'framework' command group."""
    grp = sub.add_parser(
        "framework",
        help="External frameworks: ABI import, detector I/O, payloads, ipbb components",
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
