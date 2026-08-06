"""forge.analyze.latency_static.graph
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Build a directed latency graph from the canonical resolved design IR,
falling back to an independent design.yml/modules.yml re-parse only for
the narrow,
undocumented case of a `modules_yml_path` override that genuinely differs
from design.yml's own `registry:` field (never observed in real/documented
usage, but the parameter's public contract allows it).

Each node represents one physical module instance (single-instance modules
keep their bare module name, e.g.
``"col"``; a multi-instance module's instances are named ``"dec[0]"``,
``"dec[1]"``, ... — previously, all instances of a module
collapsed into one node, which meant a real multi-producer fan-in (e.g.
trigger_demo's ``dec`` x4 -> ``col`` gather) could never register as a
"merge point" at all). Each node's latency is the number of clock cycles
from valid-in to valid-out for a single pipeline pass, wrapped in a
provenance-tagged :class:`~forge.analyze.latency_model.LatencyValue` —
identical across every instance
of the same module, since latency is a module-level (HLS-synthesis-level)
fact, not something that varies per physical instance.

Edges between a producer module with N instances and a consumer module
with exactly one instance (or vice versa) are expanded into N real
per-instance edges — structurally certain regardless of which physical
port each instance drives, since there is only one possible destination
(or source) instance to connect to. When *both* sides have more than one
instance, the exact per-instance pairing is genuinely ambiguous without
consuming contract/port-matching data this module deliberately doesn't
depend on (see the "usable before synthesis" note below) — that case
(not present in either reference design today) conservatively expands to
the full producer x consumer Cartesian product rather than guessing a
single pairing, so it never *under*-reports a possible merge point.

Latency resolution order
------------------------
1. ``latency_cycles`` field in modules.yml entry (explicit override)
2. HLS synthesis report worst-case latency (if hls_reports dict is supplied)
3. ``latency_hint`` field in modules.yml entry (rough manual estimate)
4. ``None`` — unknown; reported as a warning in the checker

Edges are inferred from ``connections`` and ``topology_groups`` in design.yml
(via the IR's resolved connections in the primary path; via a raw re-parse
in the fallback path). An edge also carries the
originating ``Connection``'s ``register_stages``/``delay_cycles``/``cdc``
latency (``generated_transformation`` provenance) — previously discarded
entirely, meaning the checker was blind to any latency FORGE itself
inserts on a connection.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from forge.analyze.latency_model import LatencyProvenance, LatencyValue
from forge.ir.identifiers import resolved_instance_id


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class LatencyNode:
    name: str             # design.yml module name (e.g. "dec")
    ref: str              # modules.yml module ref  (e.g. "hit_decoder")
    instances: int
    kind: str             # "hls" | "rtl" | "unknown"
    latency: Optional[LatencyValue]
    is_variable: bool = False
    # The real, canonical instance id
    # (``forge.ir.identifiers.resolved_instance_id`` — e.g. "dec_0"), used
    # for every join against the canonical IR (``ResolvedInstance.id``).
    # ``display_name`` is the existing human-readable bracket form (e.g.
    # "dec[0]") this module has always used for ``name`` — kept as its own
    # field, unchanged, so any Markdown/report rendering that already
    # depends on the bracket form is unaffected. Both default to ``name``
    # in ``__post_init__`` so existing call sites (and this file's own
    # legacy fallback path) that don't pass them explicitly still get a
    # correct value for the common single-instance case.
    instance_id: str = ""
    display_name: str = ""

    def __post_init__(self) -> None:
        if not self.instance_id:
            self.instance_id = self.name
        if not self.display_name:
            self.display_name = self.name

    # --- Back-compat accessors -------------------------------------------
    # LatencyNode was previously a bare (latency_cycles, latency_source)
    # pair. Never serialized (latency-check has no --json/
    # --format flag), so this internal reshape is safe; these properties
    # let checker.py/reporter.py keep reading .latency_cycles/.latency_source
    # unchanged.
    @property
    def latency_cycles(self) -> Optional[int]:
        return self.latency.cycles if self.latency else None

    @property
    def latency_source(self) -> str:
        if self.latency is None or self.latency.provenance is None:
            return "unknown"
        return self.latency.provenance.source


@dataclasses.dataclass
class LatencyEdge:
    src: str
    dst: str
    latency: Optional[LatencyValue] = None
    # ``latency is None`` is
    # ambiguous on its own — it means both "this edge adds no known extra
    # cycles" (a plain same-domain connection) AND "this edge is a
    # mailbox_transfer/async_fifo CDC crossing whose latency is
    # deliberately, honestly unknown" (_edge_latency_from_connection's own
    # docstring). Every merge-point/upstream-chain walk before this slice
    # collapsed the second case into "0 extra cycles" instead of "this
    # whole path is now unknown" — invisible until a design first wired a
    # real fixed-latency-declared module with a real fixed-latency-declared
    # sibling predecessor on the OTHER side of a mailbox_transfer/async_fifo
    # edge (every prior CDC-fed merge point happened to have its own
    # unrelated "unknown" node latency masking the gap, or no real sibling
    # predecessor requiring alignment at all). This flag lets both
    # ``_upstream_chain_latency`` and ``check_merge_points`` propagate the
    # real "unknown" instead of silently treating the crossing as free.
    unknown_cdc: bool = False


@dataclasses.dataclass
class LatencyGraph:
    nodes: Dict[str, LatencyNode]
    edges: List[LatencyEdge]

    def predecessors(self, name: str) -> List[str]:
        return [e.src for e in self.edges if e.dst == name]

    def successors(self, name: str) -> List[str]:
        return [e.dst for e in self.edges if e.src == name]

    def sources(self) -> List[str]:
        """Nodes with no incoming edges (pipeline inputs)."""
        dst_set = {e.dst for e in self.edges}
        return [n for n in self.nodes if n not in dst_set]

    def sinks(self) -> List[str]:
        """Nodes with no outgoing edges (pipeline outputs)."""
        src_set = {e.src for e in self.edges}
        return [n for n in self.nodes if n not in src_set]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _load_registry(design: dict, design_path: Path) -> dict:
    reg_field = design.get("registry")
    if reg_field:
        reg_path = (design_path.parent / reg_field).resolve()
        if reg_path.exists():
            return _load_yaml(reg_path)
    return {}


def _latency_value_from_declared_kind(
    kind: str,
    cycles: Optional[int],
    min_cycles: Optional[int],
    max_cycles: Optional[int],
) -> LatencyValue:
    """A structured ``latency: {kind: ...}`` declaration is explicit and
    authoritative — shared by both the IR-driven path
    (``Module.timing.latency``, a ``LatencyDeclaration``) and the legacy
    raw-YAML fallback (a plain dict from ``entry.get("latency")``), so
    the two paths can't silently diverge on this precedence rule."""
    return LatencyValue(
        kind=kind, cycles=cycles, min_cycles=min_cycles, max_cycles=max_cycles,
        provenance=LatencyProvenance("explicit_contract", detail="latency: block"),
    )


