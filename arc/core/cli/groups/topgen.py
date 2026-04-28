"""arc topgen — hardware topology generation commands."""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

from arc.topgen.config import DesignConfig
from arc.topgen.ip.unpacker import unpack_ip_archives
from arc.topgen.ip.parser import collect_all, write_summary
from arc.topgen.ip.matcher import load_ip_info, auto_match_ports
from arc.topgen.ip.contract_loader import load_contracts_for_design, synthesize_ip_info
from arc.topgen.generators.structural_vhdl import write_structural_vhdl
from arc.topgen.generators.structural_verilog import write_structural_verilog
from arc.topgen.generators.block_design import write_bd_tcl
from arc.topgen.generators.sv_testbench_generator import generate_sv_testbench
from arc.topgen.generators.design_parameters import write_design_parameters
from arc.topgen.validation import validate_design, validate_registry
from arc.core.diagnostics import ATGDiagnosticReport
from arc.core.stale_detection import (
    check_top_gen_staleness,
    check_ip_info_staleness,
    format_stale_report,
)
from arc.core.cli._shared import (
    print_cli_error,
    consumer_root as _consumer_root,
    resolve_path as _resolve_path,
    remove_path as _remove_path,
    match_patterns as _match_patterns,
    sort_entries as _sort_entries,
    resolve_interface_metadata as _resolve_interface_metadata,
    load_verify_flow_entries as _load_verify_flow_entries,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _filter_unaccounted(port_list, patterns):
    """Return entries from *port_list* not matched by any fnmatch *pattern*.

    *port_list* is a list of ``(instance, port, width)`` tuples.
    *patterns* is a list of ``instance.port`` glob patterns.
    Returns the subset of *port_list* that no pattern covers.
    """
    from fnmatch import fnmatch
    unaccounted = []
    for inst, port, width in port_list:
        key = f"{inst}.{port}"
        if not any(fnmatch(key, pat) for pat in patterns):
            unaccounted.append((inst, port, width))
    return unaccounted


def validate_generated_contracts(
    port_map: dict,
    probe_map: dict,
    tb_bindings_path: Path,
    design_parameters_path: Path,
) -> None:
    """Validate that generated contract artifacts are aligned with the same DUT shape."""
    if not port_map:
        raise ValueError("port_map.yaml was not generated; cannot validate generated contracts")
    if not probe_map:
        raise ValueError("probe_map.yaml was not generated; cannot validate generated contracts")
    if not tb_bindings_path.exists():
        raise FileNotFoundError(f"tb_bindings.svh not found at {tb_bindings_path}")
    if not design_parameters_path.exists():
        raise FileNotFoundError(f"design_parameters.json not found at {design_parameters_path}")

    port_groups = port_map.get("port_groups", {})
    port_sig_hash = port_map.get("port_signature_hash")
    if not port_sig_hash:
        raise ValueError("port_map.yaml is missing port_signature_hash")

    probe_sig_hash = probe_map.get("port_signature_hash")
    if probe_sig_hash != port_sig_hash:
        raise ValueError(
            f"probe_map.yaml hash {probe_sig_hash!r} does not match "
            f"port_map.yaml hash {port_sig_hash!r}"
        )

    tb_bindings_text = tb_bindings_path.read_text()
    if f"// Port sig hash: {port_sig_hash}" not in tb_bindings_text:
        raise ValueError(
            f"tb_bindings.svh at {tb_bindings_path} does not match "
            f"port_map.yaml hash {port_sig_hash}"
        )

    for group_name, group_data in port_groups.items():
        if group_name in {"clock_reset", "bx0", "outputs", "unclassified"}:
            entries = group_data if isinstance(group_data, list) else []
        elif isinstance(group_data, dict) and "channels" in group_data:
            entries = group_data.get("channels", [])
        elif isinstance(group_data, dict) and "ports" in group_data:
            entries = list(group_data.get("ports", []))
            for sub_grp in group_data.get("groups", []):
                entries = entries + sub_grp.get("ports", [])
        else:
            continue

        for entry in entries:
            name = entry.get("name")
            if name and name not in tb_bindings_text:
                raise ValueError(
                    f"tb_bindings.svh is missing the generated declaration for port {name}"
                )

    design_params = json.loads(design_parameters_path.read_text())
    interfaces = design_params.get("interfaces", {})
    interface_groups_params = interfaces.get("input_groups") or interfaces.get("groups", {})
    for group_name, group_data in port_groups.items():
        if group_name in ("clock_reset", "bx0", "outputs", "unclassified"):
            continue
        if not isinstance(group_data, dict):
            continue
        port_map_count = group_data.get("count", 0)
        observed_count = interface_groups_params.get(group_name, {}).get("count")
        if observed_count is not None and observed_count != port_map_count:
            raise ValueError(
                f"design_parameters.json interfaces.groups.{group_name}.count="
                f"{observed_count!r} does not match generated port_map count {port_map_count}"
            )

    print("  ✓ Generated contracts validated")
    print(f"    - Port signature hash: {port_sig_hash}")
    print(
        "    - port_map.yaml / probe_map.yaml / "
        "tb_bindings.svh / design_parameters.json are aligned"
    )


def generate_build_manifest(
    cfg,
    ip_info,
    ip_root,
    algo_top,
    design_file,
    manifest_output,
    *,
    project_root: Path | None = None,
    hls_build_root: Path | None = None,
):
    """Generate the simulation build manifest with all Verilog source paths."""
    import datetime

    def find_ip_verilog_dir(top_name):
        top_file_matches = sorted(ip_root.rglob(f"{top_name}.v"))
        for top_file in top_file_matches:
            if top_file.parent.is_dir():
                return top_file.parent, top_file.parent.parent.parent
        return None, None

    manifest = {
        "project_root": str((project_root or algo_top.parent).resolve()),
        "design_file": str(design_file.resolve()),
        "algorithm_top": str(algo_top.resolve()),
        "top_module": algo_top.stem,
        "timestamp": datetime.datetime.now().isoformat(),
        "verilog_files": [],
        "include_dirs": [],
        "modules": {},
    }

    manifest["verilog_files"].append(str(algo_top.resolve()))
    manifest["include_dirs"].append(str(algo_top.parent.resolve()))

    if hls_build_root is not None:
        manifest["hls_build_root"] = str(hls_build_root.resolve())

    for module in cfg.modules:
        module_name = module.name
        module_info = {
            "name": module_name,
            "top": module.top,
            "kind": module.kind,
            "verilog_files": [],
            "include_dirs": [],
        }

        verilog_dir = None

        # RTL modules
        if module.kind == "rtl" and hasattr(module, "src") and module.src:
            rtl_files = []
            rtl_sources = (
                module.abs_src
                if getattr(module, "abs_src", None)
                else [Path(src) for src in module.src]
            )
            for src_path in rtl_sources:
                src_path = Path(src_path)
                if src_path.exists():
                    rtl_files.append(str(src_path.resolve()))
                    if src_path.parent not in [Path(d) for d in module_info["include_dirs"]]:
                        module_info["include_dirs"].append(str(src_path.parent.resolve()))

            if hasattr(module, "rtl_packages") and module.rtl_packages:
                rtl_packages = (
                    module.abs_rtl_packages
                    if getattr(module, "abs_rtl_packages", None)
                    else [Path(pkg) for pkg in module.rtl_packages]
                )
                for pkg_path in rtl_packages:
                    pkg_path = Path(pkg_path)
                    if pkg_path.exists():
                        rtl_files.append(str(pkg_path.resolve()))
                        if pkg_path.parent not in [Path(d) for d in module_info["include_dirs"]]:
                            module_info["include_dirs"].append(str(pkg_path.parent.resolve()))

            if rtl_files:
                module_info["verilog_files"] = rtl_files
                module_info["rtl_sources"] = rtl_files
                manifest["verilog_files"].extend(rtl_files)
                manifest["include_dirs"].extend(module_info["include_dirs"])

        # HLS modules
        elif module_name in ip_info:
            verilog_dir, ip_dir = find_ip_verilog_dir(module.top)
            if verilog_dir and ip_dir:
                module_info["ip_dir"] = str(ip_dir.resolve())

            ip_dir = ip_root / f"{module.top}_ip"

            if not verilog_dir and ip_dir.exists() and ip_dir.is_dir():
                verilog_candidates = [
                    ip_dir / "hdl" / "verilog",
                    ip_dir / "hdl",
                    ip_dir / "src",
                    ip_dir / "verilog",
                ]
                for vdir in verilog_candidates:
                    if vdir.exists() and vdir.is_dir():
                        verilog_dir = vdir
                        module_info["ip_dir"] = str(ip_dir.resolve())
                        break

            if not verilog_dir and hls_build_root is not None:
                build_candidates = [
                    hls_build_root / "build_hls" / module_name / "solution1" / "syn" / "verilog",
                    hls_build_root / "build_hls" / module_name / "solution1" / "sim" / "verilog",
                    hls_build_root / module_name / "solution1" / "syn" / "verilog",
                    hls_build_root / module_name / "solution1" / "sim" / "verilog",
                ]
                for candidate in build_candidates:
                    if candidate.exists() and candidate.is_dir():
                        verilog_dir = candidate
                        module_info["hls_solution_dir"] = str(
                            candidate.parent.parent.resolve()
                        )
                        break

            if verilog_dir:
                verilog_dirs = [verilog_dir]
                ip_wrapper_dir = verilog_dir.parent / "ip"
                if ip_wrapper_dir.exists() and ip_wrapper_dir.is_dir():
                    verilog_dirs.append(ip_wrapper_dir)

                v_files = []
                include_dirs = []
                for source_dir in verilog_dirs:
                    source_files = [
                        f
                        for f in source_dir.glob("*.v")
                        if "autotb" not in f.name and f.name != "glbl.v"
                    ]
                    v_files.extend(source_files)
                    include_dirs.append(str(source_dir.resolve()))

                module_info["verilog_files"] = [str(f.resolve()) for f in v_files]
                module_info["include_dirs"] = include_dirs
                module_info["verilog_dir"] = str(verilog_dir.resolve())

                manifest["verilog_files"].extend(module_info["verilog_files"])
                manifest["include_dirs"].extend(include_dirs)

        manifest["modules"][module_name] = module_info

    manifest["verilog_files"] = list(dict.fromkeys(manifest["verilog_files"]))
    manifest["include_dirs"] = list(dict.fromkeys(manifest["include_dirs"]))

    manifest["statistics"] = {
        "total_verilog_files": len(manifest["verilog_files"]),
        "total_include_dirs": len(manifest["include_dirs"]),
        "total_modules": len(manifest["modules"]),
        "hls_modules": sum(1 for m in manifest["modules"].values() if m["kind"] == "hls"),
        "rtl_modules": sum(1 for m in manifest["modules"].values() if m["kind"] == "rtl"),
    }

    with open(manifest_output, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"  ✓ Build manifest: {manifest_output}")
    print(f"    - {manifest['statistics']['total_verilog_files']} Verilog files")
    print(
        f"    - {manifest['statistics']['total_modules']} modules "
        f"({manifest['statistics']['hls_modules']} HLS + "
        f"{manifest['statistics']['rtl_modules']} RTL)"
    )


def generate_port_map(
    verilog_path: Path,
    output_path: Path,
    interface_metadata: dict | None = None,
):
    """Scan the generated algo_top.v port list and write port_map.yaml."""
    import yaml as _yaml
    from arc.core.utils.hdl_parser import _scan_verilog_ports
    from arc.core.utils import port_signature as _sig

    ports = _scan_verilog_ports(verilog_path)
    if not ports:
        print(f"  ⚠  Could not parse ports from {verilog_path} — port_map.yaml not written")
        return None, None

    metadata = _resolve_interface_metadata(interface_metadata)
    clock_reset, bx0 = [], []
    outputs, unclassified = [], []

    input_groups = metadata.get("input_groups", {})
    grouped_inputs: dict[str, list[dict]] = {
        group_name: []
        for group_name in input_groups.keys()
        if not input_groups.get(group_name, {}).get("groups")
    }
    grouped_style_cfg: dict[str, list] = {
        name: cfg["groups"]
        for name, cfg in input_groups.items()
        if cfg.get("groups")
    }
    grouped_input_defs = [gd for sub_defs in grouped_style_cfg.values() for gd in sub_defs]
    grouped_style_list = [
        {
            "name": group_def["name"],
            "stimulus_prefix": group_def.get("stimulus_prefix"),
            "sort": group_def.get("sort", "numeric_suffix"),
            "ports": [],
        }
        for group_def in grouped_input_defs
    ]

    def _port_width(name: str, default: int = 0) -> int:
        port = ports.get(name)
        return port[1] if port else default

    for name, (direction, width) in sorted(ports.items()):
        entry = {
            "name": name,
            "direction": "input" if direction == "in" else "output",
            "width": width,
        }
        if name in metadata.get("clock_reset_ports", []):
            clock_reset.append(entry)
        elif name in metadata.get("bx0_ports", []):
            bx0.append(entry)
        elif direction == "out":
            outputs.append(entry)
        else:
            assigned = False
            for grouped_input, group_def in zip(grouped_style_list, grouped_input_defs):
                if _match_patterns(name, group_def.get("match", [])):
                    grouped_input["ports"].append(entry)
                    assigned = True
                    break
            if assigned:
                continue
            for group_name, group_cfg in input_groups.items():
                if group_cfg.get("groups"):
                    continue
                if _match_patterns(name, group_cfg.get("match", [])):
                    grouped_inputs[group_name].append(entry)
                    assigned = True
                    break
            if not assigned:
                unclassified.append(entry)

    for group_name, group_entries in grouped_inputs.items():
        grouped_inputs[group_name] = _sort_entries(
            group_entries, input_groups[group_name].get("sort")
        )

    for grouped_input, group_def in zip(grouped_style_list, grouped_input_defs):
        grouped_input["ports"] = _sort_entries(grouped_input["ports"], group_def.get("sort"))
        grouped_input["count"] = len(grouped_input["ports"])
        grouped_input["width"] = grouped_input["ports"][0]["width"] if grouped_input["ports"] else 0

    for e in outputs:
        e["group"] = "other"
        for group_name, group_cfg in metadata.get("output_groups", {}).items():
            if _match_patterns(e["name"], group_cfg.get("match", [])):
                e["group"] = group_name
                break

    port_sig_hash = _sig.compute(ports)

    tier2_probes = copy.deepcopy(metadata.get("tier2_probes", []))
    for probe in tier2_probes:
        if probe.get("name") == "mem_ref_hit" and probe.get("width") == 46:
            probe["width"] = _port_width("mem_out_ref_hit", 46)

    dynamic_port_groups: dict = {}
    for group_name, group_entries in grouped_inputs.items():
        group_cfg = input_groups.get(group_name, {})
        if group_cfg.get("stimulus_groups"):
            config_like: dict = {
                "count": len(group_entries),
                "width": group_entries[0]["width"] if group_entries else 0,
                "note": group_cfg.get("note", ""),
                "ports": [{"index": i, **entry} for i, entry in enumerate(group_entries)],
                "stimulus_groups": [],
            }
            cursor = 0
            for subgroup in group_cfg.get("stimulus_groups", []):
                sg_count = subgroup.get("count")
                if sg_count is None and subgroup.get("count_from"):
                    source_group = grouped_inputs.get(subgroup["count_from"], [])
                    sg_count = len(source_group)
                sg_count = int(sg_count or 0)
                subgroup_ports = group_entries[cursor:cursor + sg_count]
                config_like["stimulus_groups"].append({
                    "name": subgroup["name"],
                    "stimulus_prefix": subgroup.get("stimulus_prefix"),
                    "ports": [{"index": i, **entry} for i, entry in enumerate(subgroup_ports)],
                })
                cursor += sg_count
            dynamic_port_groups[group_name] = config_like
        else:
            dynamic_port_groups[group_name] = {
                "count": len(group_entries),
                "channel_width": group_entries[0]["width"] if group_entries else 0,
                "field_map": group_cfg.get("field_map", {}),
                "stimulus_prefix": group_cfg.get("stimulus_prefix"),
                "channels": [{"index": i, **e} for i, e in enumerate(group_entries)],
            }

    for parent_name, sub_defs in grouped_style_cfg.items():
        parent_grouped_inputs = [
            g for g in grouped_style_list
            if any(g["name"] == gd["name"] for gd in sub_defs)
        ]
        all_ports: list = []
        for rg in parent_grouped_inputs:
            all_ports.extend(rg["ports"])
        dynamic_port_groups[parent_name] = {
            "count": len(all_ports),
            "ports": all_ports,
            "groups": parent_grouped_inputs,
        }

    port_map = {
        "format_version": "1",
        "generated_by": "arc gen-top --mode verilog",
        "source_file": str(verilog_path.resolve()),
        "top_module": "algo_top",
        "port_signature_hash": port_sig_hash,
        "port_groups": {
            "clock_reset": clock_reset,
            "bx0": bx0,
            **dynamic_port_groups,
            "outputs": outputs,
            "unclassified": unclassified,
        },
        "tier2_probes": tier2_probes,
    }

    num_nonempty_groups = sum(
        1
        for k, v in port_map["port_groups"].items()
        if k not in ("unclassified",)
        and (
            (isinstance(v, list) and v)
            or (isinstance(v, dict) and v.get("count", 0) > 0)
        )
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_yaml.dump(port_map, default_flow_style=False, sort_keys=False))
    print(f"  ✓ Port map:  {output_path}")
    print(f"    - {len(ports)} ports in {num_nonempty_groups} functional groups")
    print(f"    - Port signature hash: {port_sig_hash}")

    return port_map, port_sig_hash


def generate_probe_map(port_map: dict, output_path: Path) -> None:
    """Generate probe_map.yaml from the already-generated port_map data."""
    import yaml as _yaml

    port_groups = port_map.get("port_groups", {})
    output_ports = port_groups.get("outputs", [])
    tier1 = [
        {
            "name": port["name"],
            "direction": port["direction"],
            "width": port["width"],
            "group": port.get("group", "other"),
            "tier": 1,
        }
        for port in output_ports
    ]

    probe_map = {
        "format_version": "1",
        "generated_by": port_map.get("generated_by", "arc gen-top --mode verilog"),
        "source_file": port_map.get("source_file"),
        "top_module": port_map.get("top_module", "algo_top"),
        "port_signature_hash": port_map.get("port_signature_hash"),
        "probe_tiers": {
            "tier1": tier1,
            "tier2": port_map.get("tier2_probes", []),
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_yaml.dump(probe_map, default_flow_style=False, sort_keys=False))
    print(f"  ✓ Probe map: {output_path}")
    print(
        f"    - {len(tier1)} Tier 1 probes, "
        f"{len(probe_map['probe_tiers']['tier2'])} Tier 2 probes"
    )


def generate_tb_bindings(port_map: dict, output_path: Path) -> None:
    """Generate tb_bindings.svh from a port_map dict."""
    lines = []
    sig_hash = port_map.get("port_signature_hash", "unknown")
    top_module = port_map.get("top_module", "algo_top")
    groups = port_map.get("port_groups", {})

    lines.append("// ============================================================")
    lines.append("// tb_bindings.svh — Generated by arc gen-top")
    lines.append(f"// Top module   : {top_module}")
    lines.append(f"// Port sig hash: {sig_hash}")
    lines.append("// DO NOT EDIT — regenerate: arc gen-top ...")
    lines.append("// ============================================================")
    lines.append("")

    def _decl(entry: dict) -> str:
        w = entry.get("width", 1)
        name = entry["name"]
        direction = entry.get("direction", "input")
        sv_type = "wire" if direction == "output" else "logic"
        if w == 1:
            return f"{sv_type} {name};"
        return f"{sv_type} [{w-1}:0] {name};"

    cr = groups.get("clock_reset", [])
    if cr:
        lines.append("// -- clock / reset --")
        for e in cr:
            lines.append(_decl(e))
        lines.append("")

    bx0 = groups.get("bx0", [])
    if bx0:
        lines.append("// -- BX0 pulse --")
        for e in bx0:
            lines.append(_decl(e))
        lines.append("")

    SPECIAL_GROUPS = {"clock_reset", "bx0", "outputs", "unclassified"}
    for group_name, group_data in groups.items():
        if group_name in SPECIAL_GROUPS:
            continue
        if not group_data:
            continue
        if isinstance(group_data, dict) and "channels" in group_data:
            channels = group_data.get("channels", [])
            w = group_data.get("channel_width", 1)
            lines.append(f"// -- {group_name} ({len(channels)} × {w}-bit) --")
            for ch in channels:
                lines.append(f"logic [{w-1}:0] {ch['name']};")
            lines.append("")
        elif isinstance(group_data, dict) and "stimulus_groups" in group_data:
            ports = group_data.get("ports", [])
            w = group_data.get("width", 64)
            lines.append(f"// -- {group_name} ({len(ports)} × {w}-bit) --")
            for p in ports:
                lines.append(f"logic [{w-1}:0] {p['name']};")
            lines.append("")
        elif isinstance(group_data, dict) and "groups" in group_data:
            grouped_ports_flat = group_data.get("ports", [])
            if grouped_ports_flat:
                lines.append(f"// -- {group_name} --")
                for e in grouped_ports_flat:
                    lines.append(_decl(e))
                lines.append("")
        elif isinstance(group_data, dict) and "ports" in group_data:
            ports = group_data.get("ports", [])
            w = group_data.get("width", 1)
            lines.append(f"// -- {group_name} ({len(ports)} × {w}-bit) --")
            for p in ports:
                lines.append(f"logic [{w-1}:0] {p['name']};")
            lines.append("")

    outs = groups.get("outputs", [])
    if outs:
        lines.append("// -- Outputs (wire — driven by DUT) --")
        prev_group = None
        for e in outs:
            g = e.get("group", "other")
            if g != prev_group:
                lines.append(f"// {g}")
                prev_group = g
            lines.append(_decl(e))
        lines.append("")

    unclassified = groups.get("unclassified", [])
    if unclassified:
        lines.append("// -- Unclassified DUT ports --")
        for e in unclassified:
            lines.append(_decl(e))
        lines.append("")

    lines.append("// -- Simulation helpers (not DUT ports) --")
    lines.append("integer cycle_count;")
    lines.append("integer out_csv_fd;")
    lines.append("integer probe_csv_fd;")
    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n")
    print(f"  ✓ TB bindings: {output_path}")


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

def cmd_clean(args):
    """Remove generated artifacts for one design without touching IP/HLS trees."""
    try:
        design_path = Path(args.design).expanduser().resolve()
        if not design_path.exists():
            print(f"❌ Design file not found: {design_path}", file=sys.stderr)
            print("   → Check the design.yml path and re-run arc clean.", file=sys.stderr)
            sys.exit(1)

        c_root = _consumer_root(design_path, getattr(args, "consumer_root", None))
        output = _resolve_path(args.output, c_root, Path("algo_top.v"))
        output_dir = output.parent
        ip_info_file = (
            _resolve_path(args.ip_info, c_root) if args.ip_info else output_dir / "ip_info.yaml"
        )

        generated_paths = [
            output,
            output_dir / "build_manifest.json",
            output_dir / "design_parameters.json",
            output_dir / "port_map.yaml",
            output_dir / "probe_map.yaml",
            output_dir / "tb_bindings.svh",
            output_dir / "port_signature.json",
            output_dir / "maturity_report.json",
            output_dir / "tb_algo_top.sv",
            output_dir / "stimulus_current.svh",
            ip_info_file,
        ]

        verify_design = None
        if getattr(args, "verify_design", None):
            verify_design = _resolve_path(args.verify_design, c_root)
        elif not getattr(args, "no_verify", False):
            candidate = design_path.parent.parent / "verify" / "design.verification.yml"
            if candidate.exists():
                verify_design = candidate.resolve()

        planned: list[Path] = [p for p in generated_paths if p.exists()]

        if verify_design and verify_design.exists():
            verify_root = verify_design.parent
            for flow_name, flow_kind in _load_verify_flow_entries(verify_design):
                flow_dirs = [verify_root / flow_name]
                if flow_kind:
                    flow_dirs.append(verify_root / flow_kind / flow_name)
                for flow_dir in flow_dirs:
                    planned.extend(
                        path
                        for path in [
                            flow_dir / "verify.flow.yml",
                            flow_dir / "wave.tcl",
                            flow_dir / "port_map.yaml",
                            flow_dir / "stimulus_current.svh",
                            flow_dir / "xsim_work",
                            flow_dir / "stimulus",
                        ]
                        if path.exists()
                    )
                    planned.extend(
                        path for path in sorted(flow_dir.glob("tb_*.sv")) if path.exists()
                    )

        seen: set[Path] = set()
        unique_planned: list[Path] = []
        for path in planned:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            unique_planned.append(resolved)

        if not unique_planned:
            print("No generated artifacts found to clean.")
            return

        print(f"🧹 Cleaning generated artifacts for design: {design_path}")
        for path in unique_planned:
            rel = (
                path.relative_to(c_root) if path.is_relative_to(c_root) else path
            )
            print(f"  - {rel}")

        if getattr(args, "dry_run", False):
            print("Dry run only; no files were removed.")
            return

        removed: list[Path] = []
        for path in unique_planned:
            _remove_path(path, removed)

        print(f"✓ Removed {len(removed)} generated artifact(s)")
    except Exception as e:
        print_cli_error(
            "Clean failed",
            e,
            hint="Check the design path, output path, and verify-design path, then retry arc clean.",
        )
        sys.exit(1)


def cmd_unpack_ips(args):
    """Unpack IP archives."""
    try:
        src_dir = Path(args.src).expanduser().resolve()
        c_root = _consumer_root(src_dir, getattr(args, "consumer_root", None))
        ip_root = _resolve_path(args.ip_root, c_root, Path("ips"))
        extracted = unpack_ip_archives(
            src_dir=args.src,
            ip_root=ip_root,
            force=args.force,
            verbose=True,
        )
        print(f"\n✓ Successfully extracted {len(extracted)} IP packages")
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


def cmd_ip_summary(args):
    """Generate IP summary (ip_info.yaml)."""
    try:
        design_path = Path(args.design).expanduser().resolve()
        if not design_path.exists():
            print(f"❌ Design file not found: {design_path}")
            sys.exit(1)

        cfg = DesignConfig.load_relaxed(design_path)
        c_root = _consumer_root(design_path, getattr(args, "consumer_root", None))
        build_root = _resolve_path(args.build_dir, c_root, Path("build"))
        ip_root = _resolve_path(args.ip_root, c_root, Path("ips"))
        src_root = (
            _resolve_path(args.src_root, c_root)
            if hasattr(args, "src_root") and args.src_root
            else design_path.parent
        )

        print(f"📋 Collecting IP metadata from {ip_root}...")
        summary = collect_all(
            build_root=build_root,
            modules=cfg.modules,
            ip_root=ip_root,
            src_root=src_root,
        )

        if not any(v is not None for v in summary.values()):
            print("❌ No IP metadata found. Run unpack-ips first.")
            sys.exit(1)

        output = _resolve_path(args.output, c_root, Path("ip_info.yaml"))
        write_summary(summary, output, format="yaml")
        print(f"✓ IP summary written to {output}")

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


def cmd_match_ports(args):
    """Show port matching report."""
    try:
        design_path = Path(args.design).expanduser().resolve()
        cfg = DesignConfig.load_relaxed(design_path)

        c_root = _consumer_root(design_path, getattr(args, "consumer_root", None))
        ip_info_file = _resolve_path(args.ip_info, c_root, Path("ip_info.yaml"))
        if not ip_info_file.exists():
            print(f"❌ IP info file not found: {ip_info_file}")
            print("   Run 'arc ip-summary' first")
            sys.exit(1)

        print("🔗 Matching ports...")
        ip_info = load_ip_info(ip_info_file)
        conn_map, global_nets, match_report = auto_match_ports(
            cfg, ip_info, system_yml=args.system
        )

        print(f"\n✓ Port matching complete")
        print(f"\nConnections: {len(conn_map)}")
        for key, val in conn_map.items():
            print(f"  {key} → {val}")

        if global_nets:
            print(f"\nGlobal nets: {len(global_nets)}")
            for net, ports in global_nets.items():
                print(f"  {net}: {', '.join(ports)}")

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


def cmd_lint(args):
    """Lint VHDL file(s)."""
    try:
        from arc.core.utils.vhdl_linter import lint_vhdl_files

        files = args.files if isinstance(args.files, list) else [args.files]
        vhdl_files = [Path(f) for f in files]

        for vfile in vhdl_files:
            if not vfile.exists():
                print(f"❌ File not found: {vfile}")
                sys.exit(1)

        passed, failed = lint_vhdl_files(
            vhdl_files, std=args.std, ieee=args.ieee, fix=args.fix
        )

        if failed > 0:
            sys.exit(1)

    except ImportError as e:
        print(f"❌ VHDL linter import error: {e}")
        sys.exit(1)
    except Exception as e:
        print_cli_error(
            "VHDL lint failed",
            e,
            hint="Check that the requested files exist and that the linter toolchain is installed correctly.",
        )
        sys.exit(1)


def cmd_lint_verilog(args):
    """Lint Verilog file(s)."""
    try:
        from arc.core.utils.verilog_linter import lint_verilog_files

        files = args.files if isinstance(args.files, list) else [args.files]
        verilog_files = [Path(f) for f in files]

        for vfile in verilog_files:
            if not vfile.exists():
                print(f"❌ File not found: {vfile}")
                sys.exit(1)

        passed, failed = lint_verilog_files(
            verilog_files,
            top_module=args.top_module,
            show_warnings=not args.no_warnings,
            strict=args.strict,
        )

        if failed > 0:
            sys.exit(1)

    except Exception as e:
        print_cli_error(
            "Verilog lint failed",
            e,
            hint="Check that the requested files exist and that the Verilog lint toolchain is available.",
        )
        sys.exit(1)


def cmd_validate(args):
    """Validate design.yml without generating output."""
    try:
        design_path = Path(args.design).expanduser().resolve()

        if not design_path.exists():
            print(f"❌ Design file not found: {design_path}")
            sys.exit(1)

        import yaml as _yaml

        _raw_design = _yaml.safe_load(design_path.read_text()) or {}
        _registry_ref = _raw_design.get("registry")
        registry_errors = 0
        if _registry_ref:
            registry_path = (design_path.parent / _registry_ref).resolve()
            if not registry_path.exists():
                print(f"❌ Registry file not found: {registry_path}")
                sys.exit(1)
            print(f"📖 Loading registry: {registry_path}")
            print("🔍 Validating registry...\n")
            reg_validator = validate_registry(registry_path)
            reg_validator.print_report()
            registry_errors = len(reg_validator.errors)
            if registry_errors:
                print(
                    f"\n⛔ Registry has {registry_errors} error(s). "
                    "Fix them before validating design."
                )
                sys.exit(1)
            if args.strict and reg_validator.warnings:
                print(
                    f"\n⛔ Strict mode: treating {len(reg_validator.warnings)} "
                    "registry warning(s) as errors"
                )
                sys.exit(1)
            print()

        print(f"📖 Loading design: {design_path}")
        cfg = DesignConfig.load_relaxed(design_path)

        print("🔍 Validating design configuration...\n")
        validator = validate_design(cfg, design_path)
        validator.print_report()

        if validator.has_errors():
            sys.exit(1)
        elif args.strict and len(validator.warnings) > 0:
            print(f"\n⛔ Strict mode: Treating {len(validator.warnings)} warnings as errors")
            sys.exit(1)
        else:
            if getattr(args, "check_stale", False):
                print()
                print("🕒 Checking for stale generated artifacts…")
                stale_diag = ATGDiagnosticReport("stale-check")
                output_dir = design_path.parent
                stale_report = check_top_gen_staleness(output_dir, design_yml=design_path)
                ip_info_candidates = (
                    output_dir / "ip_info.yaml",
                    output_dir.parent / "ip_info.yaml",
                )
                for _iic in ip_info_candidates:
                    if _iic.exists():
                        ip_stale = check_ip_info_staleness(_iic, design_yml=design_path)
                        stale_report.stale.extend(ip_stale.stale)
                        stale_report.missing.extend(ip_stale.missing)
                        break

                stale_lines = format_stale_report(stale_report)
                if stale_lines:
                    for line in stale_lines:
                        print(f"  ⚠️  {line}")
                    stale_diag.warn(
                        "ATG007",
                        f"{len(stale_report.stale)} stale artifact(s) detected",
                        action="Re-run arc gen-top to rebuild",
                        path=output_dir,
                    )
                    stale_diag.print_console(file=sys.stdout)
                    if args.strict:
                        print("\n⛔ Strict mode: stale artifacts treated as errors")
                        sys.exit(1)
                else:
                    print("  ✅ All generated artifacts are up to date")

            print("\n✅ Design is valid and ready for generation!")
            sys.exit(0)

    except Exception as e:
        print_cli_error(
            "Validation failed",
            e,
            hint="Fix the reported design or contract issue, then re-run arc validate.",
        )
        sys.exit(1)


def cmd_validate_registry(args):
    """Validate a modules.yml registry file independently."""
    try:
        registry_path = Path(args.registry).expanduser().resolve()
        if not registry_path.exists():
            print(f"❌ Registry file not found: {registry_path}")
            sys.exit(1)

        print(f"📖 Loading registry: {registry_path}")
        print("🔍 Validating registry...\n")
        validator = validate_registry(registry_path)
        validator.print_report()

        if validator.has_errors():
            sys.exit(1)
        elif args.strict and validator.warnings:
            print(
                f"\n⛔ Strict mode: treating {len(validator.warnings)} warning(s) as errors"
            )
            sys.exit(1)
        else:
            sys.exit(0)

    except Exception as e:
        print_cli_error(
            "Registry validation failed",
            e,
            hint="Fix the modules.yml registry issue, then re-run arc validate-registry.",
        )
        sys.exit(1)


def cmd_gen_top(args):
    """Generate algorithm top (VHDL or Block Design TCL)."""
    try:
        design_path = Path(args.design).expanduser().resolve()

        if getattr(args, "rtl_resource_root", None):
            os.environ["TOPGEN_RTL_RESOURCE_ROOT"] = str(
                Path(args.rtl_resource_root).expanduser().resolve()
            )

        if not design_path.exists():
            print(f"❌ Design file not found: {design_path}")
            print("   💡 Check the path and try again")
            sys.exit(1)

        cfg = DesignConfig.load_relaxed(design_path)

        print("🔍 Validating design configuration...")
        validator = validate_design(cfg, design_path)
        validator.print_report()

        if validator.has_errors():
            sys.exit(1)

        print("✅ Validation passed!\n")

        c_root = _consumer_root(design_path, getattr(args, "consumer_root", None))
        ip_root = _resolve_path(args.ip_root, c_root, Path("ips"))
        build_root = _resolve_path(args.build_dir, c_root, Path("build"))
        hls_build_root = _resolve_path(
            getattr(args, "hls_build_root", None), c_root, Path("build_hls")
        )
        src_root = (
            _resolve_path(args.src_root, c_root)
            if hasattr(args, "src_root") and args.src_root
            else design_path.parent
        )
        xml_stimulus_tool = _resolve_path(
            getattr(args, "xml_stimulus_tool", None), c_root
        )

        if args.output:
            output = _resolve_path(args.output, c_root)
            output_dir = output.parent
            if output_dir != Path("."):
                output_dir.mkdir(parents=True, exist_ok=True)
        else:
            output_dir = c_root

        ip_info_file = (
            _resolve_path(args.ip_info, c_root)
            if args.ip_info
            else output_dir / "ip_info.yaml"
        )

        _modules_yml = getattr(args, "contracts_from", None)
        _contracts = None
        if _modules_yml:
            _modules_yml_path = _resolve_path(_modules_yml, c_root)
            if _modules_yml_path.exists():
                try:
                    _contracts = load_contracts_for_design(_modules_yml_path, c_root)
                    print(f"📜 Contracts loaded: {len(_contracts)} module(s) covered")
                except Exception as _ce:
                    print(f"⚠️  Contract loading failed (falling back to heuristics): {_ce}")
            else:
                print(f"⚠️  --contracts-from file not found: {_modules_yml_path}")

        if ip_info_file.exists():
            ip_info = load_ip_info(ip_info_file)
        elif _contracts:
            print("📋 Projecting ip_info from contracts (not from built IP) …")
            _mapped = {}
            for m in cfg.modules:
                c = _contracts.get(m.name) or (
                    m.ip_info_key and _contracts.get(m.ip_info_key)
                )
                if c:
                    _mapped[m.name] = c
            ip_info = synthesize_ip_info(_mapped)
        else:
            print("📋 Generating IP summary from build artefacts …")
            summary = collect_all(
                build_root=build_root,
                modules=cfg.modules,
                ip_root=ip_root,
                src_root=src_root,
            )
            ip_info_file.parent.mkdir(parents=True, exist_ok=True)
            write_summary(summary, ip_info_file, format="yaml")
            ip_info = load_ip_info(ip_info_file)

        conn_map, global_nets, match_report = auto_match_ports(
            cfg, ip_info, system_yml=args.system, contracts=_contracts,
        )

        if match_report.contract_driven_modules:
            print(f"  Contract-driven : {', '.join(match_report.contract_driven_modules)}")
        if match_report.compat_mode_modules:
            print(f"  Compat-mode     : {', '.join(match_report.compat_mode_modules)}")
        wmc = match_report.wiring_method_counts
        total_conn = sum(wmc.values())
        if total_conn:
            print(
                f"  Wiring methods  : {total_conn} connections — "
                f"{wmc['contract_wiring']} contract, "
                f"{wmc['port_map_ranges']} ranges, "
                f"{wmc['port_map']} port_map, "
                f"{wmc['auto_match']} auto"
            )
        for _w in match_report.warnings:
            print(f"  ⚠️  {_w}")

        if getattr(args, "strict", False) and match_report.has_compat_modules():
            print(
                "\n❌ Strict mode failure: the following modules have no interface"
                " contract and were wired via heuristics:"
            )
            for _m in match_report.compat_mode_modules:
                print(f"   • {_m}")
            print("   Add interface contracts or remove --strict to proceed.")
            sys.exit(1)

        if getattr(args, "strict", False) and wmc["auto_match"] > 0:
            print(
                f"\n❌ Strict mode: {wmc['auto_match']} connection(s) used auto-match heuristics."
            )
            print(
                "   Add explicit port_map, contract_wiring, or topology_groups "
                "to each connection."
            )
            sys.exit(1)

        if getattr(args, "strict", False) and wmc.get("port_map_ranges", 0) > 0:
            print(
                f"\n❌ Strict mode: {wmc['port_map_ranges']} connection(s) still use "
                "port_map_ranges."
            )
            print("   Migrate to topology_groups (contract-driven) or remove --strict.")
            sys.exit(1)

        if getattr(args, "strict", False) and _contracts and cfg.topology_groups:
            from arc.topgen.ip.contract_verifier import verify_topology_groups
            tg_issues = verify_topology_groups(cfg, _contracts)
            tg_errors = [i for i in tg_issues if i.severity == "error"]
            if tg_errors:
                print(
                    f"\n❌ Strict mode: {len(tg_errors)} topology group "
                    "verification error(s):"
                )
                for _i in tg_errors:
                    print(str(_i))
                sys.exit(1)

        if args.mode == "vhdl":
            output = _resolve_path(args.output, c_root, Path(f"{args.top_name}.vhd"))
            print(f"🔨 Generating structural VHDL: {output}")

            report = write_structural_vhdl(
                cfg=cfg,
                ip_info=ip_info,
                conn_map=conn_map,
                global_nets=global_nets,
                ip_root=ip_root,
                out_path=output,
                top_name=args.top_name,
                system_yml=args.system,
            )

            print(f"✓ VHDL top generated: {output}")
            _print_gen_report(report)

            if getattr(args, "strict", False):
                _strict_port_gate(report, cfg)

            if args.lint:
                _run_vhdl_lint(output)

        elif args.mode == "verilog":
            output = _resolve_path(args.output, c_root, Path(f"{args.top_name}.v"))
            print(f"🔨 Generating structural Verilog: {output}")

            report = write_structural_verilog(
                cfg=cfg,
                ip_info=ip_info,
                conn_map=conn_map,
                global_nets=global_nets,
                ip_root=ip_root,
                out_path=output,
                top_name=args.top_name,
                system_yml=args.system,
            )

            print(f"✓ Verilog top generated: {output}")
            _print_gen_report(report)

            if getattr(args, "strict", False):
                _strict_port_gate(report, cfg)

            manifest_output = output.parent / "build_manifest.json"
            print(f"📦 Generating build manifest: {manifest_output}")
            generate_build_manifest(
                cfg=cfg,
                ip_info=ip_info,
                ip_root=ip_root,
                algo_top=output,
                design_file=design_path,
                manifest_output=manifest_output,
                project_root=c_root,
                hls_build_root=hls_build_root,
            )

            port_map_output = output.parent / "port_map.yaml"
            print(f"\n🗺  Generating port map: {port_map_output}")
            port_map_data, port_sig_hash = generate_port_map(
                output, port_map_output, cfg.interface_metadata
            )

            if port_map_data is not None:
                from arc.core.utils import port_signature as _sig
                sig_output = output.parent / "port_signature.json"
                _sig.write_artifact(
                    port_sig_hash,
                    sig_output,
                    source_file=str(output.resolve()),
                    top_module=port_map_data.get("top_module", "algo_top"),
                    port_count=sum(
                        len(v) if isinstance(v, list)
                        else v.get("count", 0) if isinstance(v, dict)
                        else 0
                        for v in port_map_data.get("port_groups", {}).values()
                    ),
                )
                print(f"  ✓ Port signature artifact: {sig_output}")

            params_output = output.parent / "design_parameters.json"
            print(f"\n📋 Generating design parameters: {params_output}")
            write_design_parameters(
                cfg,
                params_output,
                algo_top_v_path=output,
                hls_metrics_file=args.hls_metrics,
                port_map_data=port_map_data,
            )

            probe_map_output = None
            if port_map_data is not None:
                probe_map_output = output.parent / "probe_map.yaml"
                print(f"\n🔎 Generating probe map: {probe_map_output}")
                generate_probe_map(port_map_data, probe_map_output)

            if port_map_data is not None:
                tb_bindings_output = output.parent / "tb_bindings.svh"
                print(f"\n🔌 Generating TB bindings: {tb_bindings_output}")
                generate_tb_bindings(port_map_data, tb_bindings_output)

                if probe_map_output and probe_map_output.exists():
                    import yaml as _yaml
                    probe_map_data = _yaml.safe_load(probe_map_output.read_text()) or {}
                    validate_generated_contracts(
                        port_map_data,
                        probe_map_data,
                        tb_bindings_output,
                        params_output,
                    )

            maturity_output = output.parent / "maturity_report.json"
            maturity = {
                "modules": {
                    "total": len(cfg.modules),
                    "contract_driven": len(match_report.contract_driven_modules),
                    "compat_mode": len(match_report.compat_mode_modules),
                },
                "connections": dict(match_report.wiring_method_counts),
                "ports": {
                    "open_outputs": len(report.get("open_outputs", [])),
                    "tied_inputs": len(report.get("tied_to_zero", [])),
                    "open_accounted": len(report.get("open_outputs", [])) - len(
                        _filter_unaccounted(
                            report.get("open_outputs", []),
                            cfg.allowed_unconnected.open_outputs,
                        )
                    ),
                    "tied_accounted": len(report.get("tied_to_zero", [])) - len(
                        _filter_unaccounted(
                            report.get("tied_to_zero", []),
                            cfg.allowed_unconnected.tied_inputs,
                        )
                    ),
                },
                "strict_pass": (
                    not match_report.has_compat_modules()
                    and match_report.wiring_method_counts.get("auto_match", 0) == 0
                    and len(
                        _filter_unaccounted(
                            report.get("open_outputs", []),
                            cfg.allowed_unconnected.open_outputs,
                        )
                    ) == 0
                    and len(
                        _filter_unaccounted(
                            report.get("tied_to_zero", []),
                            cfg.allowed_unconnected.tied_inputs,
                        )
                    ) == 0
                ),
                "port_signature_hash": port_sig_hash if port_map_data else None,
            }
            maturity_output.write_text(json.dumps(maturity, indent=2) + "\n")
            print(f"  ✓ Maturity report: {maturity_output}")

            should_gen_tb = args.gen_testbench or (cfg.testbench and cfg.testbench.generate)
            if should_gen_tb:
                print("\n🧪 Generating SystemVerilog testbench...")
                xml_path = None
                event_id = args.event_id
                if args.xml_stimulus:
                    xml_path = Path(args.xml_stimulus)
                elif cfg.testbench and cfg.testbench.xml_stimulus_path:
                    xml_path = design_path.parent / cfg.testbench.xml_stimulus_path
                    event_id = cfg.testbench.event_id
                    print("   ℹ️  Using testbench config from design.yml")
                if xml_path:
                    if not xml_path.exists():
                        print(f"⚠️  Warning: XML stimulus file not found: {xml_path}")
                        print(f"   📁 Searched: {xml_path.absolute()}")
                        print("   Will generate testbench with placeholder stimulus")
                        xml_path = None
                    else:
                        print(f"   📄 XML stimulus: {xml_path}")
                        print(f"   🔢 Event ID: {event_id}")
                tb_path = generate_sv_testbench(
                    rtl_path=output,
                    output_dir=output.parent,
                    xml_stimulus_path=xml_path,
                    event_id=event_id,
                    stimulus_converter_path=xml_stimulus_tool,
                )
                print(f"✓ Testbench generated: {tb_path}")

            if args.lint:
                _run_verilog_lint(output, args.top_name)

        elif args.mode == "bd":
            output = _resolve_path(args.output, c_root, Path("block_design.tcl"))
            print(f"🔨 Generating Block Design TCL: {output}")

            write_bd_tcl(
                cfg=cfg,
                ip_info=ip_info,
                conn_map=conn_map,
                global_nets=global_nets,
                out_path=output,
                bd_name=args.bd_name,
                src_root=design_path.parent,
            )

            print(f"✓ Block Design TCL generated: {output}")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback as _tb
        _tb.print_exc()
        sys.exit(1)


# ---------------------------------------------------------------------------
# gen-top internal helpers
# ---------------------------------------------------------------------------

def _print_gen_report(report: dict) -> None:
    sep = "=" * 70
    print(f"\n{sep}")
    print("📊 GENERATION REPORT")
    print(f"{sep}")
    print(f"  Modules:     {report['total_modules']}")
    print(f"  Instances:   {report['total_instances']}")
    print(f"  Connections: {report['total_connections']}")
    if report.get("tied_to_zero"):
        print(f"\n⚠️  INPUTS TIED TO ZERO ({len(report['tied_to_zero'])}):")
        for inst, port, width in sorted(report["tied_to_zero"]):
            print(f"    {inst}.{port} [{width} bits]")
    if report.get("open_outputs"):
        print(f"\n⚠️  OUTPUTS LEFT OPEN ({len(report['open_outputs'])}):")
        for inst, port, width in sorted(report["open_outputs"]):
            print(f"    {inst}.{port} [{width} bits]")
    if not report.get("tied_to_zero") and not report.get("open_outputs"):
        print("\n✅ All ports properly connected!")
    print(f"{sep}\n")


def _strict_port_gate(report: dict, cfg) -> None:
    unaccounted_open = _filter_unaccounted(
        report.get("open_outputs", []), cfg.allowed_unconnected.open_outputs
    )
    unaccounted_tied = _filter_unaccounted(
        report.get("tied_to_zero", []), cfg.allowed_unconnected.tied_inputs
    )
    if not unaccounted_open and not unaccounted_tied:
        return
    if unaccounted_open:
        print(f"❌ Strict mode: {len(unaccounted_open)} unaccounted open output(s):")
        for inst, port, w in sorted(unaccounted_open)[:20]:
            print(f"   • {inst}.{port} [{w} bits]")
        if len(unaccounted_open) > 20:
            print(f"   … and {len(unaccounted_open) - 20} more")
    if unaccounted_tied:
        print(f"❌ Strict mode: {len(unaccounted_tied)} unaccounted tied input(s):")
        for inst, port, w in sorted(unaccounted_tied)[:20]:
            print(f"   • {inst}.{port} [{w} bits]")
        if len(unaccounted_tied) > 20:
            print(f"   … and {len(unaccounted_tied) - 20} more")
    print("   Add patterns to allowed_unconnected in design.yml or remove --strict.")
    sys.exit(1)


def _run_vhdl_lint(output: Path) -> None:
    try:
        from arc.core.utils.vhdl_linter import lint_vhdl_file
        print("\n🔍 Running GHDL syntax checker...")
        lint_vhdl_file(output, std="08", ieee="synopsys")
    except ImportError:
        print("⚠️  VHDL linter requires GHDL. Please install it:")
        print("   - Ubuntu/Debian: sudo apt-get install ghdl")
        print("   - Fedora/RHEL: sudo dnf install ghdl")
        print("   - macOS: brew install ghdl")


def _run_verilog_lint(output: Path, top_name: str) -> None:
    try:
        from arc.core.utils.verilog_linter import lint_verilog_file
        print("\n🔍 Running Verilog linter (Verilator)...")
        lint_verilog_file(output, top_module=top_name)
    except ImportError:
        print("⚠️  Verilog linter requires Verilator. Please install it:")
        print("   - Ubuntu/Debian: sudo apt-get install verilator")
        print("   - Fedora/RHEL: sudo dnf install verilator")
        print("   - macOS: brew install verilator")


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------

def register(sub) -> None:
    """Register the ``arc topgen`` subparser and its commands."""
    p_tg = sub.add_parser("topgen", help="Generate hardware topology structure")
    tg_sub = p_tg.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # arc topgen gen-top
    p_gen = tg_sub.add_parser("gen-top", help="Generate algorithm top-level")
    p_gen.add_argument("design", type=Path, help="design.yaml file")
    p_gen.add_argument(
        "--consumer-root", type=Path,
        help="Consumer workspace root used to resolve relative defaults",
    )
    p_gen.add_argument(
        "--mode", choices=["vhdl", "verilog", "bd"], required=True,
        help="Output mode: vhdl, verilog, or bd (Block Design TCL)",
    )
    p_gen.add_argument("--ip-root", type=Path, help="IP directory (default: ips/)")
    p_gen.add_argument("--ip-info", type=Path, help="IP info file (auto-generated if missing)")
    p_gen.add_argument("--build-dir", type=Path, help="Build directory (default: build/)")
    p_gen.add_argument(
        "--hls-build-root", type=Path,
        help="HLS build directory (default: build_hls/)",
    )
    p_gen.add_argument(
        "--src-root", type=Path,
        help="Source root for HDL files (default: design file's directory)",
    )
    p_gen.add_argument("--system", type=Path, help="system.yml for global nets")
    p_gen.add_argument("--output", "-o", type=Path, help="Output file")
    p_gen.add_argument("--top-name", default="algo_top", help="Top entity/module name")
    p_gen.add_argument("--bd-name", default="design_1", help="BD name (BD mode)")
    p_gen.add_argument(
        "--gen-testbench", action="store_true",
        help="Generate SystemVerilog testbench (Verilog mode only)",
    )
    p_gen.add_argument("--xml-stimulus", type=Path, help="XML file for stimulus generation")
    p_gen.add_argument(
        "--xml-stimulus-tool", type=Path,
        help="Path to the xml_to_sv_stimulus helper binary",
    )
    p_gen.add_argument(
        "--event-id", type=int, default=1, help="Event ID to extract from XML (default: 1)",
    )
    p_gen.add_argument("--hls-metrics", type=Path, help="HLS metrics JSON file")
    p_gen.add_argument(
        "--contracts-from", type=Path, metavar="MODULES_YML",
        help="modules.yml with interface_contract paths; enables contract-driven wiring",
    )
    p_gen.add_argument(
        "--strict", action="store_true",
        help=(
            "Fail if any module lacks a contract, any connection uses auto-match or "
            "port_map_ranges, or topology groups have verification errors"
        ),
    )
    p_gen.add_argument("--lint", action="store_true", help="Run linter after generation")
    p_gen.add_argument("--fix-lint", action="store_true", help="Auto-fix linting issues")
    p_gen.add_argument(
        "--rtl-resource-root", type=Path,
        help="Framework RTL helpers root; sets ${TOPGEN_RTL_RESOURCE_ROOT}",
    )
    p_gen.set_defaults(func=cmd_gen_top)

    # arc topgen validate
    p_validate = tg_sub.add_parser("validate", help="Validate design.yml without generating")
    p_validate.add_argument("design", type=Path, help="design.yaml file to validate")
    p_validate.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    p_validate.add_argument(
        "--check-stale", action="store_true", default=False,
        help="Also verify that generated artifacts are not older than design.yml.",
    )
    p_validate.set_defaults(func=cmd_validate)

    # arc topgen validate-registry
    p_val_reg = tg_sub.add_parser(
        "validate-registry", help="Validate a modules.yml registry independently",
    )
    p_val_reg.add_argument("registry", type=Path, help="modules.yml registry file to validate")
    p_val_reg.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    p_val_reg.set_defaults(func=cmd_validate_registry)

    # arc topgen ip-summary
    p_summary = tg_sub.add_parser(
        "ip-summary", help="Generate IP metadata summary (ip_info.yaml)",
    )
    p_summary.add_argument("design", type=Path, help="design.yaml file")
    p_summary.add_argument(
        "--consumer-root", type=Path,
        help="Consumer workspace root used to resolve relative defaults",
    )
    p_summary.add_argument("--ip-root", type=Path, help="IP directory (default: ips/)")
    p_summary.add_argument("--build-dir", type=Path, help="Build directory (default: build/)")
    p_summary.add_argument(
        "--src-root", type=Path,
        help="Source root for HDL files (default: design file's directory)",
    )
    p_summary.add_argument(
        "--output", "-o", type=Path, help="Output file (default: ip_info.yaml)",
    )
    p_summary.set_defaults(func=cmd_ip_summary)

    # arc topgen match-ports
    p_match = tg_sub.add_parser("match-ports", help="Show port connection report")
    p_match.add_argument("design", type=Path, help="design.yaml file")
    p_match.add_argument(
        "--consumer-root", type=Path,
        help="Consumer workspace root used to resolve relative defaults",
    )
    p_match.add_argument("--ip-info", type=Path, help="IP info file (default: ip_info.yaml)")
    p_match.add_argument("--system", type=Path, help="system.yml for global nets")
    p_match.set_defaults(func=cmd_match_ports)

    # arc topgen unpack-ips
    p_unpack = tg_sub.add_parser("unpack-ips", help="Extract IP archives")
    p_unpack.add_argument(
        "src", type=Path,
        help="Directory containing IP archives (*.zip, *.tar.*)",
    )
    p_unpack.add_argument(
        "--consumer-root", type=Path,
        help="Consumer workspace root used to resolve default output paths",
    )
    p_unpack.add_argument("--ip-root", type=Path, help="Output directory (default: ips/)")
    p_unpack.add_argument("--force", action="store_true", help="Overwrite existing directories")
    p_unpack.set_defaults(func=cmd_unpack_ips)

    # arc topgen clean
    p_clean = tg_sub.add_parser("clean", help="Remove generated artifacts for one design")
    p_clean.add_argument("design", type=Path, help="design.yaml file")
    p_clean.add_argument(
        "--consumer-root", type=Path,
        help="Consumer workspace root used to resolve relative defaults",
    )
    p_clean.add_argument(
        "--output", "-o", type=Path,
        help="Generated top file path used to anchor DUT artifact cleanup",
    )
    p_clean.add_argument(
        "--ip-info", type=Path,
        help="Generated ip_info.yaml path if it lives outside the output directory",
    )
    p_clean.add_argument(
        "--verify-design", type=Path,
        help="design.verification.yml to also clean generated verify artifacts",
    )
    p_clean.add_argument(
        "--no-verify", action="store_true",
        help="Only clean DUT artifacts; leave verify-generated files untouched",
    )
    p_clean.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be removed without deleting files",
    )
    p_clean.set_defaults(func=cmd_clean)

    # arc topgen lint
    p_lint = tg_sub.add_parser("lint", help="Lint VHDL file(s) using GHDL")
    p_lint.add_argument("files", nargs="+", type=Path, help="VHDL file(s) to lint")
    p_lint.add_argument(
        "--fix", action="store_true",
        help="Placeholder for compatibility (GHDL doesn't auto-fix)",
    )
    p_lint.add_argument(
        "--std", default="08",
        help="VHDL standard (87, 93, 02, 08, 19) — default: 08",
    )
    p_lint.add_argument(
        "--ieee", default="synopsys",
        help="IEEE library (standard, synopsys, mentor) — default: synopsys",
    )
    p_lint.set_defaults(func=cmd_lint)

    # arc topgen lint-verilog
    p_lint_verilog = tg_sub.add_parser(
        "lint-verilog", help="Lint Verilog file(s) with Verilator",
    )
    p_lint_verilog.add_argument("files", nargs="+", type=Path, help="Verilog file(s) to lint")
    p_lint_verilog.add_argument(
        "--top-module", help="Top module name (auto-detected if not provided)",
    )
    p_lint_verilog.add_argument(
        "--no-warnings", action="store_true", help="Suppress warnings, show only errors",
    )
    p_lint_verilog.add_argument(
        "--strict", action="store_true", help="Enable strict mode with additional checks",
    )
    p_lint_verilog.set_defaults(func=cmd_lint_verilog)
