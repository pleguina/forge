"""Main entry point for arc CLI."""

from __future__ import annotations

import argparse
import sys
import json
import copy
import os
import shutil
import traceback
from pathlib import Path

from arc.topgen.config import DesignConfig
from arc.topgen.ip.unpacker import unpack_ip_archives
from arc.topgen.ip.parser import collect_all, write_summary
from arc.topgen.ip.matcher import load_ip_info, auto_match_ports, MatchReport
from arc.topgen.ip.contract_loader import load_contracts_for_design, synthesize_ip_info
from arc.topgen.generators.structural_vhdl import write_structural_vhdl
from arc.topgen.generators.structural_verilog import write_structural_verilog
from arc.topgen.generators.block_design import write_bd_tcl
from arc.topgen.generators.sv_testbench_generator import generate_sv_testbench
from arc.topgen.generators.design_parameters import write_design_parameters
from arc.topgen.validation import validate_design, validate_registry
from arc.core.diagnostics import ATGDiagnosticReport
from arc.core.stale_detection import check_top_gen_staleness, check_ip_info_staleness, format_stale_report


_TOPGEN_DEBUG = False


def _debug_enabled() -> bool:
    return _TOPGEN_DEBUG or os.environ.get("TOPGEN_DEBUG", "0") == "1"


def _print_cli_error(prefix: str, exc: Exception, *, hint: str | None = None) -> None:
    print(f"❌ {prefix}: {exc}", file=sys.stderr)
    if hint:
        print(f"   → {hint}", file=sys.stderr)
    if _debug_enabled():
        traceback.print_exc(file=sys.stderr)
    else:
        print("   Re-run with --debug for traceback details.", file=sys.stderr)


def _match_patterns(name: str, patterns: list[str]) -> bool:
    import re as _re

    return any(_re.search(pattern, name) for pattern in patterns or [])


def _numeric_suffix_key(name: str) -> tuple[int, str]:
    import re as _re

    match = _re.search(r'_(\d+)(?:_|$)', name)
    return (int(match.group(1)), name) if match else (0, name)


def _sort_entries(entries: list[dict], sort_mode: str | None) -> list[dict]:
    if sort_mode == "numeric_suffix":
        return sorted(entries, key=lambda entry: _numeric_suffix_key(entry["name"]))
    return sorted(entries, key=lambda entry: entry["name"])


def _resolve_interface_metadata(interface_metadata: dict | None) -> dict:
    return copy.deepcopy(interface_metadata or {})


