"""Generating a structural top level: plan, gates, generation, artifacts.

``forge build`` and ``forge dev topgen gen-top`` are both front ends to this
module. :func:`compute_gen_top_plan` resolves paths, contracts, IP metadata
and wiring into a :class:`GenTopPlanContext`; :func:`gate_failure` says
whether that plan may be generated; :func:`generate_top` writes the top
level for the requested mode and every artifact delivered with it
(:mod:`forge.generation.artifacts`). Nothing here exits the process: a
refusal is a message or a :class:`GenerationRefused`, and the caller
decides what to do with it.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from forge.contracts.config import DesignConfig
from forge.contracts.contract_loader import (
    drain_contract_conflicts,
    load_contracts_for_design,
    synthesize_ip_info,
)
from forge.contracts.matcher import auto_match_ports, load_ip_info
from forge.contracts.parser import collect_all, write_summary
from forge.core.utils.path_resolution import consumer_root, resolve_path
from forge.generation.artifacts import (
    compute_maturity_summary,
    filter_unaccounted,
    generate_build_manifest,
    generate_port_map,
    generate_probe_map,
    generate_tb_bindings,
    validate_generated_contracts,
)
from forge.generation.generators.block_design import write_bd_tcl
from forge.generation.generators.design_parameters import write_design_parameters
from forge.generation.generators.structural_verilog import write_structural_verilog
from forge.generation.generators.vhdl_wrapper import write_vhdl_wrapper
from forge.ir.build import assemble_project_ir, build_tie_off_connections
from forge.ir.model import ResolvedTopLevelPort
from forge.ir.project import project_to_conn_map
from forge.ir.serialize import content_hash as ir_content_hash_of, to_json_dict


@dataclass
class GenTopOptions:
    """How to generate: the options ``gen-top`` and ``build`` share.

    Paths (``str`` or ``Path``) may be relative; they resolve against the
    consumer root.
    """
    mode: str = "verilog"
    top_name: str = "algo_top"
    bd_name: str = "design_1"
    output: Optional[str] = None
    consumer_root: Optional[str] = None
    contracts_from: Optional[str] = None
    ip_root: Optional[str] = None
    ip_info: Optional[str] = None
    build_dir: Optional[str] = None
    hls_build_root: Optional[str] = None
    src_root: Optional[str] = None
    system: Optional[str] = None
    xml_stimulus_tool: Optional[str] = None
    hls_metrics: Optional[str] = None
    strict: bool = False

    @classmethod
    def from_namespace(cls, args) -> "GenTopOptions":
        """The options a parsed command line carries, defaults for the rest."""
        values = {}
        for field in dataclasses.fields(cls):
            value = getattr(args, field.name, None)
            if value is not None:
                values[field.name] = value
        return cls(**values)


class GenerationRefused(Exception):
    """Generation stopped on a finding; ``str()`` is the report to show."""


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
    system_yml: Optional[Path]
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
    contract_conflicts: List[str] = dataclasses.field(default_factory=list)
    # delay_cycles: auto, resolved from the module latencies (None: the design
    # has no auto delay). Already applied to cfg when it has no problems.
    auto_delays: Any = None


def compute_gen_top_plan(
    cfg: DesignConfig,
    design_path: Path,
    options: "GenTopOptions",
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
    itself; only ``cmd_gen_top``'s own (unchanged) ``if options.strict:``
    blocks decide whether to act on them.
    """
    c_root = consumer_root(design_path, getattr(options, "consumer_root", None))
    ip_root = resolve_path(options.ip_root, c_root, Path("ips"))
    build_root = resolve_path(options.build_dir, c_root, Path("build"))
    hls_build_root = resolve_path(
        getattr(options, "hls_build_root", None), c_root, Path("build_hls")
    )
    src_root = (
        resolve_path(options.src_root, c_root)
        if hasattr(options, "src_root") and options.src_root
        else design_path.parent
    )
    xml_stimulus_tool = resolve_path(
        getattr(options, "xml_stimulus_tool", None), c_root
    )
    system_yml = resolve_path(options.system, c_root)

    if options.output:
        output = resolve_path(options.output, c_root)
        output_dir = output.parent
        if output_dir != Path(".") and not read_only:
            output_dir.mkdir(parents=True, exist_ok=True)
    else:
        output_dir = c_root

    ip_info_file = (
        resolve_path(options.ip_info, c_root)
        if options.ip_info
        else output_dir / "ip_info.yaml"
    )

    _modules_yml = getattr(options, "contracts_from", None)
    _modules_yml_path: Optional[Path] = None
    contracts = None
    contract_conflicts: List[str] = []
    if _modules_yml:
        _modules_yml_path = resolve_path(_modules_yml, c_root)
        if _modules_yml_path.exists():
            try:
                drain_contract_conflicts()  # only this load's conflicts count below
                contracts = load_contracts_for_design(_modules_yml_path, c_root)
                if emit_progress:
                    print(f"📜 Contracts loaded: {len(contracts)} module(s) covered")
                # Contracts whose omitted port facts couldn't be resolved, or
                # that contradict their real ports. Silence here shows up much
                # later as a role mysteriously absent from contract wiring.
                contract_conflicts = drain_contract_conflicts()
                for _conflict in contract_conflicts:
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
        if options.mode == "bd" and emit_progress:
            # synthesize_ip_info's projected metadata is a placeholder
            # (vendor/version literally "contract") -- fine for --mode
            # verilog (VLNV is never used there), but --mode bd's
            # create_bd_cell needs a real, catalog-resolvable VLNV or Vivado
            # fails deep inside a batch build with no indication why.
            _placeholder_mods = [
                name for name, meta in ip_info.items()
                if meta.get("kind") != "hdl" and meta.get("vendor") == "contract"
            ]
            if _placeholder_mods:
                print(
                    f"⚠️  --mode bd: {len(_placeholder_mods)} packaged-IP module(s) "
                    f"have placeholder vendor/version from --contracts-from "
                    f"(e.g. {_placeholder_mods[0]!r}) -- Vivado's create_bd_cell will "
                    f"fail to resolve them. Generate real IP metadata with "
                    f"`forge dev topgen ip-summary --ip-root <built-IP dir>` and pass it "
                    f"via --ip-info alongside --contracts-from."
                )
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
        cfg, ip_info, system_yml=system_yml, contracts=contracts,
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

    auto_delays = None
    if any(c.delay_auto for c in cfg.connections) or any(t.delay_auto for t in cfg.topology_groups):
        from forge.analysis.latency_static.balance import apply_auto_delays, resolve_design_auto_delays
        auto_delays = resolve_design_auto_delays(design_path, hls_build_root, conn_map)
        if not auto_delays.problems:
            apply_auto_delays(cfg, auto_delays)
        if emit_progress:
            _print_auto_delays(cfg, auto_delays)

    project = assemble_project_ir(
        cfg, design_path,
        contracts=contracts or {}, ip_info_data=ip_info,
        conn_map=conn_map, global_nets=global_nets, match_report=match_report,
    )

    return GenTopPlanContext(
        cfg=cfg, design_path=design_path,
        c_root=c_root, ip_root=ip_root, build_root=build_root,
        hls_build_root=hls_build_root, src_root=src_root,
        xml_stimulus_tool=xml_stimulus_tool, system_yml=system_yml,
        output_dir=output_dir, ip_info_file=ip_info_file,
        contracts_from=_modules_yml, contracts_from_path=_modules_yml_path,
        contracts=contracts, ip_info=ip_info,
        ip_info_generated_now=ip_info_generated_now,
        conn_map=conn_map, global_nets=global_nets, match_report=match_report,
        project=project,
        topology_group_issues=topology_group_issues,
        cardinality_issues=cardinality_issues,
        cdc_issues=cdc_issues,
        contract_conflicts=contract_conflicts,
        auto_delays=auto_delays,
    )