def _resolve_latency(
    latency_cycles: Optional[int],
    latency_hint: Optional[int],
    ref_name: str,
    hls_reports: Optional[Dict[str, int]],
) -> Optional[LatencyValue]:
    """Shared explicit > hls_report > hint > unknown precedence — used by
    both the IR-driven path and the legacy raw-YAML fallback, so the rule
    is defined in exactly one place. Returns ``None`` for "unknown" (no
    ``LatencyValue`` at all — ``LatencyNode.latency_source`` already
    reports "unknown" for a ``None`` ``.latency``, so there's no need for
    a sentinel provenance value)."""
    if latency_cycles is not None:
        return LatencyValue(cycles=latency_cycles, provenance=LatencyProvenance("explicit_contract"))
    if hls_reports and ref_name in hls_reports:
        return LatencyValue(cycles=hls_reports[ref_name], provenance=LatencyProvenance("hls_report"))
    if latency_hint is not None:
        return LatencyValue(cycles=latency_hint, provenance=LatencyProvenance("user_hint"))
    return None


def _edge_latency_from_connection(conn) -> Optional[LatencyValue]:
    """Fold ``register_stages``/``delay_cycles``/a
    known-depth CDC synchronizer into the edge's latency
    (``generated_transformation`` provenance) — this FORGE-inserted RTL
    has a real, known cycle depth that was previously discarded before
    reaching the checker (``LatencyEdge`` carried no latency at all).

    This covers the full 5-kind CDC primitive
    family: ``level_sync``/``2ff_sync`` (alias) is a fixed +2 destination-
    domain cycles, ``pulse_sync`` is a fixed +3 (2 toggle-sync
    stages + 1 edge-detect stage, ``cdc_pulse_sync.v``). ``mailbox_transfer``
    and ``async_fifo`` are deliberately NOT folded in — a request/acknowledge handshake's
    round-trip timing depends on relative clock phase, and a FIFO's
    fill/drain timing depends on relative write/read rates; neither is
    statically knowable, so such an edge stays ``latency=None``, not a
    fabricated cycle count.
    """
    cycles = 0
    details: List[str] = []
    if conn.register_stages:
        cycles += conn.register_stages
        details.append(f"register_stages={conn.register_stages}")
    if conn.delay_cycles:
        cycles += conn.delay_cycles
        tag = f"delay_cycles={conn.delay_cycles}"
        if conn.boundary:
            tag += f" (boundary={conn.boundary})"
        details.append(tag)
    if conn.cdc and conn.cdc.get("kind") in ("level_sync", "2ff_sync"):
        cycles += 2
        details.append("cdc:level_sync (cdc_sync2ff.v, fixed depth)")
    if conn.cdc and conn.cdc.get("kind") == "pulse_sync":
        cycles += 3
        details.append("cdc:pulse_sync (cdc_pulse_sync.v, fixed depth)")
    if not details:
        return None
    return LatencyValue(
        cycles=cycles,
        provenance=LatencyProvenance("generated_transformation", detail="; ".join(details)),
    )