def _consumer_root(anchor_path: Path | None = None, explicit_root: Path | None = None) -> Path:
    if explicit_root is not None:
        return Path(explicit_root).expanduser().resolve()

    env_root = os.environ.get("TOPGEN_CONSUMER_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    if anchor_path is not None:
        resolved_anchor = Path(anchor_path).expanduser().resolve()
        return resolved_anchor if resolved_anchor.is_dir() else resolved_anchor.parent

    raise ValueError(
        "Cannot determine consumer root. Pass --consumer-root, set TOPGEN_CONSUMER_ROOT, or provide an input path that can anchor relative defaults."
    )


def _resolve_path(path_value: Path | str | None, base_root: Path, default: Path | str | None = None) -> Path | None:
    candidate = path_value if path_value is not None else default
    if candidate is None:
        return None
    path = Path(candidate).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (base_root / path).resolve()


def _remove_path(path: Path, removed: list[Path]) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
    removed.append(path)


def _load_verify_flow_entries(verify_design_path: Path) -> list[tuple[str, str | None]]:
    import yaml as _yaml

    raw = _yaml.safe_load(verify_design_path.read_text()) or {}
    entries: list[tuple[str, str | None]] = []
    for flow in raw.get("flows", []) or []:
        if not isinstance(flow, dict):
            continue
        flow_name = flow.get("name")
        if not flow_name:
            continue
        flow_kind = flow.get("kind")
        entries.append((str(flow_name), str(flow_kind) if flow_kind else None))
    return entries


def cmd_clean(args):
    """Remove generated artifacts for one design without touching IP/HLS trees."""
    try:
        design_path = Path(args.design).expanduser().resolve()
        if not design_path.exists():
            print(f"❌ Design file not found: {design_path}", file=sys.stderr)
            print("   → Check the design.yml path and re-run arc clean.", file=sys.stderr)
            sys.exit(1)

        consumer_root = _consumer_root(design_path, getattr(args, 'consumer_root', None))
        output = _resolve_path(args.output, consumer_root, Path("algo_top.v"))
        output_dir = output.parent
        ip_info_file = _resolve_path(args.ip_info, consumer_root) if args.ip_info else output_dir / "ip_info.yaml"

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
        if getattr(args, 'verify_design', None):
            verify_design = _resolve_path(args.verify_design, consumer_root)
        elif not getattr(args, 'no_verify', False):
            candidate = design_path.parent.parent / "verify" / "design.verification.yml"
            if candidate.exists():
                verify_design = candidate.resolve()

        removed: list[Path] = []
        planned: list[Path] = []

        for artifact in generated_paths:
            if artifact.exists():
                planned.append(artifact)

        if verify_design and verify_design.exists():
            verify_root = verify_design.parent
            for flow_name, flow_kind in _load_verify_flow_entries(verify_design):
                flow_dirs = [verify_root / flow_name]
                if flow_kind:
                    flow_dirs.append(verify_root / flow_kind / flow_name)
                for flow_dir in flow_dirs:
                    planned.extend(path for path in [
                        flow_dir / "verify.flow.yml",
                        flow_dir / "wave.tcl",
                        flow_dir / "port_map.yaml",
                        flow_dir / "stimulus_current.svh",
                        flow_dir / "xsim_work",
                        flow_dir / "stimulus",
                    ] if path.exists())
                    planned.extend(path for path in sorted(flow_dir.glob("tb_*.sv")) if path.exists())

        # Preserve order while deduplicating.
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
            rel = path.relative_to(consumer_root) if path.is_relative_to(consumer_root) else path
            print(f"  - {rel}")

        if getattr(args, 'dry_run', False):
            print("Dry run only; no files were removed.")
            return

        for path in unique_planned:
            _remove_path(path, removed)

        print(f"✓ Removed {len(removed)} generated artifact(s)")
    except Exception as e:
        _print_cli_error(
            "Clean failed",
            e,
            hint="Check the design path, output path, and verify-design path, then retry arc clean.",
        )
        sys.exit(1)


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
            f"probe_map.yaml hash {probe_sig_hash!r} does not match port_map.yaml hash {port_sig_hash!r}"
        )

    tb_bindings_text = tb_bindings_path.read_text()
    if f"// Port sig hash: {port_sig_hash}" not in tb_bindings_text:
        raise ValueError(
            f"tb_bindings.svh at {tb_bindings_path} does not match port_map.yaml hash {port_sig_hash}"
        )

    for group_name, group_data in port_groups.items():
        if group_name in {"clock_reset", "bx0", "outputs", "unclassified"}:
            entries = group_data if isinstance(group_data, list) else []
        elif isinstance(group_data, dict) and "channels" in group_data:
            entries = group_data.get("channels", [])
        elif isinstance(group_data, dict) and "ports" in group_data:
            # Covers config-style groups and grouped (grouped-style) groups;
            # for grouped groups, also include ports from sub-groups.
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
    # Validate per-group counts generically: each input group count in port_map must
    # match the corresponding count in design_parameters.json interfaces.groups.
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
                f"design_parameters.json interfaces.groups.{group_name}.count={observed_count!r} "
                f"does not match generated port_map count {port_map_count}"
            )

    print("  ✓ Generated contracts validated")
    print(f"    - Port signature hash: {port_sig_hash}")
    print("    - port_map.yaml / probe_map.yaml / tb_bindings.svh / design_parameters.json are aligned")


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
    from glob import glob

    def find_ip_verilog_dir(top_name):
        top_file_matches = sorted(ip_root.rglob(f"{top_name}.v"))
        for top_file in top_file_matches:
            if top_file.parent.is_dir():
                return top_file.parent, top_file.parent.parent.parent
        return None, None
    
    manifest = {
        'project_root': str((project_root or algo_top.parent).resolve()),
        'design_file': str(design_file.resolve()),
        'algorithm_top': str(algo_top.resolve()),
        'top_module': algo_top.stem,
        'timestamp': datetime.datetime.now().isoformat(),
        'verilog_files': [],
        'include_dirs': [],
        'modules': {}
    }
    
    # Add algorithm top
    manifest['verilog_files'].append(str(algo_top.resolve()))
    manifest['include_dirs'].append(str(algo_top.parent.resolve()))
    
    if hls_build_root is not None:
        manifest['hls_build_root'] = str(hls_build_root.resolve())
    
    # Process each module from design
    for module in cfg.modules:
        module_name = module.name
        module_info = {
            'name': module_name,
            'top': module.top,
            'kind': module.kind,
            'verilog_files': [],
            'include_dirs': []
        }
        
        verilog_dir = None
        
        # RTL modules: source files specified directly in design.yml
        if module.kind == 'rtl' and hasattr(module, 'src') and module.src:
            rtl_files = []
            rtl_sources = module.abs_src if getattr(module, 'abs_src', None) else [Path(src) for src in module.src]
            for src_path in rtl_sources:
                src_path = Path(src_path)
                if src_path.exists():
                    rtl_files.append(str(src_path.resolve()))
                    if src_path.parent not in [Path(d) for d in module_info['include_dirs']]:
                        module_info['include_dirs'].append(str(src_path.parent.resolve()))
            
            if hasattr(module, 'rtl_packages') and module.rtl_packages:
                rtl_packages = module.abs_rtl_packages if getattr(module, 'abs_rtl_packages', None) else [Path(pkg) for pkg in module.rtl_packages]
                for pkg_path in rtl_packages:
                    pkg_path = Path(pkg_path)
                    if pkg_path.exists():
                        rtl_files.append(str(pkg_path.resolve()))
                        if pkg_path.parent not in [Path(d) for d in module_info['include_dirs']]:
                            module_info['include_dirs'].append(str(pkg_path.parent.resolve()))
            
            if rtl_files:
                module_info['verilog_files'] = rtl_files
                module_info['rtl_sources'] = rtl_files
                manifest['verilog_files'].extend(rtl_files)
                manifest['include_dirs'].extend(module_info['include_dirs'])
        
        # HLS modules: find verilog in IP packages or build_hls
        elif module_name in ip_info:
            ip_data = ip_info[module_name]
            
            # Try to find verilog directory
            verilog_dir = None
            ip_dir = None

            # Prefer resolving the IP from the actual top-module source file.
            verilog_dir, ip_dir = find_ip_verilog_dir(module.top)
            if verilog_dir and ip_dir:
                module_info['ip_dir'] = str(ip_dir.resolve())
            
            # For IP packages: The IP directory is named {component_name}_ip
            # where component_name = module.top (the actual component name)
            # Examples:
            #   module.top="input_adapter" → IP dir="input_adapter_ip"
            #   module.top="subdetector_concentrator" → IP dir="concentrator_ip" (special case)
            
            # Build candidate list with exact matching first
            # IP directory follows consistent rule: {component_name}_ip
            # where component_name is module.top from design.yml
            ip_dir = ip_root / f"{module.top}_ip"
            
            if not verilog_dir and ip_dir.exists() and ip_dir.is_dir():
                # Found IP directory, now find verilog
                verilog_candidates = [
                    ip_dir / "hdl" / "verilog",
                    ip_dir / "hdl",
                    ip_dir / "src",
                    ip_dir / "verilog",
                ]
                
                for vdir in verilog_candidates:
                    if vdir.exists() and vdir.is_dir():
                        verilog_dir = vdir
                        module_info['ip_dir'] = str(ip_dir.resolve())
                        break
            
            # If not found in IPs, try build_hls
            if not verilog_dir and hls_build_root is not None:
                build_candidates = [
                    hls_build_root / 'build_hls' / module_name / 'solution1' / 'syn' / 'verilog',
                    hls_build_root / 'build_hls' / module_name / 'solution1' / 'sim' / 'verilog',
                    hls_build_root / module_name / 'solution1' / 'syn' / 'verilog',
                    hls_build_root / module_name / 'solution1' / 'sim' / 'verilog',
                ]
                for candidate in build_candidates:
                    if candidate.exists() and candidate.is_dir():
                        verilog_dir = candidate
                        module_info['hls_solution_dir'] = str(candidate.parent.parent.resolve())
                        break
            
            # Collect verilog files from discovered directory and any sibling IP wrapper directory.
            if verilog_dir:
                verilog_dirs = [verilog_dir]
                ip_wrapper_dir = verilog_dir.parent / 'ip'
                if ip_wrapper_dir.exists() and ip_wrapper_dir.is_dir():
                    verilog_dirs.append(ip_wrapper_dir)

                v_files = []
                include_dirs = []
                for source_dir in verilog_dirs:
                    source_files = [
                        f for f in source_dir.glob('*.v')
                        if 'autotb' not in f.name and f.name != 'glbl.v'
                    ]
                    v_files.extend(source_files)
                    include_dirs.append(str(source_dir.resolve()))

                module_info['verilog_files'] = [str(f.resolve()) for f in v_files]
                module_info['include_dirs'] = include_dirs
                module_info['verilog_dir'] = str(verilog_dir.resolve())
                
                # Add to global lists
                manifest['verilog_files'].extend(module_info['verilog_files'])
                manifest['include_dirs'].extend(include_dirs)
        
        manifest['modules'][module_name] = module_info
    
    # Remove duplicates
    manifest['verilog_files'] = list(dict.fromkeys(manifest['verilog_files']))
    manifest['include_dirs'] = list(dict.fromkeys(manifest['include_dirs']))
    
    # Statistics
    manifest['statistics'] = {
        'total_verilog_files': len(manifest['verilog_files']),
        'total_include_dirs': len(manifest['include_dirs']),
        'total_modules': len(manifest['modules']),
        'hls_modules': sum(1 for m in manifest['modules'].values() if m['kind'] == 'hls'),
        'rtl_modules': sum(1 for m in manifest['modules'].values() if m['kind'] == 'rtl'),
    }
    
    # Write JSON
    with open(manifest_output, 'w') as f:
        json.dump(manifest, f, indent=2)
    
        print(f"  ✓ Build manifest: {manifest_output}")
    print(f"    - {manifest['statistics']['total_verilog_files']} Verilog files")
    print(f"    - {manifest['statistics']['total_modules']} modules ({manifest['statistics']['hls_modules']} HLS + {manifest['statistics']['rtl_modules']} RTL)")


def generate_port_map(verilog_path: Path, output_path: Path, interface_metadata: dict | None = None) -> None:
    """
    Scan the generated algo_top.v port list and write port_map.yaml.

    Implements Phase 0.5 of XSIM_FULL_CHIP_ALGORITHM_TESTER_PLAN.
    port_map.yaml is the authoritative record of DUT interface shape, port
    groupings by function, CSP packed field offsets, and a port-signature hash
    that detects structural changes between regenerations.

    This file must never contain source-selection decisions; those belong
    exclusively in build_manifest.json.
    """
    import re as _re
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
    # Discover all "grouped" (grouped-style) input groups generically — those with a "groups" sub-list
    grouped_style_cfg: dict[str, list] = {
        name: cfg["groups"]
        for name, cfg in input_groups.items()
        if cfg.get("groups")
    }
    # Flatten all sub-group defs for port classification
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
        grouped_inputs[group_name] = _sort_entries(group_entries, input_groups[group_name].get("sort"))

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

    # Port-signature hash — canonicalization lives in utils/port_signature.py
    port_sig_hash = _sig.compute(ports)

    tier2_probes = copy.deepcopy(metadata.get("tier2_probes", []))
    for probe in tier2_probes:
        if probe.get("name") == "mem_ref_hit" and probe.get("width") == 46:
            probe["width"] = _port_width("mem_out_ref_hit", 46)

    # Build dynamic port_groups for all consumer-defined input groups
    dynamic_port_groups: dict = {}
    for group_name, group_entries in grouped_inputs.items():
        group_cfg = input_groups.get(group_name, {})
        if group_cfg.get("stimulus_groups"):
            # "Config-style" group: has sub-groups for configuration registers
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
            # Simple channel group
            dynamic_port_groups[group_name] = {
                "count": len(group_entries),
                "channel_width": group_entries[0]["width"] if group_entries else 0,
                "field_map": group_cfg.get("field_map", {}),
                "stimulus_prefix": group_cfg.get("stimulus_prefix"),
                "channels": [{"index": i, **e} for i, e in enumerate(group_entries)],
            }

    # Build the grouped-style (grouped-style) groups under their consumer-defined parent names
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
        1 for k, v in port_map["port_groups"].items()
        if k not in ("unclassified",) and (
            (isinstance(v, list) and v) or
            (isinstance(v, dict) and v.get("count", 0) > 0)
        )
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_yaml.dump(port_map, default_flow_style=False, sort_keys=False))
    print(f"  ✓ Port map:  {output_path}")
    print(f"    - {len(ports)} ports in {num_nonempty_groups} functional groups")
    print(f"    - Port signature hash: {port_sig_hash}")

    return port_map, port_sig_hash


