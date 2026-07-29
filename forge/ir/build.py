"""
Build a ``ResolvedProject`` from the existing (pre-IR) loaders.

This is migration steps 1-3 of the canonical-IR plan (topology/config
loading, interface-contract loading, IP/RTL port metadata) plus read-only
consumption of the existing matcher's output for connections. It does not
reimplement matching, generation, latency analysis, or verification — it
only *reads* what those subsystems' existing loaders already produce.

Read-only by construction: this module never writes ``ip_info.yaml`` (or
any other file) to disk, mirroring the ``--dry-run`` invariant fixed in
``forge/core/cli/groups/topgen.py``. When no ``ip_info.yaml`` exists yet and
no contracts are given, IP metadata is collected in memory only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from forge import __version__ as _forge_version
from ..topgen.config import DesignConfig, Module
from ..topgen.validation import validate_design
from ..topgen.ip.contract_loader import (
    LoadedContract,
    load_contracts_for_design,
    synthesize_ip_info,
)
from ..topgen.ip.contract_verifier import load_canonical_role_vocab
from ..topgen.ip.cardinality import Bound, CardinalityError, parse_cardinality
from ..topgen.ip.coordinates import coordinate_key
from ..topgen.ip.domains import (
    CLOCK_HEURISTIC_NAMES,
    RESET_HEURISTIC_NAMES,
    resolve_domain_nets,
)
from ..topgen.ip.matcher import _expand_nd, auto_match_ports, load_ip_info
from ..topgen.ip.parser import collect_all

from .model import (
    CardinalityCheckResult,
    DiagnosticReference,
    MatchingEvidence,
    RejectedCandidate,
    ResolvedClockDomain,
    ResolvedConnection,
    ResolvedDesign,
    ResolvedEndpoint,
    ResolvedInstance,
    ResolvedInterfaceMember,
    ResolvedLogicalInterface,
    ResolvedModuleDefinition,
    ResolvedPhysicalBinding,
    ResolvedProject,
    ResolvedResetDomain,
    ResolvedTransformation,
    SourceLocation,
)

_EXTERNAL = "$external"
_TIE_OFF = "$tie_off"


def _instance_id(mod: Module, idx: int) -> str:
    """Same convention as ``forge.topgen.ip.matcher._inst`` — instance IDs
    here must match ``conn_map`` keys for connections to resolve correctly."""
    return mod.name if mod.instances == 1 else f"{mod.name}_{idx}"


def build_tie_off_connections(
    tied_to_zero: List[Tuple[str, str, int]],
) -> List[ResolvedConnection]:
    """Build synthetic ``tie_off`` connections from a generator's
    ``report["tied_to_zero"]`` list (``[(instance, port, width), ...]`` —
    ``write_structural_verilog``/``write_structural_vhdl``, release-plan
    §3.3).

    Mirrors the existing ``"$external"`` sentinel convention: a tied port
    has no real producer in ``conn_map`` at all (there's nothing driving
    it), so it gets a synthetic ``"$tie_off"`` producer instead. Only
    knowable *after* generation runs — callers (``cmd_gen_top``) attach
    the result to an already-built ``ResolvedProject`` alongside
    ``top_ports``, the same timing/asymmetry ``ResolvedTopLevelPort``
    already has (absent from ``forge inspect``'s pre-generation IR).
    """
    connections: List[ResolvedConnection] = []
    for order, (inst, port, _width) in enumerate(tied_to_zero):
        connections.append(ResolvedConnection(
            id=f"tie_off:{inst}.{port}",
            producer=ResolvedEndpoint(instance_id=_TIE_OFF, port=None),
            consumer=ResolvedEndpoint(instance_id=inst, port=port),
            transformations=[ResolvedTransformation(
                id=f"xform:tie_off:{inst}.{port}", kind="tie_off",
            )],
            emission_order=order,
        ))
    return connections


def _as_path(value: Optional[Path | str]) -> Optional[Path]:
    return Path(value).expanduser().resolve() if value is not None else None


def _resolve_ip_info(
    cfg: DesignConfig,
    *,
    ip_info_path: Optional[Path],
    contracts: Optional[Dict[str, LoadedContract]],
    build_dir: Optional[Path],
    ip_root: Optional[Path],
    src_root: Optional[Path],
) -> Dict[str, Any]:
    """Resolve ip_info the same dry-run-safe way as
    ``topgen gen-top --dry-run`` — never writes ``ip_info.yaml``.
    ``forge inspect`` is read-only by default.
    """
    if ip_info_path is not None and ip_info_path.exists():
        return load_ip_info(ip_info_path)
    if contracts:
        mapped: Dict[str, LoadedContract] = {}
        for m in cfg.modules:
            c = contracts.get(m.name) or (m.ip_info_key and contracts.get(m.ip_info_key))
            if c:
                mapped[m.name] = c
        return synthesize_ip_info(mapped)
    if build_dir is not None:
        return collect_all(build_root=build_dir, modules=cfg.modules, ip_root=ip_root, src_root=src_root)
    return {}


def _cardinality_dict(primary_role: Dict[str, Any], direction: str) -> Optional[Dict[str, Any]]:
    """Resolve a group's `member: data` role's `cardinality:` block (if
    any) into a plain JSON-serializable dict for the IR. Malformed blocks
    are already reported by ``ContractVerifier`` — the IR just omits them
    here rather than raising, so ``forge inspect`` stays usable even when
    a contract has an unrelated verification error elsewhere."""
    try:
        resolved = parse_cardinality(primary_role, direction=direction)
    except CardinalityError:
        return None
    if resolved is None:
        return None
    out: Dict[str, Any] = {}
    if resolved.producers is not None:
        out["producers"] = {"min": resolved.producers.min, "max": resolved.producers.max}
    if resolved.consumers is not None:
        out["consumers"] = {"min": resolved.consumers.min, "max": resolved.consumers.max}
    return out


def _expand_template_ports(tpl: str, dims: Any) -> List[str]:
    """Concrete physical port names covered by an ``nd_tpl`` binding.
    Reuses ``matcher._expand_nd`` (already imported cross-module by
    ``topology_deriver.py``) with the same template on both sides — for a
    single-sided expansion, one side of each resulting pair is what's
    needed."""
    if isinstance(dims, int):
        dims = [dims]
    return [s for s, _ in _expand_nd(tpl, tpl, dims=list(dims))]


def _index_interface_ports(
    interfaces: List[ResolvedLogicalInterface],
) -> Dict[str, Tuple[ResolvedLogicalInterface, ResolvedInterfaceMember]]:
    """Physical port name -> (owning interface, member), for every member
    binding on *interfaces*. Release-plan §3.5: this is what lets matching
    evidence (coordinates/protocol/wiring_kind) be attached per connection
    without any new matcher plumbing — it's a reverse index over data the
    IR already resolved in ``_build_interfaces``."""
    index: Dict[str, Tuple[ResolvedLogicalInterface, ResolvedInterfaceMember]] = {}
    for iface in interfaces:
        for member in iface.members:
            b = member.binding
            if b.kind == "scalar" and b.raw_port:
                index[b.raw_port] = (iface, member)
            elif b.kind == "prefix_array" and b.raw_port_prefix and b.count:
                for i in range(b.count):
                    index[f"{b.raw_port_prefix}{i}"] = (iface, member)
            elif b.kind == "nd_tpl" and b.raw_port_tpl and b.dims:
                for name in _expand_template_ports(b.raw_port_tpl, b.dims):
                    index[name] = (iface, member)
    return index


def _build_matching_evidence(
    *,
    src_mod: Optional[str], dst_mod: Optional[str],
    src_inst: str, src_port: str, dst_inst: str, dst_port: str,
    port_index_by_module: Dict[str, Dict[str, Tuple[ResolvedLogicalInterface, ResolvedInterfaceMember]]],
    ip_ports_by_module: Dict[str, Dict[str, Dict[str, Any]]],
    producer_count_by_sink: Dict[Tuple[str, str], int],
    consumer_count_by_source: Dict[Tuple[str, str], int],
    gather_scatter_pattern: Optional[str],
    cdc_declared: Optional[str],
    rejected_candidates: List[RejectedCandidate],
) -> Optional[MatchingEvidence]:
    """Release-plan §3.5 per-connection evidence. Returns ``None`` only
    when literally nothing is known about either pin (e.g. both sides are
    ``$external``/unresolved) — a connection to a plain handshake pin still
    gets a ``MatchingEvidence`` with only ``producer_width``/``consumer_width``
    populated, which is itself real evidence (no ``width_adapter`` exists,
    so a width mismatch here would otherwise be silent)."""
    src_entry = port_index_by_module.get(src_mod, {}).get(src_port)
    dst_entry = port_index_by_module.get(dst_mod, {}).get(dst_port)
    src_width = ip_ports_by_module.get(src_mod, {}).get(src_port, {}).get("width")
    dst_width = ip_ports_by_module.get(dst_mod, {}).get(dst_port, {}).get("width")

    if (src_entry is None and dst_entry is None and src_width is None
            and dst_width is None and gather_scatter_pattern is None
            and cdc_declared is None and not rejected_candidates):
        return None

    producer_cardinality: Optional[CardinalityCheckResult] = None
    if src_entry is not None:
        bound = (src_entry[0].cardinality or {}).get("consumers")
        if bound is not None:
            actual = consumer_count_by_source.get((src_inst, src_port), 0)
            producer_cardinality = CardinalityCheckResult(
                bound=dict(bound), actual_count=actual,
                satisfied=Bound(min=bound["min"], max=bound["max"]).satisfied(actual),
            )

    consumer_cardinality: Optional[CardinalityCheckResult] = None
    if dst_entry is not None:
        bound = (dst_entry[0].cardinality or {}).get("producers")
        if bound is not None:
            actual = producer_count_by_sink.get((dst_inst, dst_port), 0)
            consumer_cardinality = CardinalityCheckResult(
                bound=dict(bound), actual_count=actual,
                satisfied=Bound(min=bound["min"], max=bound["max"]).satisfied(actual),
            )

    return MatchingEvidence(
        producer_wiring_kind=src_entry[0].wiring_kind if src_entry else None,
        consumer_wiring_kind=dst_entry[0].wiring_kind if dst_entry else None,
        producer_coordinates=src_entry[0].coordinates if src_entry else None,
        consumer_coordinates=dst_entry[0].coordinates if dst_entry else None,
        producer_protocol=src_entry[0].protocol if src_entry else None,
        consumer_protocol=dst_entry[0].protocol if dst_entry else None,
        producer_width=src_width,
        consumer_width=dst_width,
        producer_cardinality=producer_cardinality,
        consumer_cardinality=consumer_cardinality,
        gather_scatter_pattern=gather_scatter_pattern,
        cdc_declared=cdc_declared,
        rejected_candidates=rejected_candidates,
    )


def _build_interfaces(contract: LoadedContract, vocab: Dict[str, Any], module_name: str,
                       diagnostics: List[DiagnosticReference]) -> List[ResolvedLogicalInterface]:
    """Group contract roles into logical interfaces.

    Phase 2.5: roles that declare a shared ``interface:`` name (e.g. a
    ``data``/``valid``/``ready`` triple) are merged into one
    ``ResolvedLogicalInterface`` with multiple ``ResolvedInterfaceMember``
    entries. Roles that don't declare ``interface:`` keep today's 1:1
    mapping — this is purely additive, existing contracts are unaffected.
    """
    all_roles: List[Dict[str, Any]] = []
    for direction in ("input", "output"):
        for role in contract.get_connection_roles(direction, require_wiring_kind=False):
            all_roles.append(role)

            role_name = role["role_name"]
            canonical = vocab.get(role_name)
            if canonical and canonical.get("status") == "reserved":
                diagnostics.append(DiagnosticReference(
                    severity="warning",
                    message=(
                        f"role '{role_name}' is RESERVED — declared for a future "
                        "release and has no functional effect in this version of "
                        "FORGE (no clock-domain model, no CDC validation, no "
                        "special wiring). See docs/IP_INTERFACE_POLICY.md "
                        "\"Reserved roles\"."
                    ),
                    object_id=f"module:{module_name}#interface:{role_name}",
                ))

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for role in all_roles:
        groups.setdefault(role["interface"], []).append(role)

    interfaces: List[ResolvedLogicalInterface] = []
    for group_name, roles in groups.items():
        primary = next((r for r in roles if r["member"] == "data"), roles[0])
        group_direction = primary["direction"]

        members: List[ResolvedInterfaceMember] = []
        for role in roles:
            binding = ResolvedPhysicalBinding(
                kind=role["kind"],
                raw_port=role.get("raw_port"),
                raw_port_prefix=role.get("raw_port_prefix"),
                raw_port_tpl=role.get("raw_port_tpl"),
                count=role.get("count"),
                dims=role.get("dims"),
                width=role.get("width"),
            )
            members.append(ResolvedInterfaceMember(
                name=role["member"],
                binding=binding,
                direction=role["direction"] if role["direction"] != group_direction else None,
            ))

        ck = coordinate_key(primary)
        interfaces.append(ResolvedLogicalInterface(
            name=group_name,
            direction=group_direction,
            wiring_kind=primary.get("wiring_kind"),
            coordinates=dict(ck) if ck else None,
            protocol=primary.get("protocol"),
            cardinality=_cardinality_dict(primary, group_direction),
            members=members,
        ))
    interfaces.sort(key=lambda i: i.name)
    return interfaces


def _build_project_ir_full(
    design_path: Path | str,
    *,
    contracts_from: Optional[Path | str] = None,
    ip_info: Optional[Path | str] = None,
    build_dir: Optional[Path | str] = None,
    ip_root: Optional[Path | str] = None,
    src_root: Optional[Path | str] = None,
) -> Tuple[ResolvedProject, DesignConfig, Any]:
    """Shared body of :func:`build_project_ir` and
    :func:`build_project_ir_with_match_report` — loads everything from
    scratch and returns ``(project, cfg, match_report)`` so callers needing
    only *project* (the common case) and callers also needing the matcher's
    own ``cfg``/``match_report`` (e.g. a contract-maturity summary, without
    re-running the matcher a second time) can share one implementation.
    """
    design_path = Path(design_path).expanduser().resolve()
    contracts_from_p = _as_path(contracts_from)
    ip_info_p = _as_path(ip_info)
    build_dir_p = _as_path(build_dir)
    ip_root_p = _as_path(ip_root)
    src_root_p = _as_path(src_root) if src_root is not None else design_path.parent

    cfg = DesignConfig.load_relaxed(design_path)

    contracts: Dict[str, LoadedContract] = {}
    if contracts_from_p and contracts_from_p.exists():
        contracts = load_contracts_for_design(contracts_from_p, design_path.parent)

    ip_info_data = _resolve_ip_info(
        cfg,
        ip_info_path=ip_info_p,
        contracts=contracts,
        build_dir=build_dir_p,
        ip_root=ip_root_p,
        src_root=src_root_p,
    )

    conn_map, global_nets, match_report = auto_match_ports(
        cfg, ip_info_data, contracts=contracts or None,
    )

    project = assemble_project_ir(
        cfg,
        design_path,
        contracts=contracts,
        ip_info_data=ip_info_data,
        conn_map=conn_map,
        global_nets=global_nets,
        match_report=match_report,
        generated_from={
            "design": str(design_path),
            "contracts_from": str(contracts_from_p) if contracts_from_p else None,
            "ip_info": str(ip_info_p) if ip_info_p else None,
            "build_dir": str(build_dir_p) if build_dir_p else None,
        },
    )
    return project, cfg, match_report


def build_project_ir(
    design_path: Path | str,
    *,
    contracts_from: Optional[Path | str] = None,
    ip_info: Optional[Path | str] = None,
    build_dir: Optional[Path | str] = None,
    ip_root: Optional[Path | str] = None,
    src_root: Optional[Path | str] = None,
) -> ResolvedProject:
    """Build a ``ResolvedProject`` for the design at *design_path*, loading
    everything (config, contracts, ip_info) from scratch.

    Never writes any file — safe to call from a read-only command. Used by
    ``forge inspect``, which has no other reason to have these objects
    already in hand. A caller that *already* has ``cfg``/``contracts``/
    ``ip_info``/matcher output from its own pipeline (e.g. ``topgen
    gen-top`` — migration step 4 of the canonical-IR plan, see
    ``docs/development/release-readiness.md``) should call
    ``assemble_project_ir`` directly instead of redundantly reloading
    everything here.
    """
    project, _cfg, _match_report = _build_project_ir_full(
        design_path,
        contracts_from=contracts_from,
        ip_info=ip_info,
        build_dir=build_dir,
        ip_root=ip_root,
        src_root=src_root,
    )
    return project


def build_project_ir_with_match_report(
    design_path: Path | str,
    *,
    contracts_from: Optional[Path | str] = None,
    ip_info: Optional[Path | str] = None,
    build_dir: Optional[Path | str] = None,
    ip_root: Optional[Path | str] = None,
    src_root: Optional[Path | str] = None,
) -> Tuple[ResolvedProject, DesignConfig, Any]:
    """Same as :func:`build_project_ir`, but also returns the ``cfg``/
    ``match_report`` the matcher produced along the way — for callers that
    need pre-generation topology data (e.g. ``forge inspect``'s contract-
    maturity summary, release-plan Phase 6 §6.1) without loading the
    design and re-running the matcher a second time.
    """
    return _build_project_ir_full(
        design_path,
        contracts_from=contracts_from,
        ip_info=ip_info,
        build_dir=build_dir,
        ip_root=ip_root,
        src_root=src_root,
    )


def _domain_diagnostics(unresolved: List[Tuple[str, str]]) -> List[DiagnosticReference]:
    """Wrap resolve_domain_nets's IR-agnostic (module, kind) pairs into
    DiagnosticReference objects — kept here (not in domains.py) so that
    module stays free of any IR/model.py dependency."""
    names = {"clock": CLOCK_HEURISTIC_NAMES, "reset": RESET_HEURISTIC_NAMES}
    out = []
    for mod_name, kind in unresolved:
        out.append(DiagnosticReference(
            severity="warning",
            message=(
                f"module '{mod_name}': no {kind} domain could be resolved "
                f"— no interface contract and no heuristic {kind} port "
                f"({', '.join(names[kind])}) found."
            ),
            object_id=f"module:{mod_name}",
        ))
    return out


def assemble_project_ir(
    cfg: DesignConfig,
    design_path: Path | str,
    *,
    contracts: Dict[str, LoadedContract],
    ip_info_data: Dict[str, Any],
    conn_map: Dict[Any, Any],
    global_nets: Dict[str, Any],
    match_report: Any,
    generated_from: Optional[Dict[str, Optional[str]]] = None,
) -> ResolvedProject:
    """Assemble a ``ResolvedProject`` from already-resolved facts.

    This is the shared core used both by ``build_project_ir`` (fresh
    reload — ``forge inspect``) and directly by callers that already have
    ``cfg``/``contracts``/``ip_info``/matcher output from their own
    pipeline (``topgen gen-top`` — migration step 4: generation and the IR
    now share one matching/config computation instead of two independent
    ones). Never writes any file.
    """
    design_path = Path(design_path).expanduser().resolve()

    diagnostics: List[DiagnosticReference] = []

    validator = validate_design(cfg, design_path)
    for issue in (*validator.errors, *validator.warnings, *validator.infos):
        diagnostics.append(DiagnosticReference(
            severity=issue.severity,
            message=(
                f"[{issue.category}] {issue.message}"
                + (f" (suggestion: {issue.suggestion})" if issue.suggestion else "")
            ),
            location=SourceLocation(file=str(design_path)) if issue.location else None,
            object_id=issue.location,
        ))

    vocab = load_canonical_role_vocab()

    # Release-plan §3.5: reverse indexes used later to attach per-connection
    # matching evidence (coordinates/protocol/wiring_kind/width) without any
    # new matcher plumbing — built once per module here, alongside the data
    # they're derived from, rather than re-scanned per connection.
    port_index_by_module: Dict[str, Dict[str, Tuple[ResolvedLogicalInterface, ResolvedInterfaceMember]]] = {}
    ip_ports_by_module: Dict[str, Dict[str, Dict[str, Any]]] = {}

    modules: List[ResolvedModuleDefinition] = []
    for mod in cfg.modules:
        ip_key = mod.ip_info_key or mod.name
        contract = contracts.get(mod.name) or contracts.get(ip_key)
        ip_entry = ip_info_data.get(mod.name) or ip_info_data.get(ip_key)
        ports_resolved = ip_entry is not None

        interfaces: List[ResolvedLogicalInterface] = []
        if contract is not None:
            interfaces = _build_interfaces(contract, vocab, mod.name, diagnostics)
        elif not ports_resolved:
            diagnostics.append(DiagnosticReference(
                severity="warning",
                message=(
                    f"module '{mod.name}': no interface contract and no resolvable "
                    "IP/RTL port metadata (e.g. missing HLS build artifacts) — "
                    "interfaces could not be resolved for the canonical IR."
                ),
                object_id=f"module:{mod.name}",
            ))
        port_index_by_module[mod.name] = _index_interface_ports(interfaces)
        ip_ports_by_module[mod.name] = (
            {p["name"]: p for p in ip_entry["ports"]} if ip_entry else {}
        )

        timing = mod.timing
        modules.append(ResolvedModuleDefinition(
            name=mod.name,
            kind=mod.kind,
            top=mod.top,
            source_files=list(mod.src),
            contract_path=str(contract.path) if contract is not None else None,
            ports_resolved=ports_resolved,
            interfaces=interfaces,
            parameters=dict(mod.parameters),
            latency_cycles=timing.latency_cycles if timing else None,
            latency_hint=timing.latency_hint if timing else None,
            is_variable_latency=timing.variable_latency if timing else False,
            latency=timing.latency if timing else None,
            ip_info_key=mod.ip_info_key,
        ))
    modules.sort(key=lambda m: m.name)

    instances: List[ResolvedInstance] = []
    for mod in cfg.modules:
        for idx in range(max(1, mod.instances)):
            instances.append(ResolvedInstance(
                id=_instance_id(mod, idx),
                module=mod.name,
                index=idx if mod.instances > 1 else None,
            ))
    instances.sort(key=lambda i: i.id)
    instance_ids = [i.id for i in instances]
    mod_of_instance = {i.id: i.module for i in instances}

    # Phase 3.1: resolve each instance's clock/reset domain from the net
    # actually wired to it (or None for clock-free/reset-free modules) —
    # see forge.topgen.ip.domains.resolve_domain_nets's docstring. This
    # must run before clock_domains/reset_domains below, which are
    # *derived* from these per-instance values, not populated
    # independently.
    clock_of_module, reset_of_module, unresolved_domains = resolve_domain_nets(
        cfg, contracts, match_report, global_nets, mod_of_instance,
    )
    diagnostics.extend(_domain_diagnostics(unresolved_domains))
    for inst in instances:
        inst.clock_domain = clock_of_module.get(inst.module)
        inst.reset_domain = reset_of_module.get(inst.module)

    # Per-(from-module, to-module) transformations declared on design.yml
    # connections (release-plan §3.3). Coarse: applied to every expanded
    # instance pair between those two modules — matches the generator's
    # own module-pair-level register_stages/delay_cycles/boundary/cdc maps
    # (forge/topgen/generators/structural_verilog.py).
    module_transforms: Dict[tuple, List[ResolvedTransformation]] = {}
    # Release-plan §3.5: the connection's *declared* CDC kind (if any),
    # keyed the same way as module_transforms — separate from
    # crosses_clock_domain/crosses_reset_domain (a resolved fact about the
    # two endpoints' domains) and separate from whether a cdc_synchronizer
    # transformation was actually generated (module_transforms above).
    cdc_declared_by_pair: Dict[tuple, Optional[str]] = {}
    for c in cfg.connections:
        xforms: List[ResolvedTransformation] = []
        if c.cdc:
            cdc_declared_by_pair[(c.from_, c.to)] = c.cdc.get("kind")
        if c.register_stages:
            xforms.append(ResolvedTransformation(
                id=f"xform:{c.from_}->{c.to}:pipeline_register",
                kind="pipeline_register", cycles=c.register_stages,
            ))
        if c.delay_cycles:
            if c.boundary:
                # One generated RTL instance (slr_crossing_delay, not
                # signal_delay) for this case — one transformation, not
                # both latency_delay and slr_crossing.
                xforms.append(ResolvedTransformation(
                    id=f"xform:{c.from_}->{c.to}:slr_crossing",
                    kind="slr_crossing", cycles=c.delay_cycles, tag=c.boundary,
                ))
            else:
                xforms.append(ResolvedTransformation(
                    id=f"xform:{c.from_}->{c.to}:latency_delay",
                    kind="latency_delay", cycles=c.delay_cycles,
                ))
        if c.cdc:
            cdc_kind = "cdc_synchronizer" if c.cdc.get("kind") == "2ff_sync" else "async_fifo"
            xforms.append(ResolvedTransformation(
                id=f"xform:{c.from_}->{c.to}:{cdc_kind}",
                kind=cdc_kind,
            ))
        if xforms:
            module_transforms.setdefault((c.from_, c.to), []).extend(xforms)

    # emission_order is assigned in the exact order connections are produced
    # below (conn_map, then global_nets) — the same order the legacy
    # generators iterate in. The list is later sorted by `id` for stable
    # hashing/diffing/visualization; emission_order travels with each
    # object so `forge.ir.project.project_to_conn_map` can still reproduce
    # the original iteration order (migration step 5).
    _emission_counter = 0

    # Release-plan §3.5: precomputed once (not per-connection) using the
    # exact same counting rule forge.topgen.ip.cardinality.verify_cardinality
    # already applies — a sink's producer count includes both the one
    # candidate that won (connection_evidence) and every one that lost the
    # first-driver-wins guard (rejected_fanin); a source's consumer count
    # is just how many connection_evidence entries it drives.
    producer_count_by_sink: Dict[Tuple[str, str], int] = {}
    consumer_count_by_source: Dict[Tuple[str, str], int] = {}
    for (_src_i, _s_pin, _dst_i, _d_pin) in match_report.connection_evidence:
        sink = (_dst_i, _d_pin)
        producer_count_by_sink[sink] = producer_count_by_sink.get(sink, 0) + 1
        source = (_src_i, _s_pin)
        consumer_count_by_source[source] = consumer_count_by_source.get(source, 0) + 1
    for _sink, _losers in match_report.rejected_fanin.items():
        producer_count_by_sink[_sink] = producer_count_by_sink.get(_sink, 0) + len(_losers)

    connections: List[ResolvedConnection] = []
    for (src_inst, dst_inst), pairs in conn_map.items():
        src_mod, dst_mod = mod_of_instance.get(src_inst), mod_of_instance.get(dst_inst)
        xforms = module_transforms.get((src_mod, dst_mod), [])
        # Phase 3.2: descriptive only — does the connection's two module
        # instances resolve to different, both-known domains? Enforcement
        # (rejecting an undeclared crossing under --strict) is
        # forge.topgen.ip.cdc.verify_cdc's job, not this builder's.
        src_clock, dst_clock = clock_of_module.get(src_mod), clock_of_module.get(dst_mod)
        crosses_clock = src_clock is not None and dst_clock is not None and src_clock != dst_clock
        src_reset, dst_reset = reset_of_module.get(src_mod), reset_of_module.get(dst_mod)
        crosses_reset = src_reset is not None and dst_reset is not None and src_reset != dst_reset
        cdc_declared = cdc_declared_by_pair.get((src_mod, dst_mod))
        for src_port, dst_port in pairs:
            key = (src_inst, src_port, dst_inst, dst_port)
            wiring_method = match_report.connection_evidence.get(key)
            gather_scatter_pattern = match_report.gather_scatter_evidence.get(key)
            rejected_candidates = [
                RejectedCandidate(producer=ResolvedEndpoint(instance_id=r_inst, port=r_port))
                for r_inst, r_port in match_report.rejected_fanin.get((dst_inst, dst_port), [])
            ]
            matching_evidence = _build_matching_evidence(
                src_mod=src_mod, dst_mod=dst_mod,
                src_inst=src_inst, src_port=src_port,
                dst_inst=dst_inst, dst_port=dst_port,
                port_index_by_module=port_index_by_module,
                ip_ports_by_module=ip_ports_by_module,
                producer_count_by_sink=producer_count_by_sink,
                consumer_count_by_source=consumer_count_by_source,
                gather_scatter_pattern=gather_scatter_pattern,
                cdc_declared=cdc_declared,
                rejected_candidates=rejected_candidates,
            )
            conn_xforms = list(xforms)
            if gather_scatter_pattern is not None:
                conn_xforms.append(ResolvedTransformation(
                    id=f"xform:{src_inst}.{src_port}->{dst_inst}.{dst_port}:gather_scatter",
                    kind="gather_scatter", tag=gather_scatter_pattern,
                ))
            connections.append(ResolvedConnection(
                id=f"{src_inst}.{src_port}->{dst_inst}.{dst_port}",
                producer=ResolvedEndpoint(instance_id=src_inst, port=src_port),
                consumer=ResolvedEndpoint(instance_id=dst_inst, port=dst_port),
                wiring_method=wiring_method,
                transformations=conn_xforms,
                emission_order=_emission_counter,
                crosses_clock_domain=crosses_clock,
                crosses_reset_domain=crosses_reset,
                matching_evidence=matching_evidence,
            ))
            _emission_counter += 1

    # Global nets (clock/reset/config fan-out from outside the design) are
    # represented as connections from a synthetic "$external" producer to
    # each fanned-out (instance, port).
    contract_wired = {
        (mod_name, port) for mod_name, _role, port in match_report.contract_wired_roles
    }
    for sig, binds in global_nets.items():
        for inst, port in binds:
            mod_name = mod_of_instance.get(inst)
            if (mod_name, port) in contract_wired:
                wiring_method = "contract_wiring"
            elif mod_name in match_report.compat_mode_modules:
                wiring_method = "heuristic"
            else:
                wiring_method = None
            connections.append(ResolvedConnection(
                id=f"external:{sig}->{inst}.{port}",
                producer=ResolvedEndpoint(instance_id=_EXTERNAL, interface_name=sig, port=sig),
                consumer=ResolvedEndpoint(instance_id=inst, port=port),
                wiring_method=wiring_method,
                emission_order=_emission_counter,
            ))
            _emission_counter += 1

    # Fan-out (release-plan §3.3): a producer pin driving more than one
    # connection gets a `fanout` transformation on each of those
    # connections. Computed generically from the connections already
    # built above, no new schema. Excludes "$external" producers (clock/
    # reset global-net fan-out is universal — flagging it would make
    # every design "all fanout," not a meaningful signal).
    producer_counts: Dict[Tuple[str, str], int] = {}
    for conn in connections:
        if conn.producer.instance_id != _EXTERNAL:
            key = (conn.producer.instance_id, conn.producer.port)
            producer_counts[key] = producer_counts.get(key, 0) + 1
    for conn in connections:
        if conn.producer.instance_id == _EXTERNAL:
            continue
        key = (conn.producer.instance_id, conn.producer.port)
        if producer_counts.get(key, 0) > 1:
            conn.transformations.append(ResolvedTransformation(
                id=f"xform:{conn.id}:fanout", kind="fanout",
            ))

    connections.sort(key=lambda c: c.id)

    for warning in match_report.warnings:
        diagnostics.append(DiagnosticReference(severity="warning", message=warning))

    # Phase 3.1: derive the domain-list grouping views from each instance's
    # already-resolved clock_domain/reset_domain scalar (the source of
    # truth) — instances with no resolved domain (clock-free/reset-free,
    # or unresolved) correctly don't appear in any domain here.
    def _grouped_domains(attr: str) -> Dict[str, List[str]]:
        groups: Dict[str, List[str]] = {}
        for inst in instances:
            name = getattr(inst, attr)
            if name is not None:
                groups.setdefault(name, []).append(inst.id)
        return groups

    # Phase 3.2: attach the design's optional, purely descriptive
    # clock_domains:/reset_domains: relationship declarations, matched by
    # the already-resolved domain (net) name.
    clock_domains = [
        ResolvedClockDomain(
            name=name, instances=members,
            derived_from=cfg.clock_domains.get(name, {}).get("derived_from"),
            ratio=cfg.clock_domains.get(name, {}).get("ratio"),
        )
        for name, members in sorted(_grouped_domains("clock_domain").items())
    ]
    reset_domains = [
        ResolvedResetDomain(
            name=name, instances=members,
            derived_from=cfg.reset_domains.get(name, {}).get("derived_from"),
            ratio=cfg.reset_domains.get(name, {}).get("ratio"),
        )
        for name, members in sorted(_grouped_domains("reset_domain").items())
    ]

    design = ResolvedDesign(
        name=design_path.stem,
        modules=modules,
        instances=instances,
        connections=connections,
        clock_domains=clock_domains,
        reset_domains=reset_domains,
        diagnostics=diagnostics,
        source=SourceLocation(file=str(design_path)),
    )

    return ResolvedProject(
        design=design,
        forge_version=_forge_version,
        generated_from=dict(generated_from) if generated_from else {"design": str(design_path)},
    )