def _print_auto_delays(cfg: DesignConfig, result) -> None:
    print(f"\n⏱️  delay_cycles: auto ({len(result.delays)} resolved from the module latencies)")
    for d in result.delays:
        owner = cfg.connections[d.index] if d.kind == "connection" else cfg.topology_groups[d.index]
        label = getattr(owner, "name", None) or f"{owner.from_} -> {owner.to}"
        print(f"  {label}: {d.cycles}")
    for problem in result.problems:
        print(f"  ❌ {problem}")


def gate_failure(ctx: GenTopPlanContext, options: GenTopOptions) -> Optional[str]:
    """The report of the first finding that stops this plan from being
    generated, or ``None``. ``--strict`` adds the findings about heuristic
    wiring; the others always stop it."""
    match_report = ctx.match_report
    wmc = match_report.wiring_method_counts

    if ctx.auto_delays is not None and ctx.auto_delays.problems:
        return f"\n❌ {len(ctx.auto_delays.problems)} delay_cycles: auto could not be resolved (listed above)."
    if options.strict and ctx.contract_conflicts:
        return (
            f"\n❌ Strict mode: {len(ctx.contract_conflicts)} interface contract "
            "conflict(s) with the module source (listed above).\n"
            "   Correct the contract or the source, or remove --strict."
        )
    if options.strict and match_report.has_compat_modules():
        return "\n".join([
            "\n❌ Strict mode failure: the following modules have no interface"
            " contract and were wired via heuristics:",
            *(f"   • {m}" for m in match_report.compat_mode_modules),
            "   Add interface contracts or remove --strict to proceed.",
        ])
    if options.strict and wmc["auto_match"] > 0:
        return (
            f"\n❌ Strict mode: {wmc['auto_match']} connection(s) used auto-match heuristics.\n"
            "   Add explicit port_map, contract_wiring, or topology_groups to each connection."
        )
    if options.strict and wmc.get("port_map_ranges", 0) > 0:
        return (
            f"\n❌ Strict mode: {wmc['port_map_ranges']} connection(s) still use port_map_ranges.\n"
            "   Migrate to topology_groups (contract-driven) or remove --strict."
        )
    for issues, what in (
        (ctx.topology_group_issues, "topology group verification error(s)"),
        (ctx.cardinality_issues, "declarative cardinality violation(s)"),
        (ctx.cdc_issues, "undeclared clock/reset domain crossing(s)"),
    ):
        errors = [i for i in issues if i.severity == "error"]
        if errors:
            return "\n".join([f"\n❌ {len(errors)} {what}:", *(str(i) for i in errors)])
    return None