def generate_probe_map(port_map: dict, output_path: Path) -> None:
    """
    Generate probe_map.yaml from the already-generated port_map data.

    probe_map.yaml is the dedicated probe metadata artifact for XSIM flows.
    It intentionally reuses the exact same source of truth as port_map.yaml so
    probe names, widths, and the port-signature hash stay aligned.
    """
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


def generate_tb_bindings(port_map: dict, output_path: "Path") -> None:
    """
    Generate tb_bindings.svh from a port_map dict (already constructed in memory).

    tb_bindings.svh is a SystemVerilog include file that declares every DUT port
    as a TB-side signal — inputs as 'logic', outputs as 'wire'.  A testbench can
    include it with `\`include "tb_bindings.svh"` and then connect the DUT with
    `algo_top dut (.*);` without hardcoding any port widths.

    Implements Phase 0.5 of XSIM_DEFINITIVE_ADAPTATION.
    """
    lines = []
    sig_hash = port_map.get("port_signature_hash", "unknown")
    top_module = port_map.get("top_module", "algo_top")
    groups = port_map.get("port_groups", {})

    lines.append("// ============================================================")
    lines.append(f"// tb_bindings.svh — Generated by arc gen-top")
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

    # clock_reset
    cr = groups.get("clock_reset", [])
    if cr:
        lines.append("// -- clock / reset --")
        for e in cr:
            lines.append(_decl(e))
        lines.append("")

    # bx0
    bx0 = groups.get("bx0", [])
    if bx0:
        lines.append("// -- BX0 pulse --")
        for e in bx0:
            lines.append(_decl(e))
        lines.append("")

    # Iterate over all consumer-defined input groups generically
    SPECIAL_GROUPS = {"clock_reset", "bx0", "outputs", "unclassified"}
    for group_name, group_data in groups.items():
        if group_name in SPECIAL_GROUPS:
            continue
        if not group_data:
            continue
        if isinstance(group_data, dict) and "channels" in group_data:
            # Channel-style group declared by interface metadata.
            channels = group_data.get("channels", [])
            w = group_data.get("channel_width", 1)
            lines.append(f"// -- {group_name} ({len(channels)} × {w}-bit) --")
            for ch in channels:
                lines.append(f"logic [{w-1}:0] {ch['name']};")
            lines.append("")
        elif isinstance(group_data, dict) and "stimulus_groups" in group_data:
            # Config-style group (has stimulus_groups sub-structure)
            ports = group_data.get("ports", [])
            w = group_data.get("width", 64)
            lines.append(f"// -- {group_name} ({len(ports)} × {w}-bit) --")
            for p in ports:
                lines.append(f"logic [{w-1}:0] {p['name']};")
            lines.append("")
        elif isinstance(group_data, dict) and "groups" in group_data:
            # Grouped (grouped-style) group — emit ports from all sub-groups
            grouped_ports_flat = group_data.get("ports", [])
            if grouped_ports_flat:
                lines.append(f"// -- {group_name} --")
                for e in grouped_ports_flat:
                    lines.append(_decl(e))
                lines.append("")
        elif isinstance(group_data, dict) and "ports" in group_data:
            # Generic port-list group
            ports = group_data.get("ports", [])
            w = group_data.get("width", 1)
            lines.append(f"// -- {group_name} ({len(ports)} × {w}-bit) --")
            for p in ports:
                lines.append(f"logic [{w-1}:0] {p['name']};")
            lines.append("")

    # outputs (wire — driven by DUT)
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

    # unclassified top-level ports
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