def _build_latency_map(
    registry: dict,
    hls_reports: Optional[Dict[str, int]],
) -> Dict[str, Optional[LatencyValue]]:
    """Return {ref_name: LatencyValue-or-None}."""
    result: Dict[str, Optional[LatencyValue]] = {}
    for entry in registry.get("modules", []):
        name = entry.get("name", "")
        latency_block = entry.get("latency")
        if latency_block:
            # Same precedence as _build_graph_from_ir: a structured
            # latency: {kind: ...} block wins outright.
            result[name] = _latency_value_from_declared_kind(
                latency_block.get("kind"), latency_block.get("cycles"),
                latency_block.get("min_cycles"), latency_block.get("max_cycles"),
            )
            continue
        lat_cycles = int(entry["latency_cycles"]) if "latency_cycles" in entry else None
        lat_hint = int(entry["latency_hint"]) if "latency_hint" in entry else None
        result[name] = _resolve_latency(lat_cycles, lat_hint, name, hls_reports)
    return result


def _instance_names_raw(name: str, instances: int) -> List[str]:
    """One node name per physical instance — shared by both the IR-driven
    path (via _instance_node_names) and the legacy raw-YAML fallback,
    same "define the rule once" discipline as _resolve_latency."""
    n = max(1, instances)
    if n == 1:
        return [name]
    return [f"{name}[{i}]" for i in range(n)]


def _instance_node_names(mod) -> List[str]:
    """One node name per physical instance of *mod*.
    A single-instance module keeps its bare module name
    (``"col"``) — identical to every prior slice's naming, zero graph
    change for the common case. A multi-instance module gets
    ``"name[0]"``, ``"name[1]"``, ... so latency analysis can see real
    per-instance fan-in/fan-out instead of one collapsed bucket node."""
    return _instance_names_raw(mod.name, mod.instances)


def _resolved_registry_path(design_path: Path) -> Optional[Path]:
    """The registry path design.yml's own `registry:` field resolves to,
    or None if it doesn't declare one — used to decide whether a caller's
    `modules_yml_path` override is genuinely different (see build_graph)."""
    design = _load_yaml(design_path)
    reg_field = design.get("registry")
    if not reg_field:
        return None
    return (design_path.parent / reg_field).resolve()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_graph(
    design_path: Path,
    modules_yml_path: Optional[Path] = None,
    hls_reports: Optional[Dict[str, int]] = None,
) -> LatencyGraph:
    """Build a :class:`LatencyGraph` from *design_path*.

    Parameters
    ----------
    design_path:
        Path to the plugin's ``design.yml``.
    modules_yml_path:
        Optional explicit override for the modules registry.  If not given,
        the ``registry:`` field inside ``design.yml`` is used. When given
        and it resolves to the *same* file design.yml's own ``registry:``
        field already points to (the documented, real-world usage), the IR-
        driven path is used. When it resolves to a genuinely different
        file, this falls back to an independent raw-YAML re-parse — a
        narrow compatibility path for an override never observed in real
        usage, not the primary implementation.
    hls_reports:
        Optional mapping ``{module_name: worst_case_latency_cycles}`` produced
        by :func:`forge.analyze.hls_reports.extractor.latency_map_from_reports`.
    """
    design_path = Path(design_path).resolve()
    modules_yml_path = Path(modules_yml_path).resolve() if modules_yml_path is not None else None

    own_registry_path = _resolved_registry_path(design_path)
    override_differs = (
        modules_yml_path is not None
        and modules_yml_path != own_registry_path
    )

    if override_differs:
        return _build_graph_legacy(design_path, modules_yml_path, hls_reports)
    return _build_graph_from_ir(design_path, hls_reports)