def output_paths(ctx: GenTopPlanContext, options: GenTopOptions) -> "tuple[Path, Optional[Path]]":
    """Where the top level goes, and the VHDL entity around it (``--mode vhdl``).

    ``--mode vhdl`` writes the Verilog top as ``<top>_core.v`` next to the
    VHDL entity ``<top>`` that instantiates it (vhdl_wrapper.py).
    """
    if options.mode == "vhdl":
        vhdl_output = resolve_path(options.output, ctx.c_root, Path(f"{options.top_name}.vhd"))
        return vhdl_output.with_name(f"{options.top_name}_core.v"), vhdl_output
    if options.mode == "verilog":
        return resolve_path(options.output, ctx.c_root, Path(f"{options.top_name}.v")), None
    return resolve_path(options.output, ctx.c_root, Path("block_design.tcl")), None


def planned_output_artifacts(ctx: GenTopPlanContext, options: GenTopOptions) -> List[str]:
    """The files generation writes for this plan, as ``--dry-run`` previews them."""
    out = resolve_path(
        options.output, ctx.c_root,
        Path(f"{options.top_name}.vhd") if options.mode == "vhdl"
        else Path(f"{options.top_name}.v") if options.mode == "verilog"
        else Path("block_design.tcl"),
    )
    artifacts = [str(out)]
    if options.mode in ("vhdl", "verilog"):
        for name in (
            "build_manifest.json", "port_map.yaml", "port_signature.json",
            "design_parameters.json", "probe_map.yaml", "tb_bindings.svh",
            "maturity_report.json",
        ):
            artifacts.append(str(out.parent / name))
        artifacts.append(str(out.parent / "design.ir.json"))
    elif options.mode == "bd":
        artifacts.append(str(out.parent / "design.ir.json"))
    return artifacts


@dataclass
class GeneratedTop:
    """What :func:`generate_top` wrote."""
    output: Path                  # the Verilog top or block-design Tcl
    vhdl_output: Optional[Path]   # the VHDL entity around it, --mode vhdl only
    top_name: str                 # the generated module (``<top>_core`` for --mode vhdl)
    report: Dict[str, Any]        # the structural generator's own report
    project: Any                  # the IR, with top-level ports and tie-offs attached


