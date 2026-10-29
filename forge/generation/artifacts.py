"""The artifacts a generated top level is delivered with.

Next to the HDL (or block-design Tcl) the structural generator writes,
generation emits: the build manifest every simulation compiles from, the
port map and its signature, the probe map, the testbench bindings, the
maturity summary, and the cross-checks between them. Each reads the
generator's own resolved port list or the canonical IR, never the emitted
file, so the artifacts cannot disagree with the RTL they describe.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Optional

from forge.generation.support_rtl import SUPPORT_RTL_BY_TRANSFORMATION, resolve_support_rtl
from forge.ir.serialize import content_hash as ir_content_hash_of


def match_patterns(name: str, patterns: list[str]) -> bool:
    import re as _re
    return any(_re.search(pattern, name) for pattern in patterns or [])


def numeric_suffix_key(name: str) -> tuple[int, str]:
    import re as _re
    match = _re.search(r'_(\d+)(?:_|$)', name)
    return (int(match.group(1)), name) if match else (0, name)


def sort_entries(entries: list[dict], sort_mode: str | None) -> list[dict]:
    if sort_mode == "numeric_suffix":
        return sorted(entries, key=lambda e: numeric_suffix_key(e["name"]))
    return sorted(entries, key=lambda e: e["name"])


def resolve_interface_metadata(interface_metadata: dict | None) -> dict:
    return copy.deepcopy(interface_metadata or {})


def filter_unaccounted(port_list, patterns):
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

def compute_maturity_summary(
    cfg, match_report, report: Optional[dict] = None, *, ir_content_hash: Optional[str] = None,
) -> dict:
    """Module/connection/port maturity summary — factored out of
    ``cmd_gen_top``'s inline ``maturity`` dict
    so ``forge inspect`` can share it without running the generator.

    ``report`` is the generator's own open/tied-port accounting
    (``cmd_gen_top`` always has it). ``forge inspect`` never runs the
    generator, so it always passes ``report=None`` — the port-accounting
    fields and ``strict_pass`` then stay honestly absent (``None``) rather
    than fabricated, since they cannot be known without the generator's
    own port report. Module/connection/wiring-method counts are knowable
    from *cfg*/*match_report* alone and always populate.

    *ir_content_hash* is the canonical IR this summary describes — every
    generated report records which resolved design it was built from
    (Phase G4), so a report found on disk months later can be tied back to
    an exact design rather than to a filename and a timestamp. Optional
    because a caller without a ``ResolvedProject`` in hand must be able to
    leave it honestly absent rather than invent one.
    """
    summary: dict = {
        "modules": {
            "total": len(cfg.modules),
            "contract_driven": len(match_report.contract_driven_modules),
            "compat_mode": len(match_report.compat_mode_modules),
            # The per-module name lists, not just their counts, so `forge
            # report`'s Markdown section and the design explorer's
            # per-module maturity overlay both source from the same lists
            # instead of recomputing them differently.
            "contract_driven_names": sorted(match_report.contract_driven_modules),
            "compat_mode_names": sorted(match_report.compat_mode_modules),
        },
        "connections": dict(match_report.wiring_method_counts),
        "ir_content_hash": ir_content_hash,
    }

    if report is None:
        summary["ports"] = None
        summary["strict_pass"] = None
        return summary

    open_outputs = report.get("open_outputs", [])
    tied_inputs = report.get("tied_to_zero", [])
    unaccounted_open = filter_unaccounted(open_outputs, cfg.allowed_unconnected.open_outputs)
    unaccounted_tied = filter_unaccounted(tied_inputs, cfg.allowed_unconnected.tied_inputs)

    summary["ports"] = {
        "open_outputs": len(open_outputs),
        "tied_inputs": len(tied_inputs),
        "open_accounted": len(open_outputs) - len(unaccounted_open),
        "tied_accounted": len(tied_inputs) - len(unaccounted_tied),
    }
    summary["strict_pass"] = (
        not match_report.has_compat_modules()
        and match_report.wiring_method_counts.get("auto_match", 0) == 0
        and len(unaccounted_open) == 0
        and len(unaccounted_tied) == 0
    )
    return summary

def render_maturity_markdown(maturity: dict) -> str:
    """Render a `compute_maturity_summary` dict as Markdown — pure
    presentation over an already-computed summary, no new maturity logic
    (`forge report`'s maturity/compatibility
    report section, shared with `forge inspect` rather than duplicated)."""
    modules = maturity["modules"]
    lines = [
        "# Contract maturity",
        "",
        f"- **modules**: {modules['total']} total, "
        f"{modules['contract_driven']} contract-driven, "
        f"{modules['compat_mode']} compat-mode",
    ]
    connections = maturity.get("connections") or {}
    if connections:
        conn_str = ", ".join(f"{k}={v}" for k, v in connections.items())
        lines.append(f"- **connections**: {conn_str}")

    ir_hash = maturity.get("ir_content_hash")
    if ir_hash:
        lines.append(f"- **resolved design (IR content hash)**: `{ir_hash}`")

    ports = maturity.get("ports")
    if ports is None:
        lines.append(
            "- **ports**: not available (no generator run — see `forge build`"
            " or `topgen gen-top`)"
        )
    else:
        lines.append(
            f"- **ports**: {ports['open_outputs']} open output(s) "
            f"({ports['open_accounted']} accounted), "
            f"{ports['tied_inputs']} tied input(s) ({ports['tied_accounted']} accounted)"
        )

    strict_pass = maturity.get("strict_pass")
    if strict_pass is None:
        lines.append("- **strict mode**: unknown (no generator run)")
    else:
        lines.append(f"- **strict mode**: {'PASS' if strict_pass else 'FAIL'}")

    return "\n".join(lines) + "\n"

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

def support_rtl_needed(project) -> "list[str]":
    """The framework support-RTL filenames this design's *resolved*
    transformations require, in ``SUPPORT_RTL_BY_TRANSFORMATION``'s
    declaration order (stable regardless of connection ordering).

    Read from the canonical IR rather than re-scanned out of
    ``cfg.connections``/``cfg.reset_domains``, which is what this function
    replaced: the generator emits a RegisterStage/signal_delay/cdc_*
    instance per *resolved* connection, so a declared ``register_stages:``
    on a module pair whose ports never matched produces no instance — and
    used to produce a manifest entry anyway. The IR states the
    transformations that were actually generated, so the compile list now
    matches the RTL.

    Reset synchronizers are domain-keyed, not connection-keyed
    (``ResolvedResetDomain.transformations``) — a reset crossing has no
    connection to attach to.
    """
    kinds = {
        xform.kind
        for conn in project.design.connections
        for xform in conn.transformations
    }
    kinds.update(
        xform.kind
        for domain in project.design.reset_domains
        for xform in domain.transformations
    )
    return [
        filename
        for kind, filename in SUPPORT_RTL_BY_TRANSFORMATION.items()
        if kind in kinds
    ]

def generate_build_manifest(
    project,
    ip_info,
    ip_root,
    algo_top,
    design_file,
    manifest_output,
    *,
    project_root: Path | None = None,
    hls_build_root: Path | None = None,
    auto_delays=None,
):
    """Generate the simulation build manifest with all Verilog source paths.

    *project* is the canonical IR (``forge.ir.model.ResolvedProject``) this
    build was generated from — the manifest's whole design-side content
    (which modules exist, their compile files, which framework support RTL
    the generated top level instantiates) is read from it, so the manifest
    can never disagree with the RTL that was emitted from the same IR
    object. Nothing here re-runs matching or re-resolves a declared path.

    Everything else the manifest carries is *build context* the IR
    deliberately does not model: where an HLS module's generated Verilog
    landed (*ip_info*/*ip_root*/*hls_build_root* — an artifact of running
    the tool, not a fact about the design). The framework support RTL is
    the copy shipped with forge (``forge.generation.support_rtl``);
    *project_root* is only recorded in the manifest.

    The IR's schema version and content hash are recorded in the manifest
    so a consumer can tell which resolved design a compile list belongs to
    — the same identity ``design.ir.json`` and ``provenance.json`` carry.
    *auto_delays* (an ``AutoDelayResult``) records the depth each
    ``delay_cycles: auto`` was resolved to, which the design file does not.
    """
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
        "ir_schema_version": project.schema_version,
        "ir_content_hash": ir_content_hash_of(project),
        "verilog_files": [],
        "include_dirs": [],
        "modules": {},
    }

    manifest["verilog_files"].append(str(algo_top.resolve()))
    manifest["include_dirs"].append(str(algo_top.parent.resolve()))

    if hls_build_root is not None:
        manifest["hls_build_root"] = str(hls_build_root.resolve())

    if auto_delays is not None:
        manifest["resolved_auto_delays"] = [
            {"kind": d.kind, "index": d.index, "src": d.src, "dst": d.dst, "cycles": d.cycles}
            for d in auto_delays.delays
        ]

    # Declaration order, not the IR's own name-sorted storage order: the
    # manifest is a compile list, and its file order is the order the
    # design file lists its modules in — see
    # ResolvedModuleDefinition.declaration_order.
    for module in sorted(project.design.modules, key=lambda m: m.declaration_order):
        module_name = module.name
        module_info = {
            "name": module_name,
            "top": module.top,
            "kind": module.kind,
            "ip_info_key": module.ip_info_key,
            "verilog_files": [],
            "include_dirs": [],
        }

        verilog_dir = None

        # RTL modules — `rtl_sources` is the IR's already-resolved compile
        # set (declared `src` then `rtl_packages`, absolute), so no
        # declared path is resolved a second time here.
        if module.rtl_sources:
            rtl_files = []
            for src_path in (Path(p) for p in module.rtl_sources):
                if src_path.exists():
                    rtl_files.append(str(src_path.resolve()))
                    if src_path.parent not in [Path(d) for d in module_info["include_dirs"]]:
                        module_info["include_dirs"].append(str(src_path.parent.resolve()))

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
                # The on-disk HLS solution directory is named after the
                # module's *ip_info_key* (the shared IP/registry name an
                # HLS run is keyed on), not its design.yml instance name —
                # e.g. instance `dt` synthesizes under `build_hls/dt_interface/`,
                # not `build_hls/dt/`. Try ip_info_key first; module_name
                # stays as a fallback for the (common) case where the two
                # already coincide, and for older build trees keyed by
                # instance name directly.
                build_dir_names = list(dict.fromkeys(
                    name for name in (module.ip_info_key, module_name) if name
                ))
                build_candidates = [
                    hls_build_root / "build_hls" / name / "solution1" / stage / "verilog"
                    for name in build_dir_names
                    for stage in ("syn", "sim")
                ] + [
                    hls_build_root / name / "solution1" / stage / "verilog"
                    for name in build_dir_names
                    for stage in ("syn", "sim")
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

    # ── Framework support RTL ──
    # RegisterStage, signal_delay, slr_crossing_delay and the CDC primitive
    # family are instantiated in the generated top level, so they have to be
    # in the compile list for simulation and synthesis. Which of them the
    # design needs is the IR's answer (support_rtl_needed); the files are
    # the ones shipped with forge (forge/rtl/support/), unless the design
    # already compiles its own file of the same name — see
    # forge.generation.support_rtl.
    for found in resolve_support_rtl(support_rtl_needed(project), manifest["verilog_files"]):
        manifest["verilog_files"].append(str(found))
        if str(found.parent) not in manifest["include_dirs"]:
            manifest["include_dirs"].append(str(found.parent))

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
    *,
    ports: dict[str, tuple[str, int]] | None = None,
):
    """Write port_map.yaml from the top-level port list.

    When *ports*
    is given (``{name: (direction, width)}``, the same shape
    ``forge.core.utils.hdl_parser._scan_verilog_ports`` returns), it is
    used directly — this is ``write_structural_verilog``'s own
    ``report["top_ports"]``, so the resolved top-level port list no longer
    needs to be independently re-derived by re-parsing the file it just
    wrote. When *ports* is omitted (the default), behavior is unchanged
    from before: ``verilog_path`` is scanned via ``_scan_verilog_ports``.
    """
    import yaml as _yaml
    from forge.core.utils.hdl_parser import _scan_verilog_ports
    from forge.core.utils import port_signature as _sig

    if ports is None:
        ports = _scan_verilog_ports(verilog_path)
    if not ports:
        print(f"  ⚠  Could not parse ports from {verilog_path} — port_map.yaml not written")
        return None, None

    metadata = resolve_interface_metadata(interface_metadata)
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
        entry: dict[str, Any] = {
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
                if match_patterns(name, group_def.get("match", [])):
                    grouped_input["ports"].append(entry)
                    assigned = True
                    break
            if assigned:
                continue
            for group_name, group_cfg in input_groups.items():
                if group_cfg.get("groups"):
                    continue
                if match_patterns(name, group_cfg.get("match", [])):
                    grouped_inputs[group_name].append(entry)
                    assigned = True
                    break
            if not assigned:
                unclassified.append(entry)

    for group_name, group_entries in grouped_inputs.items():
        grouped_inputs[group_name] = sort_entries(
            group_entries, input_groups[group_name].get("sort")
        )

    for grouped_input, group_def in zip(grouped_style_list, grouped_input_defs):
        grouped_input["ports"] = sort_entries(grouped_input["ports"], group_def.get("sort"))
        grouped_input["count"] = len(grouped_input["ports"])
        grouped_input["width"] = grouped_input["ports"][0]["width"] if grouped_input["ports"] else 0

    for e in outputs:
        e["group"] = "other"
        for group_name, group_cfg in metadata.get("output_groups", {}).items():
            if match_patterns(e["name"], group_cfg.get("match", [])):
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
        "generated_by": "forge gen-top --mode verilog",
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
        "generated_by": port_map.get("generated_by", "forge gen-top --mode verilog"),
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
    lines.append("// tb_bindings.svh — Generated by forge gen-top")
    lines.append(f"// Top module   : {top_module}")
    lines.append(f"// Port sig hash: {sig_hash}")
    lines.append("// DO NOT EDIT — regenerate: forge gen-top ...")
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