def cmd_unpack_ips(args):
    """Unpack IP archives."""
    try:
        src_dir = Path(args.src).expanduser().resolve()
        consumer_root = _consumer_root(src_dir, getattr(args, 'consumer_root', None))
        ip_root = _resolve_path(args.ip_root, consumer_root, Path("ips"))
        extracted = unpack_ip_archives(
            src_dir=args.src,
            ip_root=ip_root,
            force=args.force,
            verbose=True
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

        consumer_root = _consumer_root(design_path, getattr(args, 'consumer_root', None))
        build_root = _resolve_path(args.build_dir, consumer_root, Path("build"))
        ip_root = _resolve_path(args.ip_root, consumer_root, Path("ips"))
        src_root = _resolve_path(args.src_root, consumer_root) if hasattr(args, 'src_root') and args.src_root else design_path.parent
        
        print(f"📋 Collecting IP metadata from {ip_root}...")
        summary = collect_all(
            build_root=build_root,
            modules=cfg.modules,
            ip_root=ip_root,
            src_root=src_root
        )
        
        if not any(v is not None for v in summary.values()):
            print("❌ No IP metadata found. Run unpack-ips first.")
            sys.exit(1)
        
        output = _resolve_path(args.output, consumer_root, Path("ip_info.yaml"))
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

        consumer_root = _consumer_root(design_path, getattr(args, 'consumer_root', None))
        ip_info_file = _resolve_path(args.ip_info, consumer_root, Path("ip_info.yaml"))
        if not ip_info_file.exists():
            print(f"❌ IP info file not found: {ip_info_file}")
            print("   Run 'arc ip-summary' first")
            sys.exit(1)
        
        print(f"🔗 Matching ports...")
        ip_info = load_ip_info(ip_info_file)
        conn_map, global_nets, match_report = auto_match_ports(cfg, ip_info, system_yml=args.system)
        
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
        
        # Check if files exist
        for vfile in vhdl_files:
            if not vfile.exists():
                print(f"❌ File not found: {vfile}")
                sys.exit(1)
        
        # Lint files
        passed, failed = lint_vhdl_files(
            vhdl_files,
            std=args.std,
            ieee=args.ieee,
            fix=args.fix
        )
        
        if failed > 0:
            sys.exit(1)
            
    except ImportError as e:
        print(f"❌ VHDL linter import error: {e}")
        sys.exit(1)
    except Exception as e:
        _print_cli_error(
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
        
        # Check if files exist
        for vfile in verilog_files:
            if not vfile.exists():
                print(f"❌ File not found: {vfile}")
                sys.exit(1)
        
        # Lint files
        passed, failed = lint_verilog_files(
            verilog_files,
            top_module=args.top_module,
            show_warnings=not args.no_warnings,
            strict=args.strict
        )
        
        if failed > 0:
            sys.exit(1)
            
    except Exception as e:
        _print_cli_error(
            "Verilog lint failed",
            e,
            hint="Check that the requested files exist and that the Verilog lint toolchain is available.",
        )
        sys.exit(1)


def _find_framework_resource_root() -> "Path | None":
    """Find the framework root by walking up from the package file and CWD."""
    import os as _os
    # 1. Explicit env var (monorepo or installed consumer)
    for env_key in ("TOPGEN_CONSUMER_ROOT", "TOPGEN_FRAMEWORK_ROOT"):
        env_root = _os.environ.get(env_key)
        if env_root:
            candidate = Path(env_root) / "framework"
            if (candidate / "stubs" / "verilog").exists():
                return candidate
    # 2. Walk up from CWD then from the package file location
    for start in (Path.cwd(), Path(__file__).resolve()):
        p = start
        for _ in range(15):
            candidate = p / "framework"
            if (candidate / "stubs" / "verilog").exists():
                return candidate
            if p.parent == p:
                break
            p = p.parent
    return None


def cmd_verify_contract(args):
    """Verify one or more ip_interface.yaml contracts against ip_info.yaml."""
    import sys
    from arc.topgen.ip.contract_verifier import (
        ContractVerifier,
        verify_all,
        find_contracts,
        VerifyResult,
    )

    ip_info_path = Path(args.ip_info)
    if not ip_info_path.exists():
        print(f"❌ ip_info file not found: {ip_info_path}")
        sys.exit(2)

    verbose = getattr(args, "verbose", False)

    # ── Single-contract mode ──────────────────────────────────────────────
    if args.contract:
        contract_path = Path(args.contract)
        if not contract_path.exists():
            print(f"❌ Contract file not found: {contract_path}")
            sys.exit(2)
        verifier = ContractVerifier(ip_info_path, contract_path)
        result = verifier.verify()
        result.print_report(verbose=verbose)
        sys.exit(result.exit_code())

    # ── All-contracts mode ────────────────────────────────────────────────
    search_dirs = [Path(d) for d in (args.all_contracts or [])]
    if not search_dirs:
        print("❌ Provide --contract <file> or --all-contracts <dir> [<dir> …]")
        sys.exit(2)

    results = verify_all(ip_info_path, search_dirs, verbose=verbose)
    if not results:
        sys.exit(1)

    n_pass = sum(1 for r in results if r.passed)
    n_warn = sum(1 for r in results if r.passed and r.warnings)
    n_fail = sum(1 for r in results if not r.passed)

    print()
    print(f"Summary: {len(results)} contracts checked — "
          f"{n_pass} pass ({n_warn} with warnings), {n_fail} fail")

    if n_fail:
        sys.exit(2)
    elif n_warn:
        sys.exit(1)
    else:
        sys.exit(0)


def cmd_resources(args):
    """Print installed framework resource paths."""
    import json as _json

    resources: dict[str, str] = {}

    if getattr(args, "key", None):
        value = resources.get(args.key)
        if value is None:
            print(f"ERROR: unknown resource key '{args.key}'. Available keys: {', '.join(resources)}", file=sys.stderr)
            sys.exit(1)
        print(value)
        return

    if getattr(args, "format", None) == "json":
        print(_json.dumps(resources, indent=2))
    else:
        for k, v in resources.items():
            print(f"{k}={v}")


# ---------------------------------------------------------------------------
# HLS subcommand helpers
# ---------------------------------------------------------------------------

def _find_parallel_hls_script() -> "Path | None":
    """Locate the parallel_hls.sh backend script.

    Resolution order:
    1. ``TOPGEN_PARALLEL_HLS_SCRIPT`` environment variable
      2. repo-layout walk-up fallback (for monorepo/source-tree use)
    """
    env_override = os.environ.get("TOPGEN_PARALLEL_HLS_SCRIPT")
    if env_override:
        candidate = Path(env_override).expanduser().resolve()
        if candidate.is_file():
            return candidate

    for start in (Path.cwd(), Path(__file__).resolve()):
        p = start
        for _ in range(15):
            candidate = p / "hls" / "parallel_hls.sh"
            if candidate.is_file():
                return candidate
            if p.parent == p:
                break
            p = p.parent
    return None


def _find_hls_templates_dir() -> "Path | None":
    """Locate the HLS template directory.

    Resolution order:
    1. ``TOPGEN_HLS_TEMPLATES`` environment variable
      2. repo-layout walk-up fallback (for monorepo/source-tree use)
    """
    env_override = os.environ.get("TOPGEN_HLS_TEMPLATES")
    if env_override:
        candidate = Path(env_override).expanduser().resolve()
        if candidate.is_dir():
            return candidate

    for start in (Path.cwd(), Path(__file__).resolve()):
        p = start
        for _ in range(15):
            candidate = p / "hls" / "templates"
            if candidate.is_dir():
                return candidate
            if p.parent == p:
                break
            p = p.parent
    return None


def cmd_hls_gen_tcl(args):
    """Generate Vitis HLS TCL scripts for one or all catalog modules."""
    from arc.hls.catalog import (
        load_hls_catalog,
        generate_tcl,
        SUPPORTED_STAGES,
    )

    try:
        catalog = load_hls_catalog(args.hls_config)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    template_dir = getattr(args, "template_dir", None)
    if template_dir is None:
        template_dir = _find_hls_templates_dir()
        if template_dir is None:
            print(
                "ERROR: cannot locate hls/templates/. "
                "Pass --template-dir explicitly.",
                file=sys.stderr,
            )
            sys.exit(1)

    output_dir = Path(args.output_dir) if getattr(args, "output_dir", None) else Path("build_hls")
    ip_packages_dir = Path(args.ip_packages_dir) if getattr(args, "ip_packages_dir", None) else None

    config_override: dict = {}
    if getattr(args, "flow", None):
        config_override["flow"] = args.flow

    module_arg = getattr(args, "module", None) or "all"
    modules = catalog["ordered_modules"] if module_arg == "all" else [module_arg]

    ok = True
    for module_name in modules:
        print(f"\n=== Generating TCL for {module_name} ===")
        if not generate_tcl(catalog, module_name, template_dir, output_dir, ip_packages_dir, config_override):
            ok = False

    sys.exit(0 if ok else 1)


def cmd_hls_run(args):
    """Run parallel Vitis HLS jobs — pure Python, no shell script required."""
    import concurrent.futures
    import subprocess

    from arc.hls.catalog import load_hls_catalog

    # ── Resolve inputs ────────────────────────────────────────────────────
    registry_path = Path(args.registry).expanduser().resolve() if getattr(args, "registry", None) else None

    build_root = Path(args.hls_build_root).expanduser().resolve() if getattr(args, "hls_build_root", None) else Path("build_hls").resolve()
    ip_pack_dir = Path(args.ip_packages_dir).expanduser().resolve() if getattr(args, "ip_packages_dir", None) else build_root / "ip_packages"
    vitis_hls = str(Path(args.vitis_hls).expanduser().resolve()) if getattr(args, "vitis_hls", None) else "vitis_hls"
    stage = getattr(args, "stage", None)
    modules_arg = getattr(args, "modules", None) or "all"
    jobs = getattr(args, "jobs", None) or 1
    stages_arg = getattr(args, "stages", None)

    # ── Load catalog if provided ──────────────────────────────────────────
    catalog = None
    if registry_path and registry_path.exists():
        try:
            catalog = load_hls_catalog(str(registry_path))
        except Exception as exc:
            print(f"ERROR loading registry {registry_path}: {exc}", file=sys.stderr)
            sys.exit(1)

    # ── Resolve module list ───────────────────────────────────────────────
    if modules_arg == "all":
        if catalog is None:
            print("ERROR: --registry is required when --modules all is used.", file=sys.stderr)
            sys.exit(1)
        module_list = catalog["ordered_modules"]
    else:
        module_list = [m.strip() for m in modules_arg.replace(",", " ").split() if m.strip()]

    if not module_list:
        print("ERROR: no modules resolved.", file=sys.stderr)
        sys.exit(1)

    # ── Resolve stages ────────────────────────────────────────────────────
    # stages_arg is a comma-separated list like "csim,synth,cosim,export"
    # stage is a single stage (legacy arg kept for backward compat)
    if stages_arg:
        stage_list = [s.strip() for s in stages_arg.replace(",", " ").split() if s.strip()]
    elif stage:
        stage_list = [stage]
    else:
        stage_list = ["csim", "synth", "cosim", "export"]

    valid_stages = {"csim", "synth", "cosim", "export"}
    bad = [s for s in stage_list if s not in valid_stages]
    if bad:
        print(f"ERROR: invalid stage(s): {', '.join(bad)}. Valid: {', '.join(sorted(valid_stages))}", file=sys.stderr)
        sys.exit(1)

    # ── Build dir setup ───────────────────────────────────────────────────
    build_root.mkdir(parents=True, exist_ok=True)
    ip_pack_dir.mkdir(parents=True, exist_ok=True)

    # ── Completion detectors (port of parallel_hls.sh heuristics) ────────
    def _is_project_initialized(module: str) -> bool:
        return (build_root / module / "solution1" / "solution1.aps").exists()

    def _is_csim_complete(module: str) -> bool:
        csim_dir = build_root / module / "solution1" / "csim"
        if not csim_dir.exists():
            return False
        log = build_root / module / "logs" / "csim.log"
        if log.exists():
            return "PASS" in log.read_text().upper()
        return True  # csim dir exists but no log → assume done

    def _is_synth_complete(module: str) -> bool:
        report_dir = build_root / module / "solution1" / "syn" / "report"
        if not report_dir.exists():
            return False
        return any(report_dir.glob("*csynth.rpt"))

    def _is_cosim_complete(module: str) -> bool:
        mod_dir = build_root / module
        has_dir = (mod_dir / "solution1" / "sim").exists() or (mod_dir / "solution1" / "cosim").exists()
        if not has_dir:
            return False
        log = mod_dir / "logs" / "cosim.log"
        if log.exists():
            return "PASS" in log.read_text().upper()
        return False  # conservative: require log to confirm

    def _is_export_complete(module: str) -> bool:
        mod_dir = ip_pack_dir / module
        if mod_dir.is_dir() and any(mod_dir.iterdir()):
            return True
        return any(ip_pack_dir.glob(f"{module}*.zip"))

    def _is_step_complete(module: str, step: str) -> bool:
        return {"csim": _is_csim_complete, "synth": _is_synth_complete,
                "cosim": _is_cosim_complete, "export": _is_export_complete}[step](module)

    # ── Per-module, per-stage runner ──────────────────────────────────────
    def _run_tcl(module: str, tcl_name: str, log_name: str) -> "tuple[bool, str]":
        """Run vitis_hls -f <tcl_name> inside build_root/module/. Returns (ok, log_path)."""
        mod_dir = build_root / module
        tcl_path = mod_dir / tcl_name
        log_dir = mod_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = str(log_dir / log_name)

        if not tcl_path.exists():
            with open(log_path, "w") as f:
                f.write(f"ERROR: TCL script not found: {tcl_path}\n")
                f.write("Generate TCL scripts first: arc hls gen-tcl ...\n")
            return False, log_path

        with open(log_path, "w") as log_file:
            result = subprocess.run(
                [vitis_hls, "-f", tcl_name],
                cwd=str(mod_dir),
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        return result.returncode == 0, log_path

    def _run_stage_for_module(module: str, step: str) -> "tuple[str, str, bool, str]":
        """Run one stage for one module. Returns (module, step, ok, log_path)."""
        tcl_map = {"csim": "csim.tcl", "synth": "synth.tcl",
                   "cosim": "cosim.tcl", "export": "ip_export.tcl"}
        log_map = {"csim": "csim.log", "synth": "synth.log",
                   "cosim": "cosim.log", "export": "ip_export.log"}

        if _is_step_complete(module, step):
            print(f"  [SKIP] {step:6s} already done for {module}")
            return module, step, True, ""

        # For export: ensure project + synth are present first (sequential, not parallel)
        if step == "export":
            if not _is_project_initialized(module):
                ok, log = _run_tcl(module, "project.tcl", "project.log")
                if not ok:
                    return module, step, False, log
            if not _is_synth_complete(module):
                ok, log = _run_tcl(module, "synth.tcl", "synth.log")
                if not ok:
                    return module, step, False, log
        else:
            if not _is_project_initialized(module):
                ok, log = _run_tcl(module, "project.tcl", "project.log")
                if not ok:
                    return module, step, False, log

        ok, log = _run_tcl(module, tcl_map[step], log_map[step])
        return module, step, ok, log

    # ── Driver ────────────────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"  arc hls run")
    print(f"{'='*50}")
    print(f"  Modules : {', '.join(module_list)}")
    print(f"  Stages  : {', '.join(stage_list)}")
    print(f"  Jobs    : {jobs}")
    print(f"  Build   : {build_root}")
    print(f"  IPs     : {ip_pack_dir}")
    print(f"{'='*50}\n")

    # Stages run sequentially; within each stage modules run in parallel
    overall_ok = True
    for step in stage_list:
        print(f"--- Stage: {step} ---")
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            futs = {pool.submit(_run_stage_for_module, m, step): m for m in module_list}
            for fut in concurrent.futures.as_completed(futs):
                module, _step, ok, log_path = fut.result()
                status = "OK  " if ok else "FAIL"
                log_hint = f"  (log: {log_path})" if log_path and not ok else ""
                print(f"  [{status}] {module}{log_hint}")
                results.append((module, ok))

        failed = [m for m, ok in results if not ok]
        if failed:
            print(f"  Stage {step}: {len(failed)} module(s) FAILED: {', '.join(failed)}")
            overall_ok = False
            break  # stop pipeline on first failing stage
        print(f"  Stage {step}: all {len(module_list)} module(s) OK\n")

    if overall_ok:
        print("arc hls run: all stages completed successfully.")
    else:
        print("arc hls run: completed with failures.", file=sys.stderr)
        sys.exit(1)



def cmd_validate(args):
    """Validate design.yml without generating output."""
    try:
        design_path = Path(args.design).expanduser().resolve()

        # Check file exists
        if not design_path.exists():
            print(f"❌ Design file not found: {design_path}")
            sys.exit(1)

        # --- Registry validation (runs first when the design references one) ---
        import yaml as _yaml
        _raw_design = _yaml.safe_load(design_path.read_text()) or {}
        _registry_ref = _raw_design.get('registry')
        registry_errors = 0
        if _registry_ref:
            registry_path = (design_path.parent / _registry_ref).resolve()
            if not registry_path.exists():
                print(f"❌ Registry file not found: {registry_path}")
                sys.exit(1)
            print(f"📖 Loading registry: {registry_path}")
            print(f"🔍 Validating registry...\n")
            reg_validator = validate_registry(registry_path)
            reg_validator.print_report()
            registry_errors = len(reg_validator.errors)
            if registry_errors:
                print(f"\n⛔ Registry has {registry_errors} error(s). Fix them before validating design.")
                sys.exit(1)
            if args.strict and reg_validator.warnings:
                print(f"\n⛔ Strict mode: treating {len(reg_validator.warnings)} registry warning(s) as errors")
                sys.exit(1)
            print()

        # --- Design validation ---
        print(f"📖 Loading design: {design_path}")
        cfg = DesignConfig.load_relaxed(design_path)

        print(f"🔍 Validating design configuration...\n")
        validator = validate_design(cfg, design_path)
        validator.print_report()

        if validator.has_errors():
            sys.exit(1)
        elif args.strict and len(validator.warnings) > 0:
            print(f"\n⛔ Strict mode: Treating {len(validator.warnings)} warnings as errors")
            sys.exit(1)
        else:
            # ── Optional stale-artifact check ──────────────────────────────────
            if getattr(args, "check_stale", False):
                print()
                print("🕒 Checking for stale generated artifacts…")
                stale_diag = ATGDiagnosticReport("stale-check")
                output_dir = design_path.parent
                stale_report = check_top_gen_staleness(
                    output_dir,
                    design_yml=design_path,
                )
                ip_info_candidates = (
                    output_dir / "ip_info.yaml",
                    output_dir.parent / "ip_info.yaml",
                )
                for _iic in ip_info_candidates:
                    if _iic.exists():
                        ip_stale = check_ip_info_staleness(
                            _iic,
                            design_yml=design_path,
                        )
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

            print(f"\n✅ Design is valid and ready for generation!")
            sys.exit(0)

    except Exception as e:
        _print_cli_error(
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
        print(f"🔍 Validating registry...\n")
        validator = validate_registry(registry_path)
        validator.print_report()

        if validator.has_errors():
            sys.exit(1)
        elif args.strict and validator.warnings:
            print(f"\n⛔ Strict mode: treating {len(validator.warnings)} warning(s) as errors")
            sys.exit(1)
        else:
            sys.exit(0)

    except Exception as e:
        _print_cli_error(
            "Registry validation failed",
            e,
            hint="Fix the modules.yml registry issue, then re-run arc validate-registry.",
        )
        sys.exit(1)




def cmd_gen_top(args):
    """Generate algorithm top (VHDL or Block Design TCL)."""
    try:
        design_path = Path(args.design).expanduser().resolve()

        # Stage 5: allow --rtl-resource-root to pre-set the env var so that
        # src: paths containing ${TOPGEN_RTL_RESOURCE_ROOT} are expanded
        # correctly when design config is loaded below.
        if getattr(args, "rtl_resource_root", None):
            os.environ["TOPGEN_RTL_RESOURCE_ROOT"] = str(
                Path(args.rtl_resource_root).expanduser().resolve()
            )

        # Validate design file exists
        if not design_path.exists():
            print(f"❌ Design file not found: {design_path}")
            print(f"   💡 Check the path and try again")
            sys.exit(1)
        
        # Load design configuration
        cfg = DesignConfig.load_relaxed(design_path)
        
        # VALIDATE DESIGN FIRST
        print(f"🔍 Validating design configuration...")
        validator = validate_design(cfg, design_path)
        validator.print_report()
        
        if validator.has_errors():
            sys.exit(1)
        
        print(f"✅ Validation passed!\n")
        
        consumer_root = _consumer_root(design_path, getattr(args, 'consumer_root', None))
        ip_root = _resolve_path(args.ip_root, consumer_root, Path("ips"))
        build_root = _resolve_path(args.build_dir, consumer_root, Path("build"))
        hls_build_root = _resolve_path(getattr(args, 'hls_build_root', None), consumer_root, Path("build_hls"))
        src_root = _resolve_path(args.src_root, consumer_root) if hasattr(args, 'src_root') and args.src_root else design_path.parent
        xml_stimulus_tool = _resolve_path(getattr(args, 'xml_stimulus_tool', None), consumer_root)
        
        # Determine the output directory before writing ip_info.yaml.
        if args.output:
            output = _resolve_path(args.output, consumer_root)
            output_dir = output.parent
            # Ensure output directory exists
            if output_dir != Path('.'):
                output_dir.mkdir(parents=True, exist_ok=True)
        else:
            output_dir = consumer_root
        
        # Set ip_info path to be in the output directory
        if args.ip_info:
            ip_info_file = _resolve_path(args.ip_info, consumer_root)
        else:
            ip_info_file = output_dir / "ip_info.yaml"

        # Load interface contracts FIRST — they drive contract-based wiring
        # and may be used to project ip_info for early structural generation
        # (before IPs are built).  Projected ip_info is a development
        # convenience; for production verification ip_info must come from
        # built IP via ip-summary / collect_all.
        _modules_yml = getattr(args, "contracts_from", None)
        _contracts = None
        if _modules_yml:
            _modules_yml_path = _resolve_path(_modules_yml, consumer_root)
            if _modules_yml_path.exists():
                try:
                    _contracts = load_contracts_for_design(_modules_yml_path, consumer_root)
                    print(f"📜 Contracts loaded: {len(_contracts)} module(s) covered")
                except Exception as _ce:
                    print(f"⚠️  Contract loading failed (falling back to heuristics): {_ce}")
            else:
                print(f"⚠️  --contracts-from file not found: {_modules_yml_path}")

        # Resolve ip_info (3-tier fallback):
        #   1. Observed file on disk      → authoritative physical truth
        #   2. Projected from contracts   → development convenience (not verified)
        #   3. Scanned from build artefacts → observed fallback
        if ip_info_file.exists():
            ip_info = load_ip_info(ip_info_file)
        elif _contracts:
            print("📋 Projecting ip_info from contracts (not from built IP) …")
            # Map instance names → contracts (design.yml uses instance names
            # like 'src' while contracts are keyed by canonical module names
            # like 'echo_source').  Try both m.name and m.ip_info_key.
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
            cfg, ip_info,
            system_yml=args.system,
            contracts=_contracts,
        )

        # Print contract-driven generation summary
        if match_report.contract_driven_modules:
            print(f"  Contract-driven : {', '.join(match_report.contract_driven_modules)}")
        if match_report.compat_mode_modules:
            print(f"  Compat-mode     : {', '.join(match_report.compat_mode_modules)}")
        wmc = match_report.wiring_method_counts
        total_conn = sum(wmc.values())
        if total_conn:
            print(f"  Wiring methods  : {total_conn} connections — "
                  f"{wmc['contract_wiring']} contract, "
                  f"{wmc['port_map_ranges']} ranges, "
                  f"{wmc['port_map']} port_map, "
                  f"{wmc['auto_match']} auto")
        for _w in match_report.warnings:
            print(f"  ⚠️  {_w}")

        # Strict-mode gate: fail if any module fell back to heuristics
        if getattr(args, "strict", False) and match_report.has_compat_modules():
            print("\n❌ Strict mode failure: the following modules have no interface"
                  " contract and were wired via heuristics:")
            for _m in match_report.compat_mode_modules:
                print(f"   • {_m}")
            print("   Add interface contracts or remove --strict to proceed.")
            sys.exit(1)

        # Strict-mode gate: fail if any connection used auto-match heuristics
        if getattr(args, "strict", False) and wmc["auto_match"] > 0:
            print(f"\n❌ Strict mode: {wmc['auto_match']} connection(s) used auto-match heuristics.")
            print("   Add explicit port_map, contract_wiring, or topology_groups to each connection.")
            sys.exit(1)

        # Strict-mode gate: fail if any connection uses port_map_ranges
        if getattr(args, "strict", False) and wmc.get("port_map_ranges", 0) > 0:
            print(f"\n❌ Strict mode: {wmc['port_map_ranges']} connection(s) still use port_map_ranges.")
            print("   Migrate to topology_groups (contract-driven) or remove --strict.")
            sys.exit(1)

        # Strict-mode gate: fail if topology_groups have verification issues
        if getattr(args, "strict", False) and _contracts and cfg.topology_groups:
            from arc.topgen.ip.contract_verifier import verify_topology_groups
            tg_issues = verify_topology_groups(cfg, _contracts)
            tg_errors = [i for i in tg_issues if i.severity == "error"]
            if tg_errors:
                print(f"\n❌ Strict mode: {len(tg_errors)} topology group verification error(s):")
                for _i in tg_errors:
                    print(str(_i))
                sys.exit(1)

        if args.mode == "vhdl":
            output = _resolve_path(args.output, consumer_root, Path(f"{args.top_name}.vhd"))
            print(f"🔨 Generating structural VHDL: {output}")
            
            report = write_structural_vhdl(
                cfg=cfg,
                ip_info=ip_info,
                conn_map=conn_map,
                global_nets=global_nets,
                ip_root=ip_root,
                out_path=output,
                top_name=args.top_name,
                system_yml=args.system
            )
            
            print(f"✓ VHDL top generated: {output}")
            
            # Print generation report
            print(f"\n{'='*70}")
            print(f"📊 GENERATION REPORT")
            print(f"{'='*70}")
            print(f"  Modules:     {report['total_modules']}")
            print(f"  Instances:   {report['total_instances']}")
            print(f"  Connections: {report['total_connections']}")
            
            if report['tied_to_zero']:
                print(f"\n⚠️  INPUTS TIED TO ZERO ({len(report['tied_to_zero'])}):")
                for inst, port, width in sorted(report['tied_to_zero']):
                    print(f"    {inst}.{port} [{width} bits]")
            
            if report['open_outputs']:
                print(f"\n⚠️  OUTPUTS LEFT OPEN ({len(report['open_outputs'])}):")
                for inst, port, width in sorted(report['open_outputs']):
                    print(f"    {inst}.{port} [{width} bits]")
            
            if not report['tied_to_zero'] and not report['open_outputs']:
                print(f"\n✅ All ports properly connected!")
            
            print(f"{'='*70}\n")
            
            # Strict gate: unaccounted open/tied ports
            if getattr(args, "strict", False):
                unaccounted_open = _filter_unaccounted(
                    report['open_outputs'], cfg.allowed_unconnected.open_outputs)
                unaccounted_tied = _filter_unaccounted(
                    report['tied_to_zero'], cfg.allowed_unconnected.tied_inputs)
                if unaccounted_open or unaccounted_tied:
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

            # Optional: Run VHDL linting if requested
            if args.lint:
                try:
                    from arc.core.utils.vhdl_linter import lint_vhdl_file
                    print(f"\n🔍 Running GHDL syntax checker...")
                    lint_vhdl_file(output, std="08", ieee="synopsys")
                except ImportError:
                    print("⚠️  VHDL linter requires GHDL. Please install it:")
                    print("   - Ubuntu/Debian: sudo apt-get install ghdl")
                    print("   - Fedora/RHEL: sudo dnf install ghdl")
                    print("   - macOS: brew install ghdl")
        
        elif args.mode == "verilog":
            output = _resolve_path(args.output, consumer_root, Path(f"{args.top_name}.v"))
            print(f"🔨 Generating structural Verilog: {output}")
            
            report = write_structural_verilog(
                cfg=cfg,
                ip_info=ip_info,
                conn_map=conn_map,
                global_nets=global_nets,
                ip_root=ip_root,
                out_path=output,
                top_name=args.top_name,
                system_yml=args.system
            )
            
            print(f"✓ Verilog top generated: {output}")
            
            # Print generation report
            print(f"\n{'='*70}")
            print(f"📊 GENERATION REPORT")
            print(f"{'='*70}")
            print(f"  Modules:     {report['total_modules']}")
            print(f"  Instances:   {report['total_instances']}")
            print(f"  Connections: {report['total_connections']}")
            
            if report['tied_to_zero']:
                print(f"\n⚠️  INPUTS TIED TO ZERO ({len(report['tied_to_zero'])}):")
                for inst, port, width in sorted(report['tied_to_zero']):
                    print(f"    {inst}.{port} [{width} bits]")
            
            if report['open_outputs']:
                print(f"\n⚠️  OUTPUTS LEFT OPEN ({len(report['open_outputs'])}):")
                for inst, port, width in sorted(report['open_outputs']):
                    print(f"    {inst}.{port} [{width} bits]")
            
            if not report['tied_to_zero'] and not report['open_outputs']:
                print(f"\n✅ All ports properly connected!")
            
            print(f"{'='*70}\n")
            
            # Strict gate: unaccounted open/tied ports
            if getattr(args, "strict", False):
                unaccounted_open = _filter_unaccounted(
                    report['open_outputs'], cfg.allowed_unconnected.open_outputs)
                unaccounted_tied = _filter_unaccounted(
                    report['tied_to_zero'], cfg.allowed_unconnected.tied_inputs)
                if unaccounted_open or unaccounted_tied:
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

            # Generate the simulation build manifest
            manifest_output = output.parent / "build_manifest.json"
            print(f"📦 Generating build manifest: {manifest_output}")
            generate_build_manifest(
                cfg=cfg,
                ip_info=ip_info,
                ip_root=ip_root,
                algo_top=output,
                design_file=design_path,
                manifest_output=manifest_output,
                project_root=consumer_root,
                hls_build_root=hls_build_root,
            )
            
            # Generate design parameters JSON
            params_output = output.parent / "design_parameters.json"
            print(f"\n📋 Generating design parameters: {params_output}")
            # Generate port map (Phase 0.5: port/interface canonicalization)
            port_map_output = output.parent / "port_map.yaml"
            print(f"\n🗺  Generating port map: {port_map_output}")
            port_map_data, port_sig_hash = generate_port_map(output, port_map_output, cfg.interface_metadata)

            # Emit stand-alone port_signature.json so runners can preflight without
            # parsing the full port_map.yaml.
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

            # Generate design parameters JSON
            params_output = output.parent / "design_parameters.json"
            print(f"\n📋 Generating design parameters: {params_output}")
            write_design_parameters(
                cfg,
                params_output,
                algo_top_v_path=output,
                hls_metrics_file=args.hls_metrics,
                port_map_data=port_map_data,
            )

            # Generate probe map (Phase 0.5: probe canonicalization)
            probe_map_output = None
            if port_map_data is not None:
                probe_map_output = output.parent / "probe_map.yaml"
                print(f"\n🔎 Generating probe map: {probe_map_output}")
                generate_probe_map(port_map_data, probe_map_output)

            # Generate TB bindings (Phase 0.5: tb_bindings.svh)
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

            # Emit machine-readable maturity report
            maturity_output = output.parent / "maturity_report.json"
            import json as _json
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
                        _filter_unaccounted(report.get("open_outputs", []),
                                            cfg.allowed_unconnected.open_outputs)),
                    "tied_accounted": len(report.get("tied_to_zero", [])) - len(
                        _filter_unaccounted(report.get("tied_to_zero", []),
                                            cfg.allowed_unconnected.tied_inputs)),
                },
                "strict_pass": (
                    not match_report.has_compat_modules()
                    and match_report.wiring_method_counts.get("auto_match", 0) == 0
                    and len(_filter_unaccounted(
                        report.get("open_outputs", []),
                        cfg.allowed_unconnected.open_outputs)) == 0
                    and len(_filter_unaccounted(
                        report.get("tied_to_zero", []),
                        cfg.allowed_unconnected.tied_inputs)) == 0
                ),
                "port_signature_hash": port_sig_hash if port_map_data else None,
            }
            maturity_output.write_text(_json.dumps(maturity, indent=2) + "\n")
            print(f"  ✓ Maturity report: {maturity_output}")

            # Generate SystemVerilog testbench if requested
            should_gen_tb = args.gen_testbench or (cfg.testbench and cfg.testbench.generate)
            
            if should_gen_tb:
                print(f"\n🧪 Generating SystemVerilog testbench...")
                
                # Determine stimulus configuration (CLI args override YAML config)
                xml_path = None
                event_id = args.event_id
                
                # CLI argument takes precedence
                if args.xml_stimulus:
                    xml_path = Path(args.xml_stimulus)
                # Otherwise use YAML config if available
                elif cfg.testbench and cfg.testbench.xml_stimulus_path:
                    # Resolve relative to design.yml location
                    xml_path = design_path.parent / cfg.testbench.xml_stimulus_path
                    event_id = cfg.testbench.event_id
                    print(f"   ℹ️  Using testbench config from design.yml")
                
                # Validate XML file exists
                if xml_path:
                    if not xml_path.exists():
                        print(f"⚠️  Warning: XML stimulus file not found: {xml_path}")
                        print(f"   📁 Searched: {xml_path.absolute()}")
                        print(f"   Will generate testbench with placeholder stimulus")
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
            
            # Optional: Run Verilog linting if requested
            if args.lint:
                try:
                    from arc.core.utils.verilog_linter import lint_verilog_file
                    print(f"\n🔍 Running Verilog linter (Verilator)...")
                    lint_verilog_file(output, top_module=args.top_name)
                except ImportError:
                    print("⚠️  Verilog linter requires Verilator. Please install it:")
                    print("   - Ubuntu/Debian: sudo apt-get install verilator")
                    print("   - Fedora/RHEL: sudo dnf install verilator")
                    print("   - macOS: brew install verilator")
            
        elif args.mode == "bd":
            output = _resolve_path(args.output, consumer_root, Path("block_design.tcl"))
            print(f"🔨 Generating Block Design TCL: {output}")
            
            write_bd_tcl(
                cfg=cfg,
                ip_info=ip_info,
                conn_map=conn_map,
                global_nets=global_nets,
                out_path=output,
                bd_name=args.bd_name,
                src_root=design_path.parent
            )
            
            print(f"✓ Block Design TCL generated: {output}")
            
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