def generate_top(
    ctx: GenTopPlanContext,
    options: GenTopOptions,
    *,
    attach_verification: Optional[Callable[[Any, Path], None]] = None,
) -> GeneratedTop:
    """Write the top level of *ctx* and every artifact delivered with it.

    *attach_verification* resolves the design's verification plan onto the
    IR before it is recorded (verilog and vhdl modes); it is a callback
    because the verification contract is read by ``forge.verification``,
    which generation does not import. Raises :class:`GenerationRefused`
    when ``--strict`` finds ports neither connected nor allowed open.
    """
    cfg = ctx.cfg
    output, vhdl_output = output_paths(ctx, options)
    top_name = f"{options.top_name}_core" if vhdl_output is not None else options.top_name
    if options.mode == "bd":
        print(f"🔨 Generating Block Design TCL: {output}")
    else:
        print(f"🔨 Generating structural Verilog: {output}")

    # Generation is driven by the canonical IR rather than directly by
    # auto_match_ports's conn_map/global_nets; project_to_conn_map
    # reproduces the original iteration order via
    # ResolvedConnection.emission_order, so reg_stage_N/delay_N instance
    # naming and wire-declaration order are unchanged.
    project = assemble_project_ir(
        cfg,
        ctx.design_path,
        contracts=ctx.contracts or {},
        ip_info_data=ctx.ip_info,
        conn_map=ctx.conn_map,
        global_nets=ctx.global_nets,
        match_report=ctx.match_report,
        generated_from={
            "design": str(ctx.design_path),
            "contracts_from": (
                str(ctx.contracts_from_path)
                if ctx.contracts_from_path is not None and ctx.contracts_from_path.exists() else None
            ),
            "ip_info": str(ctx.ip_info_file) if ctx.ip_info_file.exists() else None,
            "build_dir": str(ctx.build_root),
        },
    )
    conn_map_ir, global_nets_ir = project_to_conn_map(project)

    if options.mode == "bd":
        report = write_bd_tcl(
            cfg=cfg,
            ip_info=ctx.ip_info,
            conn_map=conn_map_ir,
            global_nets=global_nets_ir,
            out_path=output,
            bd_name=options.bd_name,
            src_root=ctx.design_path.parent,
            ip_root=ctx.ip_root,
            system_yml=ctx.system_yml,
            contracts=ctx.contracts or {},
            match_report=ctx.match_report,
        )
        print(f"✓ Block Design TCL generated: {output}")
    else:
        report = write_structural_verilog(
            cfg=cfg,
            ip_info=ctx.ip_info,
            conn_map=conn_map_ir,
            global_nets=global_nets_ir,
            ip_root=ctx.ip_root,
            out_path=output,
            top_name=top_name,
            system_yml=ctx.system_yml,
            contracts=ctx.contracts,
            match_report=ctx.match_report,
        )
        print(f"✓ Verilog top generated: {output}")
    print_generation_report(report)

    # The generator's resolved top-level ports and tie-offs exist only now,
    # so the pre-generation IR (forge inspect's) carries neither.
    project.design.top_ports = [
        ResolvedTopLevelPort(
            name=p["name"], direction=p["direction"], width=p["width"],
            origin=p.get("origin"),
            instance_id=p.get("instance"),
            instance_port=p.get("port"),
        )
        for p in report.get("top_ports", [])
    ]
    project.design.connections.extend(build_tie_off_connections(report.get("tied_to_zero", [])))
    project.design.connections.sort(key=lambda c: c.id)

    if options.strict:
        refused = strict_port_failure(report, cfg)
        if refused:
            raise GenerationRefused(refused)

    # Before the manifest: the manifest records the IR's content hash, and
    # both of these are part of the IR's content. A block design has no
    # top module name; its own name stands in for it.
    project.design.top_module = options.bd_name if options.mode == "bd" else top_name
    if options.mode != "bd" and attach_verification is not None:
        attach_verification(project, output.parent)

    _write_artifacts(ctx, options, project, report, output)

    if vhdl_output is not None:
        write_vhdl_wrapper(
            report.get("top_ports", []),
            entity_name=options.top_name, core_name=top_name, out_path=vhdl_output,
        )
        print(f"✓ VHDL entity {options.top_name} around {top_name}: {vhdl_output}")

    return GeneratedTop(
        output=output, vhdl_output=vhdl_output, top_name=top_name, report=report, project=project,
    )