def _build_graph_from_ir(
    design_path: Path,
    hls_reports: Optional[Dict[str, int]] = None,
) -> LatencyGraph:
    """Migration step 7: build the LatencyGraph from ``DesignConfig`` — the
    same shared loader the canonical IR itself is built from
    (``forge.topgen.config``) — instead of independently re-parsing
    design.yml/modules.yml as raw YAML.

    Deliberately stops at ``DesignConfig``/``Module`` rather than going
    through the full matched IR (``forge.ir.build_project_ir``): latency
    analysis only ever needed module-level topology (name/kind/instances/
    timing, and module-to-module connectivity) — it never needed IP/port-
    level physical matching, and forcing it through the full IR would
    require ``ip_info``/contracts to resolve successfully, breaking the
    tool's existing "usable before synthesis" property (a design.yml can be
    latency-checked long before any IP is built or contract is written).
    ``Connection``/``TopologyGroup`` already carry singular, fan-out-
    expanded ``from_``/``to`` module names, so no port matching is needed
    to build module-to-module edges either.
    """
    from forge.topgen.config import DesignConfig

    cfg = DesignConfig.load_relaxed(design_path)

    mod_by_name = {mod.name: mod for mod in cfg.modules}

    nodes: Dict[str, LatencyNode] = {}
    for mod in cfg.modules:
        ref = mod.ip_info_key or mod.name
        timing = mod.timing
        lat_cycles = timing.latency_cycles if timing else None
        lat_hint = timing.latency_hint if timing else None
        is_var = timing.variable_latency if timing else False
        declaration = timing.latency if timing else None
        if declaration is not None:
            # A structured latency: {kind: ...} block is
            # explicit and authoritative — takes precedence over the flat
            # latency_cycles/hls_report/latency_hint chain, same "explicit
            # wins" precedence _resolve_latency already applies to
            # latency_cycles.
            lat = _latency_value_from_declared_kind(
                declaration.kind, declaration.cycles, declaration.min_cycles, declaration.max_cycles,
            )
        else:
            lat = _resolve_latency(lat_cycles, lat_hint, ref, hls_reports)
        # One LatencyNode per physical instance — see
        # this module's docstring. Every instance of the same module
        # shares the exact same latency (a module/HLS-synthesis-level
        # fact, not something that varies per instance).
        for idx, inst_name in enumerate(_instance_node_names(mod)):
            nodes[inst_name] = LatencyNode(
                name=inst_name,
                ref=ref,
                instances=1,
                kind=mod.kind,
                latency=lat,
                is_variable=is_var,
                # The real, canonical IR instance id
                # (matches forge.ir.build.py's ResolvedInstance.id exactly)
                # — the fix for the bracket-vs-underscore join mismatch.
                instance_id=resolved_instance_id(mod.name, idx, mod.instances),
                display_name=inst_name,
            )

    edges: List[LatencyEdge] = []
    seen_pairs: set = set()

    def _add_edge(
        src: str, dst: str, latency: Optional[LatencyValue] = None, unknown_cdc: bool = False,
    ) -> None:
        if src in nodes and dst in nodes and (src, dst) not in seen_pairs:
            seen_pairs.add((src, dst))
            edges.append(LatencyEdge(src=src, dst=dst, latency=latency, unknown_cdc=unknown_cdc))

    def _add_instance_edges(
        src_mod_name: str, dst_mod_name: str,
        latency: Optional[LatencyValue] = None, unknown_cdc: bool = False,
    ) -> None:
        src_mod = mod_by_name.get(src_mod_name)
        dst_mod = mod_by_name.get(dst_mod_name)
        src_names = _instance_node_names(src_mod) if src_mod else [src_mod_name]
        dst_names = _instance_node_names(dst_mod) if dst_mod else [dst_mod_name]
        for s in src_names:
            for d in dst_names:
                _add_edge(s, d, latency, unknown_cdc)

    for conn in cfg.connections:
        conn_cdc_kind = conn.cdc.get("kind") if conn.cdc else None
        _add_instance_edges(
            conn.from_, conn.to, _edge_latency_from_connection(conn),
            unknown_cdc=conn_cdc_kind in ("mailbox_transfer", "async_fifo"),
        )
    for tg in cfg.topology_groups:
        # TopologyGroup carries no register_stages/delay_cycles/cdc field —
        # honestly latency=None (no data source), not a fabricated 0.
        _add_instance_edges(tg.from_, tg.to)

    return LatencyGraph(nodes=nodes, edges=edges)


