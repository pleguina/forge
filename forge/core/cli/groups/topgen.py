"""forge topgen — hardware topology generation commands."""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from forge.contracts.config import DesignConfig
from forge.contracts.unpacker import unpack_ip_archives
from forge.contracts.parser import collect_all, write_summary
from forge.contracts.matcher import load_ip_info, auto_match_ports
from forge.contracts.contract_loader import (
    drain_contract_conflicts,
    load_contracts_for_design,
    synthesize_ip_info,
)
from forge.generation.generators.structural_vhdl import write_structural_vhdl
from forge.generation.generators.structural_verilog import write_structural_verilog
from forge.generation.generators.block_design import write_bd_tcl
from forge.generation.generators.sv_testbench_generator import generate_sv_testbench
from forge.generation.generators.design_parameters import write_design_parameters
from forge.generation.validation import validate_design, validate_registry
from forge.generation.support_rtl import SUPPORT_RTL_BY_TRANSFORMATION, resolve_support_rtl
from forge.ir.build import assemble_project_ir, build_tie_off_connections
from forge.ir.model import ResolvedTopLevelPort
from forge.ir.project import project_to_conn_map
from forge.ir.serialize import content_hash as ir_content_hash_of, to_json_dict
from forge.core.diagnostics import ATGDiagnosticReport
from forge.core.stale_detection import (
    check_top_gen_staleness,
    check_ip_info_staleness,
    format_stale_report,
)
from forge.core.cli._shared import (
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

def _validator_payload(validator) -> dict:
    """Serialize a DesignValidator/RegistryValidator (topgen/validation.py)
    into a JSON-ready dict. Their errors/warnings/infos are plain
    ValidationError dataclasses, so this is a direct asdict() mapping."""
    return {
        "passed": not validator.has_errors(),
        "errors": [dataclasses.asdict(e) for e in validator.errors],
        "warnings": [dataclasses.asdict(e) for e in validator.warnings],
        "infos": [dataclasses.asdict(e) for e in validator.infos],
    }


def _validation_error_to_diagnostic(err, *, category_prefix: str) -> dict:
    """Reshape one `ValidationError` (topgen/validation.py) into the
    shared envelope's diagnostic-dict shape
    — `code=None` (ValidationError has no stable code table, honest
    absence), `action` from `suggestion`, `object_id` from `location`."""
    d: dict = {
        "severity": err.severity, "message": err.message, "code": None,
        "category": f"{category_prefix}:{err.category}",
    }
    if err.location:
        d["object_id"] = err.location
    if err.suggestion:
        d["action"] = err.suggestion
    return d


def _validators_to_envelope(*validators_with_prefix, metrics: Optional[dict] = None):
    """Build a `CommandEnvelope` from one or more (validator, category_prefix)
    pairs — shared by `cmd_validate` (design + optional registry) and
    `cmd_validate_registry` (registry alone). `status` is the honest 3-way
    pass/warn/fail derived from real severities; `--strict`'s warn->fail
    exit-code promotion is applied by the caller via
    `CommandEnvelope.exit_code(strict=...)`/`emit(..., strict=...)` — same
    "status string never changes, only the exit code does" contract every
    other envelope-adopting command follows."""
    from forge.core.cli.envelope import CommandEnvelope

    diagnostics: List[dict] = []
    for validator, prefix in validators_with_prefix:
        for err in validator.errors:
            diagnostics.append(_validation_error_to_diagnostic(err, category_prefix=prefix))
        for err in validator.warnings:
            diagnostics.append(_validation_error_to_diagnostic(err, category_prefix=prefix))
        for err in validator.infos:
            diagnostics.append(_validation_error_to_diagnostic(err, category_prefix=prefix))

    has_error = any(d["severity"] == "error" for d in diagnostics)
    has_warning = any(d["severity"] == "warning" for d in diagnostics)
    status = "fail" if has_error else ("warn" if has_warning else "pass")

    next_actions: List[str] = []
    seen = set()
    for d in diagnostics:
        if d["severity"] not in ("warning", "error") or "action" not in d:
            continue
        if d["action"] not in seen:
            seen.add(d["action"])
            next_actions.append(d["action"])

    return CommandEnvelope(
        status=status, diagnostics=diagnostics, metrics=dict(metrics or {}),
        next_actions=next_actions,
    )


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


def _compute_maturity_summary(
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
            # The real per-module name
            # lists were already sitting on match_report, previously
            # discarded down to a bare len() here — additive so `forge
            # report`'s existing Markdown section and the visual design
            # explorer's per-module maturity overlay both source from the
            # same real lists instead of recomputing them differently.
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
    unaccounted_open = _filter_unaccounted(open_outputs, cfg.allowed_unconnected.open_outputs)
    unaccounted_tied = _filter_unaccounted(tied_inputs, cfg.allowed_unconnected.tied_inputs)

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
    """Render a `_compute_maturity_summary` dict as Markdown — pure
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
            "- **ports**: not available (no generator run — see `forge build --apply`"
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


def _support_rtl_needed(project) -> "list[str]":
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


def _discover_verify_design(design_path: Path, args, consumer_root: Path | None) -> Path | None:
    """The verification contract to resolve the IR's plan against.

    ``--verify-design`` wins; otherwise the conventional sibling location
    (``<plugin>/forge/verify/design.verification.yml``) is used when it
    exists — the same discovery ``forge topgen clean`` already performs, so
    a project doesn't have to name the file twice. ``--no-verify`` skips it.
    """
    if getattr(args, "no_verify", False):
        return None
    declared = getattr(args, "verify_design", None)
    if declared:
        return _resolve_path(declared, consumer_root)
    candidate = design_path.parent.parent / "verify" / "design.verification.yml"
    return candidate.resolve() if candidate.exists() else None


def _attach_verification_plan(
    project, design_path: Path, args, consumer_root: Path | None, dut_dir: Path,
) -> None:
    """Resolve this design's verification plan onto the IR it just
    generated from — Phase G3.

    Attached here, after generation, for the same reason ``top_ports`` and
    the tie-off connections are: the plan's stimulus and observation points
    *are* the generated top level's ports, which only exist once the
    generator has run. ``forge inspect``'s pre-generation IR therefore
    carries no plan, exactly as it carries no top-level ports.

    A contract that fails to load is reported and skipped rather than
    failing the build: verification planning is a description of what was
    generated, and a broken verification contract must not stop the RTL
    from being written. Whoever runs `forge verify` gets the same load
    error from the loader that owns it.
    """
    from forge.ir.verification_plan import build_verification_plan

    verify_design = _discover_verify_design(design_path, args, consumer_root)
    contract = None
    if verify_design and verify_design.exists():
        try:
            from forge.verification.design_contract import load_verify_design

            contract = load_verify_design(verify_design)
        except Exception as e:  # noqa: BLE001 - never fail generation for this
            print(f"  ⚠️  verification plan not resolved: {e}")

    plan = build_verification_plan(
        project, contract, dut_dir=dut_dir, consumer_root=consumer_root,
    )
    project.design.verification_plan = plan
    if plan.populated:
        mine = [fl for fl in plan.flows if fl.targets_this_design]
        print(
            f"  ✓ Verification plan: {len(mine)} of {len(plan.flows)} declared "
            f"flow(s) target this design, {len(plan.stimulus)} stimulus / "
            f"{len(plan.observation)} observation port(s)"
        )
        for fl in plan.flows:
            if fl.unresolved_reason:
                print(f"    ⚠️  flow '{fl.name}': {fl.unresolved_reason}")


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
    # design needs is the IR's answer (_support_rtl_needed); the files are
    # the ones shipped with forge (forge/rtl/support/), unless the design
    # already compiles its own file of the same name — see
    # forge.generation.support_rtl.
    for found in resolve_support_rtl(_support_rtl_needed(project), manifest["verilog_files"]):
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


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

def cmd_clean(args):
    """Remove generated artifacts for one design without touching IP/HLS trees."""
    try:
        design_path = Path(args.design).expanduser().resolve()
        if not design_path.exists():
            print(f"❌ Design file not found: {design_path}", file=sys.stderr)
            print("   → Check the design.yml path and re-run forge topgen clean.", file=sys.stderr)
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
            hint="Check the design path, output path, and verify-design path, then retry forge clean.",
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
            print("   Run 'forge ip-summary' first")
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
            for net, binds in global_nets.items():
                fanout = ", ".join(f"{inst}.{port}" for inst, port in binds)
                print(f"  {net}: {fanout}")

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


def cmd_lint(args):
    """Lint VHDL file(s)."""
    try:
        from forge.core.utils.vhdl_linter import lint_vhdl_files

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
        from forge.core.utils.verilog_linter import lint_verilog_files

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
    from forge.core.cli.envelope import CommandEnvelope, emit, status_for_exception

    json_mode = getattr(args, "json", False)
    strict = getattr(args, "strict", False)

    def _fail_early(msg: str) -> None:
        envelope = CommandEnvelope(status="fail", diagnostics=[{"severity": "error", "message": msg}])
        if not json_mode:
            print(f"❌ {msg}")
            sys.exit(envelope.exit_code())
        sys.exit(emit(envelope, json_mode=True))

    try:
        design_path = Path(args.design).expanduser().resolve()

        if not design_path.exists():
            _fail_early(f"Design file not found: {design_path}")

        import yaml as _yaml

        _raw_design = _yaml.safe_load(design_path.read_text()) or {}
        _registry_ref = _raw_design.get("registry")
        validators: List[Any] = []

        if _registry_ref:
            registry_path = (design_path.parent / _registry_ref).resolve()
            if not registry_path.exists():
                _fail_early(f"Registry file not found: {registry_path}")

            if not json_mode:
                print(f"📖 Loading registry: {registry_path}")
                print("🔍 Validating registry...\n")
            reg_validator = validate_registry(registry_path)
            if not json_mode:
                reg_validator.print_report()
            validators.append((reg_validator, "registry"))

            if reg_validator.has_errors() or (strict and reg_validator.warnings):
                if not json_mode:
                    if reg_validator.has_errors():
                        print(
                            f"\n⛔ Registry has {len(reg_validator.errors)} error(s). "
                            "Fix them before validating design."
                        )
                    else:
                        print(
                            f"\n⛔ Strict mode: treating {len(reg_validator.warnings)} "
                            "registry warning(s) as errors"
                        )
                envelope = _validators_to_envelope(*validators)
                sys.exit(emit(envelope, json_mode=json_mode, strict=strict))
            if not json_mode:
                print()

        if not json_mode:
            print(f"📖 Loading design: {design_path}")
        cfg = DesignConfig.load_relaxed(design_path)

        if not json_mode:
            print("🔍 Validating design configuration...\n")
        validator = validate_design(cfg, design_path)
        if not json_mode:
            validator.print_report()
        validators.append((validator, "design"))

        if validator.has_errors() or (strict and validator.warnings):
            if not json_mode and not validator.has_errors():
                print(f"\n⛔ Strict mode: Treating {len(validator.warnings)} warnings as errors")
            envelope = _validators_to_envelope(*validators)
            sys.exit(emit(envelope, json_mode=json_mode, strict=strict))

        metrics: Dict[str, Any] = {}

        # ── CDC crossing check ────────────────────────────────────────
        # verify_cdc used to be gen-top --strict-only (see cmd_gen_top);
        # authors got no CDC feedback until the final generation step.
        # Run it here too, as a best-effort, whenever contracts can be
        # loaded without needing already-built HDL (the same
        # --contracts-from-style projection cmd_gen_top itself supports) —
        # skip gracefully (no diagnostic at all) if the registry/contracts
        # aren't available yet, matching every other optional check in
        # this command.
        cdc_diagnostics: List[dict] = []
        if _registry_ref:
            try:
                _cdc_contracts = load_contracts_for_design(registry_path, design_path.parent)
                _cdc_mapped = {}
                for m in cfg.modules:
                    c = _cdc_contracts.get(m.name) or (m.ip_info_key and _cdc_contracts.get(m.ip_info_key))
                    if c:
                        _cdc_mapped[m.name] = c
                if _cdc_mapped:
                    _cdc_ip_info = synthesize_ip_info(_cdc_mapped)
                    _conn_map, _global_nets, _match_report = auto_match_ports(
                        cfg, _cdc_ip_info, contracts=_cdc_contracts,
                    )
                    from forge.contracts.cdc import verify_cdc
                    _cdc_issues = verify_cdc(cfg, _cdc_contracts, _match_report, _conn_map, _global_nets)
                    cdc_diagnostics = [
                        {
                            "severity": "warning", "code": issue.code or "ATG023",
                            "category": "cdc", "message": f"[{issue.connection}] {issue.message}",
                            "action": "Add a matching 'cdc:' block, or run gen-top --strict for a hard failure.",
                        }
                        for issue in _cdc_issues
                    ]

                    # forge.cdc_verification_result.v1
                    # — written from the exact same crossing data the
                    # warnings above were derived from, no recomputation.
                    _cdc_result_json = getattr(args, "cdc_result_json", None)
                    if _cdc_result_json:
                        import json as _json

                        from forge.core.utils.content_hash import hash_file
                        from forge.contracts.cdc import report_all_crossings
                        from forge.verification.cdc_verification_result import build_cdc_verification_result

                        _crossings = report_all_crossings(
                            cfg, _cdc_contracts, _match_report, _conn_map, _global_nets,
                        )
                        _cdc_result = build_cdc_verification_result(_crossings, hash_file(design_path))
                        _cdc_result_path = Path(_cdc_result_json).expanduser().resolve()
                        _cdc_result_path.parent.mkdir(parents=True, exist_ok=True)
                        _cdc_result_path.write_text(_json.dumps(_cdc_result.to_dict(), indent=2) + "\n")
                        if not json_mode:
                            print(f"  wrote {_cdc_result_path}")
            except Exception:
                cdc_diagnostics = []

        if cdc_diagnostics:
            if not json_mode:
                print("\n🔌 CDC crossing check:")
                for d in cdc_diagnostics:
                    print(f"  ⚠️  [{d['code']}] {d['message']}")
            if strict:
                if not json_mode:
                    print(f"\n⛔ Strict mode: treating {len(cdc_diagnostics)} CDC warning(s) as errors")
                envelope = _validators_to_envelope(*validators)
                envelope.diagnostics.extend(cdc_diagnostics)
                sys.exit(emit(envelope, json_mode=json_mode, strict=True))

        stale_diagnostic: Optional[dict] = None
        if getattr(args, "check_stale", False):
            if not json_mode:
                print()
                print("🕒 Checking for stale generated artifacts…")
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
            metrics["stale"] = {"count": len(stale_report.stale), "artifacts": stale_lines}
            if stale_lines:
                if not json_mode:
                    for line in stale_lines:
                        print(f"  ⚠️  {line}")
                stale_diagnostic = {
                    "severity": "warning", "code": "ATG007", "category": "stale",
                    "message": f"{len(stale_report.stale)} stale artifact(s) detected",
                    "action": "Re-run forge gen-top to rebuild",
                    "path": str(output_dir),
                }
                if strict:
                    if not json_mode:
                        print("\n⛔ Strict mode: stale artifacts treated as errors")
                    envelope = _validators_to_envelope(*validators, metrics=metrics)
                    envelope.diagnostics.append(stale_diagnostic)
                    envelope.next_actions.append(stale_diagnostic["action"])
                    sys.exit(emit(envelope, json_mode=json_mode, strict=True))
            elif not json_mode:
                print("  ✅ All generated artifacts are up to date")

        if not json_mode:
            print("\n✅ Design is valid and ready for generation!")
        envelope = _validators_to_envelope(*validators, metrics=metrics)
        if stale_diagnostic is not None:
            envelope.diagnostics.append(stale_diagnostic)
            envelope.next_actions.append(stale_diagnostic["action"])
            if envelope.status == "pass":
                envelope.status = "warn"
        if cdc_diagnostics:
            envelope.diagnostics.extend(cdc_diagnostics)
            envelope.next_actions.append(cdc_diagnostics[0]["action"])
            if envelope.status == "pass":
                envelope.status = "warn"
        sys.exit(emit(envelope, json_mode=json_mode, strict=strict))

    except SystemExit:
        raise
    except Exception as e:
        # A malformed design.yml is a finding about the user's file (exit 1),
        # not a FORGE crash (exit 2) — see envelope.status_for_exception.
        envelope = CommandEnvelope(
            status=status_for_exception(e),
            diagnostics=[{"severity": "error", "message": str(e)}],
        )
        if json_mode:
            sys.exit(emit(envelope, json_mode=True))
        print_cli_error(
            "Validation failed",
            e,
            hint="Fix the reported design or contract issue, then re-run forge validate.",
        )
        sys.exit(envelope.exit_code())


def cmd_validate_registry(args):
    """Validate a modules.yml registry file independently."""
    from forge.core.cli.envelope import CommandEnvelope, emit, status_for_exception

    json_mode = getattr(args, "json", False)
    strict = getattr(args, "strict", False)
    try:
        registry_path = Path(args.registry).expanduser().resolve()
        if not registry_path.exists():
            envelope = CommandEnvelope(
                status="fail",
                diagnostics=[{
                    "severity": "error",
                    "message": f"Registry file not found: {registry_path}",
                }],
            )
            if not json_mode:
                print(f"❌ Registry file not found: {registry_path}")
                sys.exit(envelope.exit_code())
            sys.exit(emit(envelope, json_mode=True))

        if not json_mode:
            print(f"📖 Loading registry: {registry_path}")
            print("🔍 Validating registry...\n")
        validator = validate_registry(registry_path)
        if not json_mode:
            validator.print_report()

        envelope = _validators_to_envelope((validator, "registry"))

        if json_mode:
            sys.exit(emit(envelope, json_mode=True, strict=strict))
        if strict and envelope.status == "warn":
            print(
                f"\n⛔ Strict mode: treating {len(validator.warnings)} warning(s) as errors"
            )
        sys.exit(envelope.exit_code(strict=strict))

    except SystemExit:
        raise
    except Exception as e:
        envelope = CommandEnvelope(
            status=status_for_exception(e),
            diagnostics=[{"severity": "error", "message": str(e)}],
        )
        if json_mode:
            sys.exit(emit(envelope, json_mode=True))
        print_cli_error(
            "Registry validation failed",
            e,
            hint="Fix the modules.yml registry issue, then re-run forge validate-registry.",
        )
        sys.exit(envelope.exit_code())


@dataclass
class GenTopPlanContext:
    """Everything ``cmd_gen_top`` computes before its dry-run/real-write
    fork (design/path/contract/ip_info resolution, matching, the 3
    structured strict-check issue lists, and the pre-generation IR) —
    bundled so ``forge build`` can reuse the exact
    same computation without cmd_gen_top's sys.exit-based control flow.
    Adding a field here must never change any existing print/exit
    behavior in ``cmd_gen_top`` — this is pure extraction, not a behavior
    change (see ``compute_gen_top_plan``'s docstring)."""
    cfg: DesignConfig
    design_path: Path
    c_root: Path
    ip_root: Path
    build_root: Path
    hls_build_root: Path
    src_root: Path
    xml_stimulus_tool: Optional[Path]
    output_dir: Path
    ip_info_file: Path
    contracts_from: Optional[str]
    contracts_from_path: Optional[Path]
    contracts: Optional[Dict[str, Any]]
    ip_info: Dict[str, Any]
    ip_info_generated_now: bool
    conn_map: Any
    global_nets: Any
    match_report: Any
    project: Any  # forge.ir.model.ResolvedProject — pre-generation IR, no tie_off/top_ports yet
    topology_group_issues: List[Any]
    cardinality_issues: List[Any]
    cdc_issues: List[Any]


def compute_gen_top_plan(
    cfg: DesignConfig,
    design_path: Path,
    args,
    *,
    read_only: bool,
    emit_progress: bool = True,
) -> GenTopPlanContext:
    """Behavior-preserving extraction of ``cmd_gen_top``'s compute phase
    (path/contract/ip_info resolution, matching, and the 3 structured
    strict-check issue lists), used both by ``cmd_gen_top`` itself and by
    ``forge build``.

    Callers must have already loaded and validated *cfg* — the two
    "can't even start" failure modes (missing design file, validation
    errors) keep their existing custom print+exit messages in
    ``cmd_gen_top`` rather than being folded into this function, which
    raises ordinary exceptions (same as every call in this range already
    did before extraction — they were always inside ``cmd_gen_top``'s own
    ``try/except``, and remain so via the caller's identical `try` block).

    ``read_only=True`` never writes ``ip_info.yaml`` — the exact same
    in-memory-only path ``--dry-run`` already used, generalized so
    ``forge build`` (which must never write) can reuse it unconditionally.
    ``read_only=False`` preserves ``gen-top``'s real-write behavior
    exactly. ``emit_progress=False`` suppresses this function's narrative
    ``print()`` calls (used by ``forge build --json`` so stdout carries
    only the JSON payload); it does not change what's computed.

    The 3 structured issue lists (topology-group/cardinality/CDC) are
    always computed here, unlike ``cmd_gen_top``'s own strict-gated calls
    to the same verify_* functions — those functions are pure, non-raising,
    side-effect-free (return a plain issue list, same convention as
    ``contract_verifier``'s other checks), so always computing them costs
    a little extra CPU but changes no observable behavior for ``gen-top``
    itself; only ``cmd_gen_top``'s own (unchanged) ``if args.strict:``
    blocks decide whether to act on them.
    """
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
        if output_dir != Path(".") and not read_only:
            output_dir.mkdir(parents=True, exist_ok=True)
    else:
        output_dir = c_root

    ip_info_file = (
        _resolve_path(args.ip_info, c_root)
        if args.ip_info
        else output_dir / "ip_info.yaml"
    )

    _modules_yml = getattr(args, "contracts_from", None)
    _modules_yml_path: Optional[Path] = None
    contracts = None
    if _modules_yml:
        _modules_yml_path = _resolve_path(_modules_yml, c_root)
        if _modules_yml_path.exists():
            try:
                contracts = load_contracts_for_design(_modules_yml_path, c_root)
                if emit_progress:
                    print(f"📜 Contracts loaded: {len(contracts)} module(s) covered")
                # Contracts whose omitted port facts couldn't be resolved, or
                # that contradict their real ports. Silence here shows up much
                # later as a role mysteriously absent from contract wiring.
                for _conflict in drain_contract_conflicts():
                    print(f"⚠️  {_conflict}")
            except Exception as _ce:
                if emit_progress:
                    print(f"⚠️  Contract loading failed (falling back to heuristics): {_ce}")
        else:
            if emit_progress:
                print(f"⚠️  --contracts-from file not found: {_modules_yml_path}")

    ip_info_generated_now = False
    if ip_info_file.exists():
        ip_info = load_ip_info(ip_info_file)
    elif contracts:
        if emit_progress:
            print("📋 Projecting ip_info from contracts (not from built IP) …")
        _mapped = {}
        for m in cfg.modules:
            c = contracts.get(m.name) or (
                m.ip_info_key and contracts.get(m.ip_info_key)
            )
            if c:
                _mapped[m.name] = c
        ip_info = synthesize_ip_info(_mapped)
    else:
        if emit_progress:
            print("📋 Generating IP summary from build artefacts …")
        summary = collect_all(
            build_root=build_root,
            modules=cfg.modules,
            ip_root=ip_root,
            src_root=src_root,
        )
        if read_only:
            # --dry-run / forge build must never create, modify, or delete
            # project files. Use the in-memory summary directly instead of
            # writing ip_info.yaml to disk and reading it back.
            if emit_progress:
                print(
                    f"📋 [dry-run] IP summary computed in memory "
                    f"(would be written to {ip_info_file} on a real run)"
                )
            ip_info = summary
        else:
            ip_info_file.parent.mkdir(parents=True, exist_ok=True)
            write_summary(summary, ip_info_file, format="yaml")
            ip_info = load_ip_info(ip_info_file)
            ip_info_generated_now = True

    conn_map, global_nets, match_report = auto_match_ports(
        cfg, ip_info, system_yml=args.system, contracts=contracts,
    )

    if emit_progress:
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

    topology_group_issues: List[Any] = []
    if contracts and cfg.topology_groups:
        from forge.contracts.contract_verifier import verify_topology_groups
        topology_group_issues = verify_topology_groups(cfg, contracts)

    cardinality_issues: List[Any] = []
    if contracts:
        from forge.contracts.cardinality import verify_cardinality
        cardinality_issues = verify_cardinality(cfg, contracts, match_report)

    from forge.contracts.cdc import verify_cdc
    cdc_issues = verify_cdc(cfg, contracts or {}, match_report, conn_map, global_nets)

    project = assemble_project_ir(
        cfg, design_path,
        contracts=contracts or {}, ip_info_data=ip_info,
        conn_map=conn_map, global_nets=global_nets, match_report=match_report,
    )

    return GenTopPlanContext(
        cfg=cfg, design_path=design_path,
        c_root=c_root, ip_root=ip_root, build_root=build_root,
        hls_build_root=hls_build_root, src_root=src_root,
        xml_stimulus_tool=xml_stimulus_tool,
        output_dir=output_dir, ip_info_file=ip_info_file,
        contracts_from=_modules_yml, contracts_from_path=_modules_yml_path,
        contracts=contracts, ip_info=ip_info,
        ip_info_generated_now=ip_info_generated_now,
        conn_map=conn_map, global_nets=global_nets, match_report=match_report,
        project=project,
        topology_group_issues=topology_group_issues,
        cardinality_issues=cardinality_issues,
        cdc_issues=cdc_issues,
    )


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

        # compute_gen_top_plan is the shared, sys.exit-free compute phase
        # also used by `forge build`. Unpacked back into the same local
        # names the rest of this function (dry-run preview, all 3 mode
        # branches, unchanged below) already expects — pure code motion.
        ctx = compute_gen_top_plan(
            cfg, design_path, args, read_only=getattr(args, "dry_run", False),
        )
        c_root = ctx.c_root
        ip_root = ctx.ip_root
        build_root = ctx.build_root
        hls_build_root = ctx.hls_build_root
        src_root = ctx.src_root
        xml_stimulus_tool = ctx.xml_stimulus_tool
        output_dir = ctx.output_dir
        ip_info_file = ctx.ip_info_file
        _modules_yml = ctx.contracts_from
        _modules_yml_path = ctx.contracts_from_path
        _contracts = ctx.contracts
        ip_info = ctx.ip_info
        _ip_info_generated_now = ctx.ip_info_generated_now
        conn_map = ctx.conn_map
        global_nets = ctx.global_nets
        match_report = ctx.match_report
        wmc = match_report.wiring_method_counts

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
            tg_errors = [i for i in ctx.topology_group_issues if i.severity == "error"]
            if tg_errors:
                print(
                    f"\n❌ Strict mode: {len(tg_errors)} topology group "
                    "verification error(s):"
                )
                for _i in tg_errors:
                    print(str(_i))
                sys.exit(1)

        if getattr(args, "strict", False) and _contracts:
            card_errors = [i for i in ctx.cardinality_issues if i.severity == "error"]
            if card_errors:
                print(
                    f"\n❌ Strict mode: {len(card_errors)} declarative "
                    "cardinality violation(s):"
                )
                for _i in card_errors:
                    print(str(_i))
                sys.exit(1)

        if getattr(args, "strict", False):
            cdc_errors = [i for i in ctx.cdc_issues if i.severity == "error"]
            if cdc_errors:
                print(
                    f"\n❌ Strict mode: {len(cdc_errors)} undeclared clock/reset "
                    "domain crossing(s):"
                )
                for _i in cdc_errors:
                    print(str(_i))
                sys.exit(1)

        if args.mode == "bd":
            # --gen-testbench and --lint are Verilog/VHDL-specific (a real
            # RTL file to simulate/lint) — --mode bd's output is a Tcl
            # script, not RTL, so there's no equivalent. Reject explicitly
            # rather than silently accepting the flag and doing nothing —
            # a user expecting a testbench/lint result and getting none
            # silently is worse than an upfront error.
            if args.gen_testbench or (cfg.testbench and cfg.testbench.generate):
                print_cli_error(
                    "--gen-testbench is not supported with --mode bd",
                    ValueError("bd mode's output is a Tcl script, not RTL — there is nothing to testbench"),
                    hint="Generate a Verilog/VHDL top for testbench flows, or drive BD-mode "
                         "verification through Vivado directly.",
                )
                sys.exit(1)
            if getattr(args, "lint", False):
                print_cli_error(
                    "--lint is not supported with --mode bd",
                    ValueError("no Verilog/VHDL linter applies to a generated Tcl script"),
                    hint="Drop --lint, or use --mode verilog/vhdl if RTL linting is needed.",
                )
                sys.exit(1)

        if getattr(args, "dry_run", False):
            if args.mode == "vhdl":
                _preview_output = _resolve_path(args.output, c_root, Path(f"{args.top_name}.vhd"))
            elif args.mode == "verilog":
                _preview_output = _resolve_path(args.output, c_root, Path(f"{args.top_name}.v"))
            else:
                _preview_output = _resolve_path(args.output, c_root, Path("block_design.tcl"))

            print(f"\n[dry-run] Would write:")
            print(f"  {_preview_output}")
            if args.mode in ("vhdl", "verilog"):
                for _artifact in (
                    "build_manifest.json", "port_map.yaml", "port_signature.json",
                    "design_parameters.json", "probe_map.yaml", "tb_bindings.svh",
                    "maturity_report.json",
                ):
                    print(f"  {_preview_output.parent / _artifact}")
                # design.ir.json (canonical IR snapshot) is emitted for both
                # verilog and vhdl modes; provenance.json is written as its
                # sibling.
                print(f"  {_preview_output.parent / 'design.ir.json'}")
                print(f"  {_preview_output.parent / 'provenance.json'}")
                _should_gen_tb = args.mode == "verilog" and (
                    args.gen_testbench or (cfg.testbench and cfg.testbench.generate)
                )
                if _should_gen_tb:
                    print(f"  {_preview_output.parent}/  (SystemVerilog testbench — exact name set by generate_sv_testbench)")
            elif args.mode == "bd":
                # --mode bd now produces the same manifest/contract
                # artifact set --mode verilog does (see the bd branch
                # below) — design_parameters.json is the one exception,
                # generated with algo_top_v_path=None since there's no
                # flat Verilog file to associate debug ports from.
                for _artifact in (
                    "build_manifest.json", "port_map.yaml", "port_signature.json",
                    "design_parameters.json", "probe_map.yaml", "tb_bindings.svh",
                    "maturity_report.json",
                ):
                    print(f"  {_preview_output.parent / _artifact}")
                print(f"  {_preview_output.parent / 'design.ir.json'}")
                print(f"  {_preview_output.parent / 'provenance.json'}")

            assert not _ip_info_generated_now, (
                "dry-run must never persist ip_info.yaml to disk"
            )
            print("\n[dry-run] No files written.")
            sys.exit(0)

        if args.mode == "vhdl":
            output = _resolve_path(args.output, c_root, Path(f"{args.top_name}.vhd"))
            print(f"🔨 Generating structural VHDL: {output}")

            # Extended to vhdl mode (same pattern as verilog): proven
            # byte-identical to the original direct conn_map/global_nets on
            # both reference designs (test_generation_ir_equivalence.py)
            # before this switch was made.
            project = assemble_project_ir(
                cfg,
                design_path,
                contracts=_contracts or {},
                ip_info_data=ip_info,
                conn_map=conn_map,
                global_nets=global_nets,
                match_report=match_report,
                generated_from={
                    "design": str(design_path),
                    "contracts_from": str(_modules_yml_path) if _modules_yml and _modules_yml_path.exists() else None,
                    "ip_info": str(ip_info_file) if ip_info_file.exists() else None,
                    "build_dir": str(build_root),
                },
            )
            conn_map_ir, global_nets_ir = project_to_conn_map(project)

            report = write_structural_vhdl(
                cfg=cfg,
                ip_info=ip_info,
                conn_map=conn_map_ir,
                global_nets=global_nets_ir,
                ip_root=ip_root,
                out_path=output,
                top_name=args.top_name,
                system_yml=args.system,
            )

            print(f"✓ VHDL top generated: {output}")
            _print_gen_report(report)

            # Attach tie_off connections — see the
            # verilog branch's identical comment above.
            project.design.connections.extend(build_tie_off_connections(report.get("tied_to_zero", [])))
            project.design.connections.sort(key=lambda c: c.id)

            if getattr(args, "strict", False):
                _strict_port_gate(report, cfg)

            if args.lint:
                _run_vhdl_lint(output)

            ir_output = output.parent / "design.ir.json"
            ir_output.write_text(json.dumps(to_json_dict(project), indent=2, sort_keys=True))
            print(f"  ✓ Canonical IR snapshot: {ir_output}")
            _write_gen_top_provenance(project, ctx, args, ir_output)

        elif args.mode == "verilog":
            output = _resolve_path(args.output, c_root, Path(f"{args.top_name}.v"))
            print(f"🔨 Generating structural Verilog: {output}")

            # Generation is driven by the canonical IR rather than
            # directly by auto_match_ports's conn_map/global_nets. Proven
            # byte-for-byte equivalent to the original direct conn_map on
            # both reference designs (test_generation_ir_equivalence.py)
            # before this switch was made — project_to_conn_map reproduces
            # the exact original iteration order via
            # ResolvedConnection.emission_order, so reg_stage_N/delay_N
            # instance naming and wire-declaration order are unchanged.
            project = assemble_project_ir(
                cfg,
                design_path,
                contracts=_contracts or {},
                ip_info_data=ip_info,
                conn_map=conn_map,
                global_nets=global_nets,
                match_report=match_report,
                generated_from={
                    "design": str(design_path),
                    "contracts_from": str(_modules_yml_path) if _modules_yml and _modules_yml_path.exists() else None,
                    "ip_info": str(ip_info_file) if ip_info_file.exists() else None,
                    "build_dir": str(build_root),
                },
            )
            conn_map_ir, global_nets_ir = project_to_conn_map(project)

            report = write_structural_verilog(
                cfg=cfg,
                ip_info=ip_info,
                conn_map=conn_map_ir,
                global_nets=global_nets_ir,
                ip_root=ip_root,
                out_path=output,
                top_name=args.top_name,
                system_yml=args.system,
                contracts=_contracts,
                match_report=match_report,
            )

            print(f"✓ Verilog top generated: {output}")
            _print_gen_report(report)

            # Attach the generator's resolved top-level port list to the IR
            # object built above — this is the one place
            # both pieces of information exist together; `project` is
            # serialized to design.ir.json further below.
            project.design.top_ports = [
                ResolvedTopLevelPort(
                    name=p["name"], direction=p["direction"], width=p["width"],
                    origin=p.get("origin"),
                    instance_id=p.get("instance"),
                    instance_port=p.get("port"),
                )
                for p in report.get("top_ports", [])
            ]

            # Attach tie_off connections — same timing/
            # asymmetry as top_ports: only known after generation runs, so
            # absent from forge inspect's pre-generation IR.
            project.design.connections.extend(build_tie_off_connections(report.get("tied_to_zero", [])))
            project.design.connections.sort(key=lambda c: c.id)

            if getattr(args, "strict", False):
                _strict_port_gate(report, cfg)

            # Before the manifest: the manifest records the IR's content
            # hash, and both of these are part of the IR's content.
            project.design.top_module = args.top_name
            _attach_verification_plan(
                project, design_path, args, c_root, dut_dir=output.parent,
            )

            manifest_output = output.parent / "build_manifest.json"
            print(f"📦 Generating build manifest: {manifest_output}")
            generate_build_manifest(
                project=project,
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
            # Use the generator's own resolved top-level port list
            # (report["top_ports"]) instead of re-parsing it back out of
            # the file just written.
            _top_ports_for_map = {
                p["name"]: (p["direction"], p["width"]) for p in report.get("top_ports", [])
            }
            port_map_data, port_sig_hash = generate_port_map(
                output, port_map_output, cfg.interface_metadata, ports=_top_ports_for_map or None
            )

            if port_map_data is not None:
                from forge.core.utils import port_signature as _sig
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
            maturity = _compute_maturity_summary(
                cfg, match_report, report, ir_content_hash=ir_content_hash_of(project),
            )
            maturity["port_signature_hash"] = port_sig_hash if port_map_data else None
            maturity_output.write_text(json.dumps(maturity, indent=2) + "\n")
            print(f"  ✓ Maturity report: {maturity_output}")

            # Canonical IR snapshot — `project` was already built above to
            # drive this generation run; reuse it rather
            # than building it a second time.
            ir_output = output.parent / "design.ir.json"
            ir_output.write_text(json.dumps(to_json_dict(project), indent=2, sort_keys=True))
            print(f"  ✓ Canonical IR snapshot: {ir_output}")
            _write_gen_top_provenance(project, ctx, args, ir_output)

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

            # Extended to bd mode (same pattern as verilog/vhdl):
            # proven byte-identical to the original direct conn_map/
            # global_nets on both reference designs
            # (test_generation_ir_equivalence.py) before this switch.
            project = assemble_project_ir(
                cfg,
                design_path,
                contracts=_contracts or {},
                ip_info_data=ip_info,
                conn_map=conn_map,
                global_nets=global_nets,
                match_report=match_report,
                generated_from={
                    "design": str(design_path),
                    "contracts_from": str(_modules_yml_path) if _modules_yml and _modules_yml_path.exists() else None,
                    "ip_info": str(ip_info_file) if ip_info_file.exists() else None,
                    "build_dir": str(build_root),
                },
            )
            conn_map_ir, global_nets_ir = project_to_conn_map(project)

            report = write_bd_tcl(
                cfg=cfg,
                ip_info=ip_info,
                conn_map=conn_map_ir,
                global_nets=global_nets_ir,
                out_path=output,
                bd_name=args.bd_name,
                src_root=design_path.parent,
                ip_root=ip_root,
                system_yml=args.system,
            )

            print(f"✓ Block Design TCL generated: {output}")
            _print_gen_report(report)

            # Attach the generator's resolved top-level port list to the
            # IR — same join verilog mode performs, from the same shared
            # resolver (forge.generation.generators._port_resolution), so
            # the two modes can never independently disagree on it.
            project.design.top_ports = [
                ResolvedTopLevelPort(
                    name=p["name"], direction=p["direction"], width=p["width"],
                    origin=p.get("origin"),
                    instance_id=p.get("instance"),
                    instance_port=p.get("port"),
                )
                for p in report.get("top_ports", [])
            ]

            # Attach tie_off connections — same as verilog mode.
            project.design.connections.extend(build_tie_off_connections(report.get("tied_to_zero", [])))
            project.design.connections.sort(key=lambda c: c.id)

            if getattr(args, "strict", False):
                _strict_port_gate(report, cfg)

            # BD mode has no Verilog/VHDL top module name — the BD's own
            # name is the closest equivalent for provenance/manifest
            # bookkeeping (deliberate divergence from verilog's
            # args.top_name).
            project.design.top_module = args.bd_name

            manifest_output = output.parent / "build_manifest.json"
            print(f"📦 Generating build manifest: {manifest_output}")
            generate_build_manifest(
                project=project,
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
            # generate_port_map never re-parses `output` (a Tcl file, not
            # Verilog) since `ports=` is supplied here — same mechanism
            # the verilog branch uses to avoid re-parsing algo_top.v.
            _top_ports_for_map = {
                p["name"]: (p["direction"], p["width"]) for p in report.get("top_ports", [])
            }
            port_map_data, port_sig_hash = generate_port_map(
                output, port_map_output, cfg.interface_metadata, ports=_top_ports_for_map or None
            )

            if port_map_data is not None:
                from forge.core.utils import port_signature as _sig
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
            # algo_top_v_path=None: block_design.tcl isn't Verilog, so the
            # module-level debug-port-association section
            # (design_parameters.py's parse_algo_top_ports) can't read it
            # — a known, accepted gap for --mode bd (no debug-port
            # metadata in design_parameters.json yet). The interface-counts
            # section still populates fully from port_map_data.
            write_design_parameters(
                cfg,
                params_output,
                algo_top_v_path=None,
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
            maturity = _compute_maturity_summary(
                cfg, match_report, report, ir_content_hash=ir_content_hash_of(project),
            )
            maturity["port_signature_hash"] = port_sig_hash if port_map_data else None
            maturity_output.write_text(json.dumps(maturity, indent=2) + "\n")
            print(f"  ✓ Maturity report: {maturity_output}")

            # Canonical IR snapshot — `project` was already built above,
            # now carrying top_ports/tie-offs attached above, to drive
            # this generation run; reuse it rather than building it twice.
            ir_output = output.parent / "design.ir.json"
            ir_output.write_text(json.dumps(to_json_dict(project), indent=2, sort_keys=True))
            print(f"  ✓ Canonical IR snapshot: {ir_output}")
            _write_gen_top_provenance(project, ctx, args, ir_output)

    except Exception as e:
        print_cli_error(
            "Generation failed",
            e,
            hint="Check the design file, mode, and IP paths, then re-run forge topgen gen-top.",
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# gen-top internal helpers
# ---------------------------------------------------------------------------

def _gen_top_command_options(args) -> Dict[str, Any]:
    """The subset of CLI args that affect generation output — recorded in
    the provenance manifest so a future ``--explain-staleness`` can tell
    "you changed how you called gen-top" apart from "an input file
    changed" (same rationale as
    ``forge.core.cli.groups.inspect._command_options``, scoped to
    gen-top's own flags)."""
    _system = getattr(args, "system", None)
    _contracts_from = getattr(args, "contracts_from", None)
    return {
        "mode": getattr(args, "mode", None),
        "top_name": getattr(args, "top_name", None),
        "strict": getattr(args, "strict", False),
        # --system/--contracts-from are argparse `type=Path` — stringify so
        # the manifest stays plain-JSON-serializable (a bare `Path` isn't).
        "system": str(_system) if _system is not None else None,
        "contracts_from": str(_contracts_from) if _contracts_from is not None else None,
    }


def _write_gen_top_provenance(
    project, ctx: "GenTopPlanContext", args, ir_output: Path,
) -> Path:
    """Write ``provenance.json`` as a sibling of ``design.ir.json`` — same
    ``project`` object, same directory, same timing as the IR snapshot
    (closing the gap between the existing IR-attached provenance manifest
    and gen-top, which never wrote one).

    ``plan_hash`` is computed from ``ctx.project`` (the *pre-generation*
    IR, before tie-off/top-port attachment) via the exact same
    ``build_generation_plan``/``plan_hash`` call ``forge build`` already
    uses — so a plan hash pinned via ``forge build --accept-plan-hash``
    can be compared directly against the one a subsequent ``gen-top`` run
    records here. ``output_hashes`` reuses ``forge build``'s own
    ``_planned_output_artifacts`` for the candidate file list rather than
    re-deriving gen-top's artifact set a third time — files that mode
    didn't actually write (e.g. verilog-only artifacts when nothing
    needed a testbench) are silently skipped by ``build_provenance``,
    which only hashes files that exist.
    """
    from forge.ir.provenance import build_provenance, write_provenance
    from forge.ir.plan import build_generation_plan, plan_hash as _plan_hash_of
    from forge.core.cli.groups.build import _planned_output_artifacts

    plan = build_generation_plan(
        ctx.project, ctx.match_report,
        topology_group_issues=ctx.topology_group_issues,
        cardinality_issues=ctx.cardinality_issues,
        cdc_issues=ctx.cdc_issues,
        compat_mode_modules=ctx.match_report.compat_mode_modules,
        output_artifacts=_planned_output_artifacts(ctx, args),
    )

    manifest = build_provenance(
        project,
        command_options=_gen_top_command_options(args),
        plan_hash=_plan_hash_of(plan),
        output_paths=_planned_output_artifacts(ctx, args),
        project_identity=ctx.c_root.name or None,
    )
    provenance_output = ir_output.parent / "provenance.json"
    write_provenance(provenance_output, manifest)
    print(f"  ✓ Provenance manifest: {provenance_output}")
    return provenance_output


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
        from forge.core.utils.vhdl_linter import lint_vhdl_file
        print("\n🔍 Running GHDL syntax checker...")
        lint_vhdl_file(output, std="08", ieee="synopsys")
    except ImportError:
        print("⚠️  VHDL linter requires GHDL. Please install it:")
        print("   - Ubuntu/Debian: sudo apt-get install ghdl")
        print("   - Fedora/RHEL: sudo dnf install ghdl")
        print("   - macOS: brew install ghdl")


def _run_verilog_lint(output: Path, top_name: str) -> None:
    try:
        from forge.core.utils.verilog_linter import lint_verilog_file
        print("\n🔍 Running Verilog linter (Verilator)...")
        lint_verilog_file(output, top_module=top_name)
    except ImportError:
        print("⚠️  Verilog linter requires Verilator. Please install it:")
        print("   - Ubuntu/Debian: sudo apt-get install verilator")
        print("   - Fedora/RHEL: sudo dnf install verilator")
        print("   - macOS: brew install verilator")


# ---------------------------------------------------------------------------
# init-plugin command — scaffold the topgen side of a new plugin capsule
# ---------------------------------------------------------------------------
# Mirrors forge.verification.__main__._cmd_init_plugin's shape (template-string
# constants, files_to_create dict, --dry-run lists without writing, skip
# already-existing files on a real run). This scaffolds the *topgen* half
# (modules.yml, designs/design.yml, interfaces/*.yaml, an RTL stub);
# `forge verify init-plugin` scaffolds the verify/ half separately.
#
# plugin_id is used as both the module ref/registry name *and* the
# design.yml instance name, so ip_info_key trivially equals the design
# instance name from the start — sidestepping the exact ip_info_key
# mismatch bug found and fixed in plugins/passthrough_demo's own contract
# (see CHANGELOG.md).

_INIT_MODULES_YML_TEMPLATE = '''\
# ═══════════════════════════════════════════════════════════════════════════
# {plugin_id} — Module Registry
# ═══════════════════════════════════════════════════════════════════════════
# Scaffolded by `forge init`. See docs/PLUGIN_AUTHOR_GUIDE.md
# to register HLS modules, add topology_groups, or grow beyond this single
# RTL passthrough stub.
# ═══════════════════════════════════════════════════════════════════════════
registry_version: '1'

defaults:
  part: xcvu9p-flga2104-2L-e
  clock_period: 4.0
  vendor: {plugin_id}
  version: '1.0'

modules:

- name: {plugin_id}
  kind: rtl
  rtl_lang: verilog
  top: {plugin_id}
  latency_hint: 1
  interface_contract: interfaces/{plugin_id}.interface.yaml
  src: [../algo/rtl/{plugin_id}.v]
'''

_INIT_DESIGN_YML_TEMPLATE = '''\
# ═══════════════════════════════════════════════════════════════════════════
# {plugin_id} — Design Topology
# ═══════════════════════════════════════════════════════════════════════════
# Scaffolded by `forge init`. Single module, both data ports
# exposed at the top level — the minimal case. Add `connections:` or
# `topology_groups:` here as you add more modules.
# ═══════════════════════════════════════════════════════════════════════════

part: xcvu9p-flga2104-2L-e
clock_period: 4.0

block_protocol: none
connect_clock: true
connect_reset: true

registry: ../modules.yml

modules:

- name: {plugin_id}
  ref: {plugin_id}
  instances: 1
  external_in_ports: [data_in, data_in_valid]
  external_out_ports: [data_out, data_out_valid]
'''

_INIT_INTERFACE_YAML_TEMPLATE = '''\
# Integration contract for the {plugin_id} RTL reference module.
# Scaffolded by `forge init`.

ip_interface:
  module_name: {plugin_id}
  # Optional: defaults to module_name above. Declare it only when a design
  # instance (design.yml modules[].name) is named differently from its
  # module — `forge core verify-contract` names the available keys if it
  # doesn't resolve.
  ip_info_key: {plugin_id}
  source_type: rtl
  normalization_status: ready
  notes: >
    Minimal single-module reference generated by `forge init`.
    Registers an 8-bit data path one clock cycle.

  roles:

    clock_primary:
      raw_port: ap_clk
      direction: input
      width: 1

    reset_primary:
      raw_port: ap_rst
      direction: input
      width: 1
      active_level: high

    data_in:
      raw_port: data_in
      direction: input
      width: 8

    data_in_valid:
      raw_port: data_in_valid
      direction: input
      width: 1

    data_out:
      raw_port: data_out
      direction: output
      width: 8

    data_out_valid:
      raw_port: data_out_valid
      direction: output
      width: 1
'''

_INIT_RTL_STUB_TEMPLATE = '''\
//==============================================================================
// {plugin_id}.v
//==============================================================================
// Scaffolded by `forge init`. A minimal registered N-bit
// passthrough with a valid strobe — replace the body with your real logic.
// Ports and behavior deliberately match plugins/passthrough_demo's proven
// reference implementation, so `forge topgen gen-top` succeeds immediately
// with zero further edits.
//
// Parameters:
//   WIDTH - Bit width of the data path (default: 8)
//==============================================================================

`timescale 1ns / 1ps

module {plugin_id} #(
    parameter WIDTH = 8
)(
    input  wire             ap_clk,
    input  wire             ap_rst,
    input  wire [WIDTH-1:0] data_in,
    input  wire             data_in_valid,
    output reg  [WIDTH-1:0] data_out,
    output reg              data_out_valid
);

    always @(posedge ap_clk) begin
        if (ap_rst) begin
            data_out       <= {{WIDTH{{1'b0}}}};
            data_out_valid <= 1'b0;
        end else begin
            data_out       <= data_in;
            data_out_valid <= data_in_valid;
        end
    end

endmodule
'''


def cmd_init_plugin(args):
    """Scaffold the topgen side of a new plugin capsule.

    Creates ``<plugins_root>/<plugin_id>/forge/{{modules.yml,designs/design.yml,
    interfaces/<plugin_id>.interface.yaml}}`` and
    ``<algo_root>/<plugin_id>/algo/rtl/<plugin_id>.v``.
    """
    plugin_id = args.plugin_id
    plugins_root = Path(args.plugins_root)
    algo_root = Path(getattr(args, "algo_root", None) or args.plugins_root)
    forge_root = plugins_root / plugin_id / "forge"
    algo_dir = algo_root / plugin_id / "algo" / "rtl"

    files_to_create = {
        forge_root / "modules.yml": _INIT_MODULES_YML_TEMPLATE.format(plugin_id=plugin_id),
        forge_root / "designs" / "design.yml": _INIT_DESIGN_YML_TEMPLATE.format(plugin_id=plugin_id),
        forge_root / "interfaces" / f"{plugin_id}.interface.yaml":
            _INIT_INTERFACE_YAML_TEMPLATE.format(plugin_id=plugin_id),
        algo_dir / f"{plugin_id}.v": _INIT_RTL_STUB_TEMPLATE.format(plugin_id=plugin_id),
    }

    if args.dry_run:
        print(f"[init-plugin] Would create plugin skeleton for {plugin_id!r}:")
        for path in sorted(files_to_create):
            print(f"  {path}")
        return

    created: list[Path] = []
    skipped: list[Path] = []
    for dest_path, content in files_to_create.items():
        if dest_path.exists():
            skipped.append(dest_path)
            continue
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text(content)
        created.append(dest_path)

    print(f"[init-plugin] Plugin {plugin_id!r} scaffolded:")
    for p in created:
        print(f"  created  {p}")
    for p in skipped:
        print(f"  skipped  {p}  (already exists)")

    design_yml = forge_root / "designs" / "design.yml"
    modules_yml = forge_root / "modules.yml"
    # This command scaffolds only the topology half. `forge init` runs it as
    # one of its stages and hides this epilogue, so what's printed here is
    # what someone who ran *this* command alone still has to do.
    print("\nNext steps:")
    print(f"  1. Run: forge verify init-plugin {plugin_id}   (scaffolds the verify/ half)")
    print(
        f"  2. Run: forge build {design_yml} "
        f"--contracts-from {modules_yml} --apply --output out/algo_top.v"
    )
    print(f"  3. Run: forge test prepare {forge_root / 'verify' / 'design.verification.yml'}")
    print("\n  (or run `forge init` next time to do all of the above in one step)")


def cmd_migrate(args):
    """Migration helpers — see
    docs/development/MIGRATION_TOOLING.md for the full policy of each kind.

    Every kind computes its change fully in memory, always prints a
    readable diff/plan, and only writes to disk when ``--dry-run`` is not
    passed — the same compute-then-write-gated-by-dry-run pattern used by
    ``cmd_gen_top``/``cmd_clean``/``cmd_init_plugin``.
    """
    from forge.generation.migrate import (
        apply_legacy_plugin_layout,
        apply_rename_verify_contract,
        find_legacy_plugin_layout,
        find_legacy_verify_contract_name,
        migrate_schema_version,
        partition_to_coordinates,
        unified_diff_text,
    )

    kind = args.kind
    dry_run = getattr(args, "dry_run", False)

    if kind == "schema-version":
        if not args.file:
            print("❌ --file is required for --kind schema-version", file=sys.stderr)
            sys.exit(2)
        if not args.file.exists():
            print(f"❌ file not found: {args.file}", file=sys.stderr)
            sys.exit(2)
        try:
            result = migrate_schema_version(args.file, schema_kind=args.schema_kind)
        except ValueError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            sys.exit(2)
        print(result.message)
        if result.diff:
            print(result.diff)
        if result.changed and not dry_run:
            args.file.write_text(result.new_content)
            print(f"✅ wrote {args.file}")
        sys.exit(0)

    elif kind == "partition-to-coordinates":
        if not args.contract or not args.axis:
            print("❌ --contract and --axis are required for --kind partition-to-coordinates", file=sys.stderr)
            sys.exit(2)
        if not args.contract.exists():
            print(f"❌ file not found: {args.contract}", file=sys.stderr)
            sys.exit(2)
        text = args.contract.read_text()
        try:
            new_text, changed_roles = partition_to_coordinates(text, axis=args.axis, role=args.role)
        except ValueError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            sys.exit(2)
        if not changed_roles:
            print(f"{args.contract.name}: nothing to migrate")
            sys.exit(0)
        print(unified_diff_text(text, new_text, args.contract))
        print(f"Roles migrated: {', '.join(changed_roles)}")
        if not dry_run:
            args.contract.write_text(new_text)
            print(f"✅ wrote {args.contract}")
        sys.exit(0)

    elif kind == "legacy-plugin-layout":
        if not args.plugin_root:
            print("❌ --plugin-root is required for --kind legacy-plugin-layout", file=sys.stderr)
            sys.exit(2)
        issue = find_legacy_plugin_layout(args.plugin_root)
        if issue.is_clean():
            print("Nothing to migrate — layout already current.")
            sys.exit(0)
        if issue.move_needed:
            print(f"Would move: {issue.old_verify_dir} -> {issue.new_verify_dir}")
        for f in issue.fw_python_files:
            print(f"Would remove _FW_PYTHON sys.path block from: {f}")
        for f in issue.manual_files:
            print(f"⚠️  {f}: mentions _FW_PYTHON but the block wasn't confidently "
                  "recognized — remove it manually.")
        if not dry_run:
            for action in apply_legacy_plugin_layout(issue):
                print(f"✅ {action}")
        sys.exit(0)

    elif kind == "rename-verify-contract":
        if not args.plugin_root:
            print("❌ --plugin-root is required for --kind rename-verify-contract", file=sys.stderr)
            sys.exit(2)
        legacy = find_legacy_verify_contract_name(args.plugin_root)
        if legacy is None:
            print("Nothing to migrate.")
            sys.exit(0)
        new_path = legacy.with_name("design.verification.yml")
        print(f"Would rename: {legacy} -> {new_path}")
        if not dry_run:
            apply_rename_verify_contract(legacy)
            print(f"✅ renamed to {new_path}")
        sys.exit(0)

    else:
        print(f"❌ unknown --kind: {kind!r}", file=sys.stderr)
        sys.exit(2)


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------

def register(sub) -> None:
    """Register the ``forge topgen`` subparser and its commands."""
    p_tg = sub.add_parser("topgen", help="Generate hardware topology structure")
    tg_sub = p_tg.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # forge topgen gen-top
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
            "port_map_ranges, topology groups have verification errors, a "
            "declared cardinality (producers/consumers/fanout/completeness) is violated, "
            "or a connection crosses clock/reset domains without a declared 'cdc:' adapter"
        ),
    )
    p_gen.add_argument("--lint", action="store_true", help="Run linter after generation")
    p_gen.add_argument("--fix-lint", action="store_true", help="Auto-fix linting issues")
    p_gen.add_argument(
        "--rtl-resource-root", type=Path,
        help="Framework RTL helpers root; sets ${TOPGEN_RTL_RESOURCE_ROOT}",
    )
    p_gen.add_argument(
        "--verify-design", type=Path,
        help="design.verification.yml to resolve the IR's verification plan "
             "against (default: ../verify/design.verification.yml, when present)",
    )
    p_gen.add_argument(
        "--no-verify", action="store_true",
        help="Skip verification-plan resolution; the emitted IR carries no plan",
    )
    p_gen.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=False,
        help="Validate, resolve IP info, and match ports, then print what "
             "would be written without generating any output files.",
    )
    p_gen.set_defaults(func=cmd_gen_top)

    # forge topgen validate
    p_validate = tg_sub.add_parser("validate", help="Validate design.yml without generating")
    p_validate.add_argument("design", type=Path, help="design.yaml file to validate")
    p_validate.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    p_validate.add_argument(
        "--check-stale", action="store_true", default=False,
        help="Also verify that generated artifacts are not older than design.yml.",
    )
    p_validate.add_argument(
        "--cdc-result-json", dest="cdc_result_json", default=None,
        help="Write a forge.cdc_verification_result.v1 artifact to this path "
             "(only when the registry/contracts needed for the CDC check are available)",
    )
    p_validate.add_argument(
        "--json", action="store_true", default=False,
        help="Machine-readable JSON output instead of the human report.",
    )
    p_validate.set_defaults(func=cmd_validate)

    # forge topgen validate-registry
    p_val_reg = tg_sub.add_parser(
        "validate-registry", help="Validate a modules.yml registry independently",
    )
    p_val_reg.add_argument("registry", type=Path, help="modules.yml registry file to validate")
    p_val_reg.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    p_val_reg.add_argument(
        "--json", action="store_true", default=False,
        help="Machine-readable JSON output instead of the human report.",
    )
    p_val_reg.set_defaults(func=cmd_validate_registry)

    # forge topgen ip-summary
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

    # forge topgen match-ports
    p_match = tg_sub.add_parser("match-ports", help="Show port connection report")
    p_match.add_argument("design", type=Path, help="design.yaml file")
    p_match.add_argument(
        "--consumer-root", type=Path,
        help="Consumer workspace root used to resolve relative defaults",
    )
    p_match.add_argument("--ip-info", type=Path, help="IP info file (default: ip_info.yaml)")
    p_match.add_argument("--system", type=Path, help="system.yml for global nets")
    p_match.set_defaults(func=cmd_match_ports)

    # forge topgen unpack-ips
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

    # forge topgen clean
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

    # forge topgen lint
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

    # forge topgen lint-verilog
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

    # forge topgen init-plugin
    p_init = tg_sub.add_parser(
        "init-plugin",
        help="Scaffold the topgen side of a new plugin (modules.yml, "
             "design.yml, interface contract, RTL stub)",
    )
    p_init.add_argument("plugin_id", help="Plugin identifier (e.g. 'my_algo')")
    p_init.add_argument(
        "--plugins-root", default="plugins",
        help="Root directory under which to create <plugin_id>/forge/ (default: plugins/)",
    )
    p_init.add_argument(
        "--algo-root", default=None,
        help="Root directory under which to create <plugin_id>/algo/ "
             "(default: same as --plugins-root)",
    )
    p_init.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=False,
        help="Print what would be created without writing files",
    )
    p_init.set_defaults(func=cmd_init_plugin)

    p_migrate = tg_sub.add_parser(
        "migrate",
        help="Migration helpers: schema-version insertion, partition->coordinates, "
             "legacy plugin layout, legacy verify-contract filename",
    )
    p_migrate.add_argument(
        "--kind", required=True,
        choices=[
            "schema-version", "partition-to-coordinates", "legacy-plugin-layout",
            "rename-verify-contract",
        ],
        help="Which migration to run",
    )
    p_migrate.add_argument(
        "--file", type=Path,
        help="[schema-version] path to the design.yml/modules.yml/*.interface.yaml/"
             "design.verification.yml file to migrate",
    )
    p_migrate.add_argument(
        "--schema-kind", dest="schema_kind",
        choices=["design", "registry", "interface", "verify_contract"],
        default=None,
        help="[schema-version] override auto-detection from --file's filename",
    )
    p_migrate.add_argument(
        "--contract", type=Path,
        help="[partition-to-coordinates] path to the *.interface.yaml file",
    )
    p_migrate.add_argument(
        "--axis", default=None,
        help="[partition-to-coordinates] axis name to wrap the partition value under",
    )
    p_migrate.add_argument(
        "--role", default=None,
        help="[partition-to-coordinates] migrate only this role (default: all eligible roles)",
    )
    p_migrate.add_argument(
        "--plugin-root", type=Path,
        help="[legacy-plugin-layout, rename-verify-contract] plugin root directory "
             "(the directory containing verify/ or forge/)",
    )
    p_migrate.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=False,
        help="Show the planned change/diff without writing anything",
    )
    p_migrate.set_defaults(func=cmd_migrate)