def _write_artifacts(
    ctx: GenTopPlanContext, options: GenTopOptions, project, report: Dict[str, Any], output: Path,
) -> None:
    """Every file written next to the top level, in the order the top-level
    port list, the IR and the provenance depend on each other."""
    cfg = ctx.cfg
    manifest_output = output.parent / "build_manifest.json"
    print(f"📦 Generating build manifest: {manifest_output}")
    generate_build_manifest(
        project=project,
        ip_info=ctx.ip_info,
        ip_root=ctx.ip_root,
        algo_top=output,
        design_file=ctx.design_path,
        manifest_output=manifest_output,
        project_root=ctx.c_root,
        hls_build_root=ctx.hls_build_root,
        auto_delays=ctx.auto_delays,
    )

    port_map_output = output.parent / "port_map.yaml"
    print(f"\n🗺  Generating port map: {port_map_output}")
    # The generator's own resolved port list, so a block-design Tcl file is
    # never parsed as Verilog and algo_top.v is not read back.
    top_ports = {p["name"]: (p["direction"], p["width"]) for p in report.get("top_ports", [])}
    port_map_data, port_sig_hash = generate_port_map(
        output, port_map_output, cfg.interface_metadata, ports=top_ports or None
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
    # A block design's Tcl is not Verilog, so its debug ports cannot be read
    # from it; the interface counts still come from the port map.
    write_design_parameters(
        cfg,
        params_output,
        algo_top_v_path=None if options.mode == "bd" else output,
        hls_metrics_file=resolve_path(options.hls_metrics, ctx.c_root),
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
            probe_map_data = yaml.safe_load(probe_map_output.read_text()) or {}
            validate_generated_contracts(
                port_map_data,
                probe_map_data,
                tb_bindings_output,
                params_output,
            )

    maturity_output = output.parent / "maturity_report.json"
    maturity = compute_maturity_summary(
        cfg, ctx.match_report, report, ir_content_hash=ir_content_hash_of(project),
    )
    maturity["port_signature_hash"] = port_sig_hash if port_map_data else None
    maturity_output.write_text(json.dumps(maturity, indent=2) + "\n")
    print(f"  ✓ Maturity report: {maturity_output}")

    ir_output = output.parent / "design.ir.json"
    ir_output.write_text(json.dumps(to_json_dict(project), indent=2, sort_keys=True))
    print(f"  ✓ Canonical IR snapshot: {ir_output}")
    write_generation_provenance(project, ctx, options, ir_output)


def command_options(options: GenTopOptions) -> Dict[str, Any]:
    """The options that affect generation output, recorded in the
    provenance manifest so ``--explain-staleness`` can tell "you changed
    how you called it" apart from "an input file changed"."""
    return {
        "mode": options.mode,
        "top_name": options.top_name,
        "strict": options.strict,
        "system": str(options.system) if options.system is not None else None,
        "contracts_from": str(options.contracts_from) if options.contracts_from is not None else None,
    }


def write_generation_provenance(
    project, ctx: GenTopPlanContext, options: GenTopOptions, ir_output: Path,
) -> Path:
    """Write ``provenance.json`` next to ``design.ir.json``.

    ``plan_hash`` is computed from ``ctx.project`` (the pre-generation IR)
    with the same ``build_generation_plan``/``plan_hash`` call ``forge build``
    makes, so a hash pinned with ``forge build --accept-plan-hash`` compares
    directly with the one recorded here. ``build_provenance`` hashes only
    the planned outputs that exist.
    """
    from forge.ir.plan import build_generation_plan, plan_hash as _plan_hash_of
    from forge.ir.provenance import build_provenance, write_provenance

    planned = planned_output_artifacts(ctx, options)
    plan = build_generation_plan(
        ctx.project, ctx.match_report,
        topology_group_issues=ctx.topology_group_issues,
        cardinality_issues=ctx.cardinality_issues,
        cdc_issues=ctx.cdc_issues,
        compat_mode_modules=ctx.match_report.compat_mode_modules,
        output_artifacts=planned,
    )
    manifest = build_provenance(
        project,
        command_options=command_options(options),
        plan_hash=_plan_hash_of(plan),
        output_paths=planned,
        project_identity=ctx.c_root.name or None,
    )
    provenance_output = ir_output.parent / "provenance.json"
    write_provenance(provenance_output, manifest)
    print(f"  ✓ Provenance manifest: {provenance_output}")
    return provenance_output


def print_generation_report(report: dict) -> None:
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


def strict_port_failure(report: dict, cfg) -> Optional[str]:
    """The ``--strict`` report of ports neither connected nor allowed open, or ``None``."""
    unaccounted_open = filter_unaccounted(
        report.get("open_outputs", []), cfg.allowed_unconnected.open_outputs
    )
    unaccounted_tied = filter_unaccounted(
        report.get("tied_to_zero", []), cfg.allowed_unconnected.tied_inputs
    )
    if not unaccounted_open and not unaccounted_tied:
        return None
    lines: List[str] = []
    for found, what in ((unaccounted_open, "open output"), (unaccounted_tied, "tied input")):
        if not found:
            continue
        lines.append(f"❌ Strict mode: {len(found)} unaccounted {what}(s):")
        lines.extend(f"   • {inst}.{port} [{w} bits]" for inst, port, w in sorted(found)[:20])
        if len(found) > 20:
            lines.append(f"   … and {len(found) - 20} more")
    lines.append("   Add patterns to allowed_unconnected in design.yml or remove --strict.")
    return "\n".join(lines)