def _build_graph_legacy(
    design_path: Path,
    modules_yml_path: Optional[Path] = None,
    hls_reports: Optional[Dict[str, int]] = None,
) -> LatencyGraph:
    """Pre-migration implementation: independently re-parses design.yml/
    modules.yml as raw YAML. Kept only as a compatibility fallback for a
    `modules_yml_path` override that genuinely differs from design.yml's
    own `registry:` field — never observed in real/documented usage, but
    the public `build_graph` signature has always allowed it."""
    design = _load_yaml(design_path)

    if modules_yml_path is not None:
        registry = _load_yaml(modules_yml_path)
    else:
        registry = _load_registry(design, design_path)

    latency_map = _build_latency_map(registry, hls_reports)
    reg_by_name: Dict[str, dict] = {e["name"]: e for e in registry.get("modules", [])}
    design_mod_by_name: Dict[str, dict] = {m["name"]: m for m in design.get("modules", [])}

    # ── Nodes ──────────────────────────────────────────────────────────────
    # One node per physical instance,
    # same rule as _build_graph_from_ir (via _instance_names_raw) — so the
    # two paths can't silently diverge on graph shape.
    nodes: Dict[str, LatencyNode] = {}
    for mod in design.get("modules", []):
        name = mod["name"]
        ref = mod.get("ref", name)
        instances = mod.get("instances", 1)
        reg_entry = reg_by_name.get(ref, {})
        kind = reg_entry.get("kind", "unknown")
        lat = latency_map.get(ref)
        is_var = bool(reg_entry.get("variable_latency", False))
        for idx, inst_name in enumerate(_instance_names_raw(name, instances)):
            nodes[inst_name] = LatencyNode(
                name=inst_name,
                ref=ref,
                instances=1,
                kind=kind,
                latency=lat,
                is_variable=is_var,
                instance_id=resolved_instance_id(name, idx, instances),
                display_name=inst_name,
            )

    # ── Edges ──────────────────────────────────────────────────────────────
    # Legacy raw-YAML path: register_stages/delay_cycles/cdc are not folded
    # into edge latency here (unlike _build_graph_from_ir) — this fallback
    # exists only for a modules_yml_path override never observed in real
    # usage (see build_graph's docstring), not worth duplicating the
    # Connection-object-based logic for a raw dict re-parse.
    def _add_edge(src: str, dst: str, edges: List[LatencyEdge]) -> None:
        if src in nodes and dst in nodes:
            if not any(e.src == src and e.dst == dst for e in edges):
                edges.append(LatencyEdge(src=src, dst=dst))

    def _endpoints(value) -> List[str]:
        """Normalise a connection endpoint into a list of module names.

        A ``from``/``to`` endpoint may be a single module name or, for fan-out
        and fan-in connections, a list of module names.  Both forms are
        expanded into individual edges.
        """
        if isinstance(value, list):
            return [v for v in value if isinstance(v, str) and v]
        if isinstance(value, str) and value:
            return [value]
        return []

    def _instance_endpoints(module_names: List[str]) -> List[str]:
        """Expand each design-level module name in *module_names* into its
        per-instance node names (same rule as the node-construction loop
        above)."""
        out: List[str] = []
        for name in module_names:
            m = design_mod_by_name.get(name)
            instances = m.get("instances", 1) if m else 1
            out.extend(_instance_names_raw(name, instances))
        return out

    edges: List[LatencyEdge] = []
    for conn in design.get("connections", []):
        for src in _instance_endpoints(_endpoints(conn.get("from", ""))):
            for dst in _instance_endpoints(_endpoints(conn.get("to", ""))):
                _add_edge(src, dst, edges)
    for tg in design.get("topology_groups", []):
        for src in _instance_endpoints(_endpoints(tg.get("from", ""))):
            for dst in _instance_endpoints(_endpoints(tg.get("to", ""))):
                _add_edge(src, dst, edges)

    return LatencyGraph(nodes=nodes, edges=edges)