# ── fw_verify delegation ─────────────────────────────────────────────────────

def _dispatch_fw_verify(remaining: list) -> None:
    """Delegate ``arc verify <subcommand> [args]`` to fw_verify.__main__.main().

    This keeps fw_verify as the single source of truth for verification logic
    while exposing its commands under the unified ``arc verify`` group.
    """
    from arc.verify.__main__ import main as _fw_verify_main  # type: ignore[import]
    old_argv = sys.argv[:]
    sys.argv = ["arc verify"] + list(remaining)
    try:
        _fw_verify_main()
    except SystemExit:
        raise
    finally:
        sys.argv = old_argv


def cmd_verify_dispatch(args):
    _dispatch_fw_verify(args.verify_args or [])


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="arc",
        description="ARC framework CLI — topology generation, HLS build, and verification orchestration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Groups:
  core      Shared schema, CLI, and plugin utilities
  topgen    Generate hardware topology structure
  hls       Build HLS modules and IPs
  verify    Run simulations and check correctness

Examples:
  arc topgen gen-top design.yml --mode verilog --output algo_top.v
  arc topgen validate design.yml
  arc hls gen-tcl --hls-config catalog.yml
  arc hls run --registry catalog.yml --stages csim,synth --jobs 4
  arc verify generate plugins/my_plugin/verify/design.verification.yml
  arc verify run plugins/my_plugin/verify/hit_decoder_xsim/verify.flow.yml --plugin my_plugin
  arc verify doctor plugins/my_plugin/verify/design.verification.yml
  arc core resources
  arc core verify-contract --ip-info ip_info.yaml --contract ip_interface.yaml
        """,
    )

    parser.add_argument("--version", action="version", version="arc 1.1.0")
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Print Python tracebacks for unexpected failures.",
    )

    sub = parser.add_subparsers(
        dest="group", required=True, metavar="GROUP",
        help="Command group (core | topgen | hls | verify)",
    )

    # ═══════════════════════════════════════════════════════════════════════════
    # arc core — shared schema, CLI, and plugin utilities
    # ═══════════════════════════════════════════════════════════════════════════
    p_core = sub.add_parser("core", help="Shared schema, CLI, and plugin utilities")
    core_sub = p_core.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # arc core resources
    p_resources = core_sub.add_parser(
        "resources", help="Print installed framework resource paths",
    )
    p_resources.add_argument("--key", help="Print only the value for a specific resource key")
    p_resources.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="Output format (default: text)",
    )
    p_resources.set_defaults(func=cmd_resources)

    # arc core verify-contract
    p_vc = core_sub.add_parser(
        "verify-contract",
        help="Verify ip_interface.yaml contract(s) against ip_info.yaml",
    )
    p_vc.add_argument(
        "--ip-info", required=True, type=Path, dest="ip_info",
        help="Path to ip_info.yaml (raw port metadata)",
    )
    p_vc.add_argument(
        "--contract", type=Path, default=None,
        help="Single ip_interface.yaml file to verify",
    )
    p_vc.add_argument(
        "--all-contracts", nargs="+", metavar="DIR", default=None,
        help="Directories to search recursively for ip_interface*.yaml files",
    )
    p_vc.add_argument(
        "--verbose", "-v", action="store_true", default=False,
        help="Print details even for passing contracts",
    )
    p_vc.set_defaults(func=cmd_verify_contract)

    # ═══════════════════════════════════════════════════════════════════════════
    # arc topgen — generate hardware topology structure
    # ═══════════════════════════════════════════════════════════════════════════
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
        help="Output mode: vhdl (structural VHDL), verilog (structural Verilog), or bd (Block Design TCL)",
    )
    p_gen.add_argument("--ip-root", type=Path, help="IP directory (default: ips/)")
    p_gen.add_argument("--ip-info", type=Path, help="IP info file (auto-generated if missing)")
    p_gen.add_argument("--build-dir", type=Path, help="Build directory (default: build/)")
    p_gen.add_argument(
        "--hls-build-root", type=Path,
        help="HLS build directory used for manifest/source resolution (default: build_hls/)",
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
    p_gen.add_argument(
        "--xml-stimulus", type=Path,
        help="Path to XML file for stimulus generation",
    )
    p_gen.add_argument(
        "--xml-stimulus-tool", type=Path,
        help="Path to the xml_to_sv_stimulus helper binary",
    )
    p_gen.add_argument("--event-id", type=int, default=1, help="Event ID to extract from XML (default: 1)")
    p_gen.add_argument("--hls-metrics", type=Path, help="HLS metrics JSON file")
    p_gen.add_argument(
        "--contracts-from", type=Path, metavar="MODULES_YML",
        help="modules.yml registry with interface_contract paths; enables contract-driven wiring",
    )
    p_gen.add_argument(
        "--strict", action="store_true",
        help="Fail if any module lacks a contract, any connection uses auto-match or "
             "port_map_ranges, or topology groups have verification errors",
    )
    p_gen.add_argument("--lint", action="store_true", help="Run VHDL linter after generation")
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
        help=(
            "Also verify that generated artifacts are not older than "
            "design.yml / modules.yml / ip_info.yaml."
        ),
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
    p_summary = tg_sub.add_parser("ip-summary", help="Generate IP metadata summary (ip_info.yaml)")
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
    p_summary.add_argument("--output", "-o", type=Path, help="Output file (default: ip_info.yaml)")
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
    p_lint_verilog = tg_sub.add_parser("lint-verilog", help="Lint Verilog file(s) with Verilator")
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

    # ═══════════════════════════════════════════════════════════════════════════
    # arc hls — build HLS modules and IPs
    # ═══════════════════════════════════════════════════════════════════════════
    p_hls = sub.add_parser("hls", help="Build HLS modules and IPs")
    hls_sub = p_hls.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # arc hls gen-tcl
    p_hls_gen = hls_sub.add_parser(
        "gen-tcl", help="Generate Vitis HLS TCL scripts from a plugin catalog",
    )
    p_hls_gen.add_argument(
        "module", nargs="?", default="all",
        help="Module name or 'all' (default: all)",
    )
    p_hls_gen.add_argument(
        "--hls-config", required=True, dest="hls_config", type=Path,
        help="Plugin HLS catalog YAML file",
    )
    p_hls_gen.add_argument(
        "--output-dir", dest="output_dir", type=Path, default=Path("build_hls"),
        help="Output directory for generated TCL (default: build_hls/)",
    )
    p_hls_gen.add_argument(
        "--ip-packages-dir", dest="ip_packages_dir", type=Path,
        help="Directory for exported IP archives",
    )
    p_hls_gen.add_argument(
        "--template-dir", dest="template_dir", type=Path,
        help="Override .tcl.j2 template directory",
    )
    p_hls_gen.add_argument(
        "--flow", choices=["export", "syn", "impl"], default="impl",
        help="IP export flow: export (no synth), syn (RTL only), impl (synth+P&R) (default: impl)",
    )
    p_hls_gen.set_defaults(func=cmd_hls_gen_tcl)

    # arc hls run
    p_hls_run = hls_sub.add_parser(
        "run", help="Run parallel Vitis HLS jobs (pure Python, no shell script)",
    )
    p_hls_run.add_argument(
        "--registry", dest="registry", type=Path,
        help="Plugin HLS catalog YAML file (modules registry)",
    )
    p_hls_run.add_argument(
        "--stages", dest="stages",
        help="Comma-separated HLS stages to run: csim,synth,cosim,export (default: all four)",
    )
    p_hls_run.add_argument(
        "--stage", "-c", dest="stage", choices=["csim", "synth", "cosim", "export"],
        help="Single HLS stage (legacy; use --stages for multiple)",
    )
    p_hls_run.add_argument(
        "--modules", "-m", dest="modules",
        help="Comma- or space-separated module names, or 'all' (default: all)",
    )
    p_hls_run.add_argument(
        "--jobs", "-j", type=int, default=1, dest="jobs",
        help="Maximum parallel jobs per stage (default: 1)",
    )
    p_hls_run.add_argument(
        "--hls-build-root", "-b", dest="hls_build_root", type=Path,
        help="HLS build root directory (default: build_hls/)",
    )
    p_hls_run.add_argument(
        "--ip-packages-dir", "-i", dest="ip_packages_dir", type=Path,
        help="IP packages output directory (default: <hls-build-root>/ip_packages/)",
    )
    p_hls_run.add_argument(
        "--vitis-hls", "-v", dest="vitis_hls", type=Path,
        help="Path to vitis_hls executable (default: vitis_hls on PATH)",
    )
    p_hls_run.set_defaults(func=cmd_hls_run)

    # ═══════════════════════════════════════════════════════════════════════════
    # arc verify — run simulations and check correctness
    # (delegates to fw_verify engine; pass any sub-command and its args through)
    # ═══════════════════════════════════════════════════════════════════════════
    p_verify = sub.add_parser(
        "verify",
        help="Run simulations and check correctness",
        description=(
            "Simulation and verification commands.\n\n"
            "Available sub-commands:\n"
            "  generate      Generate verify.flow.yml, tb_*.sv, wave.tcl\n"
            "  run           Execute the full verification lifecycle\n"
            "  preflight     Run DUT artifact preflight checks\n"
            "  prepare       Generate + validate + check layout in one step\n"
            "  doctor        Comprehensive read-only health check\n"
            "  release-check Release-readiness gate (RC-01 … RC-10)\n"
            "  init-plugin   Scaffold a new plugin skeleton\n\n"
            "Pass --help after any sub-command to see its options:\n"
            "  arc verify run --help"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  arc verify generate plugins/my_plugin/verify/design.verification.yml\n"
            "  arc verify run plugins/my_plugin/verify/hit_decoder_xsim/verify.flow.yml --plugin my_plugin\n"
            "  arc verify doctor plugins/my_plugin/verify/design.verification.yml\n"
            "  arc verify release-check plugins/my_plugin/verify/design.verification.yml\n"
            "  arc verify init-plugin my_trigger\n"
        ),
    )
    p_verify.add_argument("verify_args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    p_verify.set_defaults(func=cmd_verify_dispatch)

    args = parser.parse_args()

    global _TOPGEN_DEBUG
    _TOPGEN_DEBUG = bool(getattr(args, "debug", False))

    try:
        args.func(args)
    except SystemExit:
        raise
    except Exception as exc:
        _print_cli_error(
            "Unexpected error",
            exc,
            hint="Check the command arguments and input files, or re-run with --debug to capture traceback details.",
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
