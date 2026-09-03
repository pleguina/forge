"""The visual-design-explorer's graph model.

**Load-bearing invariant**: ``DesignGraph`` is a deterministic, read-only
**visualization projection** of exactly one canonical IR
(``forge.ir.model.ResolvedProject``). It may enrich IR data with external
overlays (latency, maturity, verification), but it may never independently
resolve topology, infer semantics, or in any way change what the design
*means*. Every fact ``DesignGraph`` states about topology must already be
a fact ``ResolvedProject`` states; ``DesignGraph`` only re-shapes and
enriches, never invents.

``DesignGraph`` carries real provenance back to the IR it was built from
(``source_ir_schema_version``/``source_ir_content_hash``/``overlay_hashes``),
so its own identity is reproducible and auditable — never from generation
time, current working directory, absolute paths, or client-side layout
state (layout/node positions are computed client-side by Cytoscape.js at
render time, never stored here).

Node kinds (``GraphNodeKind``) are explicit and typed rather than 1:1 with
``ResolvedInstance`` — real IR connections exist whose producer or
consumer endpoint is the ``"$external"``/``"$tie_off"`` sentinel
(``forge/ir/build.py``), with no materialized node to attach to before
this module. ``EXTERNAL_PORT``/``MODULE_GROUP``/``DOMAIN_GROUP`` nodes are
materialized as real graph nodes with real, stable ids so every real
connection resolves to two real, resolvable node ids — never a fabricated
or silently-dropped endpoint.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...core.artifact_schema import ArtifactSchema
from ...core.utils.portable_path import portable_display_path
from ...ir.model import (
    ResolvedConnection,
    ResolvedProject,
    SourceLocation,
)
from ...ir.serialize import content_hash as ir_content_hash

DESIGN_GRAPH_SCHEMA = ArtifactSchema("forge.design_graph", "1.0")

_EXTERNAL = "$external"
_TIE_OFF = "$tie_off"


# ── Node/reference vocabulary ────────────────────────────────────────────

class GraphNodeKind(str, Enum):
    INSTANCE = "instance"              # a real ResolvedInstance
    EXTERNAL_PORT = "external-port"    # a top-level I/O/clock/reset net, or a synthesized tie-off source
    MODULE_GROUP = "module-group"      # the compound parent for all instances of one module definition
    DOMAIN_GROUP = "domain-group"      # the compound parent for one clock or reset domain


@dataclass(frozen=True)
class ObjectReference:
    """A reference into one of the real IR object kinds — never
    pre-formatted as ``"module:x"``; ``kind`` and ``id`` are separate so a
    consumer can dispatch on kind without string-parsing."""
    kind: str  # "module-definition" | "instance" | "connection" | "interface" | "top-port"
    id:   str


@dataclass(frozen=True)
class ProjectionDiagnostic:
    """A projection-layer diagnostic reference — corresponds to
    ``forge.ir.model.DiagnosticReference`` but with its free-text
    ``object_id`` resolved into a typed ``ObjectReference`` (or ``None``
    when nothing could be identified). Deliberately a distinct type: per
    the module's projection invariant, this enriches IR data, it does not
    redefine the IR's own schema."""
    severity: str
    message:  str
    code:     Optional[str]
    target:   Optional[ObjectReference]
    location: Optional[SourceLocation]


# ── Contract-maturity rollup ─────────────────────────────────────────────

class MaturityStatus(str, Enum):
    CONTRACT = "contract"              # every classified connection touching this module is contract_wiring
    MIXED = "mixed"                    # a real mix
    COMPATIBILITY = "compatibility"    # every classified connection is auto_match/heuristic
    UNKNOWN = "unknown"                # no classified connections (e.g. an unconnected stub)


@dataclass(frozen=True)
class MaturitySummary:
    status: MaturityStatus
    contract_edges: int
    explicit_edges: int       # port_map / port_map_ranges
    topology_edges: int       # topology_group
    compatibility_edges: int  # auto_match / heuristic


_EXPLICIT_METHODS = {"port_map", "port_map_ranges"}
_COMPATIBILITY_METHODS = {"auto_match", "heuristic"}


def _maturity_from_counts(contract: int, explicit: int, topology: int, compatibility: int) -> MaturitySummary:
    total = contract + explicit + topology + compatibility
    if total == 0:
        status = MaturityStatus.UNKNOWN
    elif contract == total:
        status = MaturityStatus.CONTRACT
    elif compatibility == total:
        status = MaturityStatus.COMPATIBILITY
    else:
        status = MaturityStatus.MIXED
    return MaturitySummary(
        status=status, contract_edges=contract, explicit_edges=explicit,
        topology_edges=topology, compatibility_edges=compatibility,
    )


def _compute_module_maturity(connections: Sequence[ResolvedConnection], instance_module: Dict[str, str]) -> Dict[str, MaturitySummary]:
    """Per-module-definition ``MaturitySummary``, computed directly from
    the real ``wiring_method`` on every connection touching that module
    (as producer or consumer) — no new data source, real aggregation
    instead of a lossy boolean ("Maturity is not a boolean")."""
    counts: Dict[str, Dict[str, int]] = {}

    def _bump(module_name: str, bucket: str) -> None:
        entry = counts.setdefault(module_name, {"contract": 0, "explicit": 0, "topology": 0, "compatibility": 0})
        entry[bucket] += 1

    for conn in connections:
        method = conn.wiring_method
        if method == "contract_wiring":
            bucket = "contract"
        elif method in _EXPLICIT_METHODS:
            bucket = "explicit"
        elif method == "topology_group":
            bucket = "topology"
        elif method in _COMPATIBILITY_METHODS:
            bucket = "compatibility"
        else:
            continue  # unclassified (e.g. an unresolved global net) — not real evidence either way

        touched = set()
        for endpoint in (conn.producer, conn.consumer):
            mod = instance_module.get(endpoint.instance_id)
            if mod is not None:
                touched.add(mod)
        for mod in touched:
            _bump(mod, bucket)

    return {
        name: _maturity_from_counts(c["contract"], c["explicit"], c["topology"], c["compatibility"])
        for name, c in counts.items()
    }


# ── Diagnostic object_id parsing ──────────────────────────────────────────

_MODULE_OBJECT_ID_RE = re.compile(r"^module:(?P<name>[^#]+)(?:#interface:(?P<iface>.+))?$")


def parse_object_reference(object_id: Optional[str]) -> Optional[ObjectReference]:
    """Parse a real IR ``DiagnosticReference.object_id`` string into a
    typed ``ObjectReference``, honestly returning ``None`` for anything
    that doesn't match a known, structured convention (e.g. a free-text
    validator YAML-path like ``"connections[2]"``) — never guessed.

    Handles the two real, structured conventions the IR emits today
    (``forge/ir/build.py``): ``f"module:{name}"`` (module-definition) and
    ``f"module:{name}#interface:{role}"`` (interface, on a module).
    ``instance``/``connection``/``top-port`` kinds are part of the
    vocabulary for forward-compatibility but have no real emission site
    today — nothing parses to them yet.
    """
    if not object_id:
        return None
    m = _MODULE_OBJECT_ID_RE.match(object_id)
    if not m:
        return None
    name = m.group("name")
    iface = m.group("iface")
    if iface:
        return ObjectReference(kind="interface", id=f"{name}#{iface}")
    return ObjectReference(kind="module-definition", id=name)


# ── Graph nodes/edges/objects ────────────────────────────────────────────

@dataclass(frozen=True)
class GraphNode:
    id:    str
    kind:  GraphNodeKind
    label: str
    parent: Optional[str] = None  # compound-parent node id (Cytoscape `parent`) — the default, load-time grouping
    module: Optional[str] = None  # module definition name, when applicable (INSTANCE/MODULE_GROUP)
    clock_domain: Optional[str] = None
    reset_domain: Optional[str] = None
    # Members of a MODULE_GROUP/DOMAIN_GROUP compound node (instance ids)
    # — carried directly so client-side re-parenting (e.g. toggling
    # clock-domain grouping) doesn't need to re-derive membership by
    # walking `parent` pointers.
    members: Tuple[str, ...] = ()
    latency: Optional[Dict[str, Any]] = None  # asdict()'d LatencyValue, or None for genuinely absent data
    maturity: Optional[MaturitySummary] = None
    diagnostics: Tuple[ProjectionDiagnostic, ...] = ()
    inherited_diagnostics: Tuple[ProjectionDiagnostic, ...] = ()  # INSTANCE only — its MODULE_GROUP's diagnostics
    object_id: str = ""  # the id of the ObjectRecord in DesignGraph.objects backing this node's details, if any


@dataclass(frozen=True)
class GraphEdge:
    id: str
    source: str
    target: str
    wiring_method: Optional[str]
    crosses_clock_domain: bool
    crosses_reset_domain: bool
    transformations: Tuple[Dict[str, Any], ...]  # asdict()'d ResolvedTransformation entries
    diagnostics: Tuple[ProjectionDiagnostic, ...] = ()
    object_id: str = ""


@dataclass(frozen=True)
class ObjectRecord:
    """One entry in the typed registry backing the selected-object details
    panel — one per real IR object (instance, module definition,
    connection, transformation, top-port/external-port, interface), built
    once here and referenced by id everywhere else rather than
    duplicated."""
    id:    str
    kind:  str
    label: str
    data:  Dict[str, Any]


@dataclass(frozen=True)
class OpenDecision:
    """One connection the design has not made, and what could make it.

    The explorer's authoring half (Phase J): an unwired producer with
    candidate consumers, or a consumer with competing producers.

    Computed **nowhere near here**. ``forge.project.discovery`` is what
    decides that a port is unwired and which candidates are equally valid;
    this carries its answer, and ``declaration``/``command`` carry the exact
    ``forge.yml`` entry and CLI invocation that record a choice. The
    explorer therefore never resolves topology, never infers a candidate,
    and never holds a decision the project file does not — which is the
    difference between an authoring view and a private parallel state.
    """
    id: str
    subject: str            # "module.port" the question is about
    question: str
    #: What the subject could be connected to — a real candidate list, in
    #: the order discovery reported it.
    candidates: Tuple[str, ...] = ()
    #: Is *subject* the producer (candidates are consumers), or the reverse?
    subject_is_producer: bool = True
    #: The node id the question attaches to, so the UI can highlight it.
    node_id: str = ""

    def declaration_for(self, candidate: str) -> Dict[str, str]:
        """The ``connections:`` entry that records choosing *candidate*."""
        producer, consumer = (
            (self.subject, candidate) if self.subject_is_producer
            else (candidate, self.subject)
        )
        return {"from": producer, "to": consumer}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "question": self.question,
            "candidates": list(self.candidates),
            "subject_is_producer": self.subject_is_producer,
            "node_id": self.node_id,
            "choices": [
                {
                    "candidate": candidate,
                    "declaration": self.declaration_for(candidate),
                }
                for candidate in self.candidates
            ],
        }


@dataclass(frozen=True)
class DesignGraph:
    schema: ArtifactSchema
    source_ir_schema_version: str
    source_ir_content_hash: str
    overlay_hashes: Dict[str, str]
    nodes: Tuple[GraphNode, ...]
    edges: Tuple[GraphEdge, ...]
    objects: Tuple[ObjectRecord, ...]
    #: Connections the design has not made — empty unless the caller
    #: supplied them (see :func:`build_design_graph`).
    open_decisions: Tuple[OpenDecision, ...] = ()


# ── Construction helpers ─────────────────────────────────────────────────

def _hash_json(obj: Any) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _module_group_id(module_name: str) -> str:
    return f"module:{module_name}"


def _domain_group_id(axis: str, domain_name: str) -> str:
    return f"domain:{axis}:{domain_name}"


def _external_port_id(name: str) -> str:
    return f"top:{name}"


def _tie_off_id(consumer_instance: str, consumer_port: Optional[str]) -> str:
    return f"tie-off:{consumer_instance}.{consumer_port}"


def _portable_source_files(files: Sequence[str], roots: Sequence[Path]) -> List[str]:
    return [portable_display_path(f, roots) for f in files]


def build_design_graph(
    project: ResolvedProject,
    *,
    latency_by_instance: Optional[Dict[str, Any]] = None,
    verification_flow_entry_points: Optional[Dict[str, str]] = None,
    open_decisions: Optional[Sequence[OpenDecision]] = None,
    source_roots: Optional[Sequence["str | Path"]] = None,
) -> DesignGraph:
    """Build a ``DesignGraph`` projection of *project*.

    *latency_by_instance*: optional
    ``{instance_id: LatencyValue}`` map, keyed by the real, canonical
    ``ResolvedInstance.id`` (e.g. via
    ``forge.analysis.latency_static.graph.LatencyNode.instance_id`` — the
    fixed join). Honestly absent (``None``/omitted key) where no latency
    data exists — never fabricated.

    *verification_flow_entry_points*: optional ``{flow_name:
    module_group_node_id}`` map (see
    ``forge.analysis.design_explorer.verification_join``) — the
    conservative "flow entry points" overlay: a real fact
    ("this flow's declared entry point is this module"), never rendered
    or labeled as proof of behavioral coverage.

    *open_decisions*: optional :class:`OpenDecision` list — the connections
    the design has not made and the candidates for each, as
    ``forge.project.discovery`` reported them. Supplied by the caller for
    the same reason latency and verification data are: this module projects
    and enriches, it never resolves topology itself.

    *source_roots*: paths every absolute source-file path is made
    portable against (``forge.core.utils.portable_path.portable_display_path``)
    — closes the absolute-path leak at construction time rather than
    patching it per-renderer later.
    """
    design = project.design
    roots = [Path(r) for r in (source_roots or [])]
    latency_by_instance = latency_by_instance or {}
    verification_flow_entry_points = verification_flow_entry_points or {}
    open_decisions = tuple(open_decisions or ())

    instance_module = {inst.id: inst.module for inst in design.instances}
    module_by_name = {m.name: m for m in design.modules}
    maturity_by_module = _compute_module_maturity(design.connections, instance_module)

    # ── Diagnostics: parse + group by resolved attachment point ─────────
    parsed_diagnostics: List[ProjectionDiagnostic] = []
    diags_by_module: Dict[str, List[ProjectionDiagnostic]] = {}
    for d in design.diagnostics:
        target = parse_object_reference(d.object_id)
        pd = ProjectionDiagnostic(
            severity=d.severity, message=d.message, code=d.code,
            target=target, location=d.location,
        )
        parsed_diagnostics.append(pd)
        if target is not None and target.kind in ("module-definition", "interface"):
            module_name = target.id.split("#", 1)[0]
            diags_by_module.setdefault(module_name, []).append(pd)

    # ── MODULE_GROUP nodes ────────────────────────────────────────────────
    module_instance_ids: Dict[str, List[str]] = {}
    for inst in design.instances:
        module_instance_ids.setdefault(inst.module, []).append(inst.id)

    nodes: List[GraphNode] = []
    for mod in design.modules:
        members = tuple(sorted(module_instance_ids.get(mod.name, [])))
        nodes.append(GraphNode(
            id=_module_group_id(mod.name),
            kind=GraphNodeKind.MODULE_GROUP,
            label=mod.name,
            module=mod.name,
            members=members,
            maturity=maturity_by_module.get(mod.name),
            diagnostics=tuple(diags_by_module.get(mod.name, [])),
            object_id=_module_group_id(mod.name),
        ))

    # ── DOMAIN_GROUP nodes ────────────────────────────────────────────────
    for cd in design.clock_domains:
        nodes.append(GraphNode(
            id=_domain_group_id("clock", cd.name),
            kind=GraphNodeKind.DOMAIN_GROUP,
            label=f"clock: {cd.name}",
            clock_domain=cd.name,
            members=tuple(sorted(cd.instances)),
            object_id=_domain_group_id("clock", cd.name),
        ))
    for rd in design.reset_domains:
        nodes.append(GraphNode(
            id=_domain_group_id("reset", rd.name),
            kind=GraphNodeKind.DOMAIN_GROUP,
            label=f"reset: {rd.name}",
            reset_domain=rd.name,
            members=tuple(sorted(rd.instances)),
            object_id=_domain_group_id("reset", rd.name),
        ))

    # ── INSTANCE nodes ────────────────────────────────────────────────────
    for inst in design.instances:
        lat = latency_by_instance.get(inst.id)
        nodes.append(GraphNode(
            id=inst.id,
            kind=GraphNodeKind.INSTANCE,
            label=inst.id,
            parent=_module_group_id(inst.module),
            module=inst.module,
            clock_domain=inst.clock_domain,
            reset_domain=inst.reset_domain,
            latency=asdict(lat) if lat is not None else None,
            maturity=maturity_by_module.get(inst.module),
            inherited_diagnostics=tuple(diags_by_module.get(inst.module, [])),
            object_id=inst.id,
        ))

    # ── EXTERNAL_PORT nodes: real ResolvedTopLevelPort entries (post-
    # generation only) unioned with any "$external"/"$tie_off"-endpoint
    # connection's own signal name (pre-generation `forge inspect` never
    # has top_ports populated at all — see ResolvedTopLevelPort's
    # docstring — so this union is what guarantees every real connection
    # still resolves to two real, resolvable node ids regardless of
    # whether generation has run yet).
    external_nodes: Dict[str, GraphNode] = {}
    for p in design.top_ports:
        nid = _external_port_id(p.name)
        external_nodes[nid] = GraphNode(
            id=nid, kind=GraphNodeKind.EXTERNAL_PORT,
            label=f"{p.name} ({p.direction}, {p.width}b)",
            object_id=nid,
        )
    for conn in design.connections:
        if conn.producer.instance_id == _EXTERNAL:
            sig = conn.producer.interface_name or conn.producer.port or conn.producer.instance_id
            nid = _external_port_id(sig)
            if nid not in external_nodes:
                external_nodes[nid] = GraphNode(
                    id=nid, kind=GraphNodeKind.EXTERNAL_PORT, label=sig, object_id=nid,
                )
        elif conn.producer.instance_id == _TIE_OFF:
            nid = _tie_off_id(conn.consumer.instance_id, conn.consumer.port)
            if nid not in external_nodes:
                external_nodes[nid] = GraphNode(
                    id=nid, kind=GraphNodeKind.EXTERNAL_PORT, label="tie-off (0)", object_id=nid,
                )
    nodes.extend(external_nodes[k] for k in sorted(external_nodes))

    # ── Edges ─────────────────────────────────────────────────────────────
    def _endpoint_node_id(instance_id: str, port: Optional[str], interface_name: Optional[str]) -> str:
        if instance_id == _EXTERNAL:
            sig = interface_name or port or instance_id
            return _external_port_id(sig)
        return instance_id

    edges: List[GraphEdge] = []
    for conn in design.connections:
        if conn.producer.instance_id == _TIE_OFF:
            src_id = _tie_off_id(conn.consumer.instance_id, conn.consumer.port)
        else:
            src_id = _endpoint_node_id(
                conn.producer.instance_id, conn.producer.port, conn.producer.interface_name,
            )
        dst_id = _endpoint_node_id(
            conn.consumer.instance_id, conn.consumer.port, conn.consumer.interface_name,
        )
        edges.append(GraphEdge(
            id=conn.id,
            source=src_id,
            target=dst_id,
            wiring_method=conn.wiring_method,
            crosses_clock_domain=conn.crosses_clock_domain,
            crosses_reset_domain=conn.crosses_reset_domain,
            transformations=tuple(asdict(t) for t in conn.transformations),
            object_id=conn.id,
        ))

    # ── Verification overlay: mirror flow-entry-point diagnostics-free
    # info directly onto MODULE_GROUP nodes as a lightweight annotation
    # (kept out of `diagnostics` — this is not a diagnostic, it's real,
    # conservative coverage metadata; the renderers read it from
    # `ObjectRecord.data["verification_flow_entry_points"]` instead, see
    # below, so GraphNode's shape doesn't grow a rarely-used field).

    # ── Object registry ──────────────────────────────────────────────────
    objects: List[ObjectRecord] = []
    for mod in design.modules:
        entry_flows = sorted(
            flow for flow, node_id in verification_flow_entry_points.items()
            if node_id == _module_group_id(mod.name)
        )
        objects.append(ObjectRecord(
            id=_module_group_id(mod.name),
            kind="module-definition",
            label=mod.name,
            data={
                "name": mod.name,
                "kind": mod.kind,
                "top": mod.top,
                "source_files": _portable_source_files(mod.source_files, roots),
                "contract_path": (
                    portable_display_path(mod.contract_path, roots) if mod.contract_path else None
                ),
                "ports_resolved": mod.ports_resolved,
                "parameters": dict(mod.parameters),
                "latency_cycles": mod.latency_cycles,
                "latency_hint": mod.latency_hint,
                "is_variable_latency": mod.is_variable_latency,
                "latency_declaration": asdict(mod.latency) if mod.latency else None,
                "ip_info_key": mod.ip_info_key,
                "interfaces": [asdict(i) for i in mod.interfaces],
                "instances": sorted(module_instance_ids.get(mod.name, [])),
                "maturity": asdict(maturity_by_module[mod.name]) if mod.name in maturity_by_module else None,
                "diagnostics": [_diag_to_dict(d) for d in diags_by_module.get(mod.name, [])],
                "verification_flow_entry_points": entry_flows,
            },
        ))
        for iface in mod.interfaces:
            iface_id = f"{mod.name}#{iface.name}"
            objects.append(ObjectRecord(
                id=iface_id, kind="interface", label=f"{mod.name}.{iface.name}",
                data={"module": mod.name, **asdict(iface)},
            ))

    for inst in design.instances:
        objects.append(ObjectRecord(
            id=inst.id, kind="instance", label=inst.id,
            data={
                "id": inst.id,
                "module": inst.module,
                "index": inst.index,
                "clock_domain": inst.clock_domain,
                "reset_domain": inst.reset_domain,
                "latency": asdict(latency_by_instance[inst.id]) if inst.id in latency_by_instance else None,
                "inherited_diagnostics": [_diag_to_dict(d) for d in diags_by_module.get(inst.module, [])],
            },
        ))

    for conn in design.connections:
        objects.append(ObjectRecord(
            id=conn.id, kind="connection", label=conn.id,
            data={
                "id": conn.id,
                "producer": asdict(conn.producer),
                "consumer": asdict(conn.consumer),
                "wiring_method": conn.wiring_method,
                "transformations": [asdict(t) for t in conn.transformations],
                "crosses_clock_domain": conn.crosses_clock_domain,
                "crosses_reset_domain": conn.crosses_reset_domain,
                "matching_evidence": asdict(conn.matching_evidence) if conn.matching_evidence else None,
            },
        ))
        for xform in conn.transformations:
            objects.append(ObjectRecord(
                id=xform.id, kind="transformation", label=f"{xform.kind} ({conn.id})",
                data={"connection_id": conn.id, **asdict(xform)},
            ))

    for p in design.top_ports:
        objects.append(ObjectRecord(
            id=_external_port_id(p.name), kind="top-port", label=p.name,
            data=asdict(p),
        ))

    objects.sort(key=lambda o: (o.kind, o.id))
    nodes.sort(key=lambda n: (n.kind.value, n.id))
    edges.sort(key=lambda e: e.id)

    overlay_hashes: Dict[str, str] = {}
    if latency_by_instance:
        overlay_hashes["latency"] = _hash_json({
            iid: asdict(lv) for iid, lv in sorted(latency_by_instance.items())
        })
    if verification_flow_entry_points:
        overlay_hashes["verification"] = _hash_json(dict(sorted(verification_flow_entry_points.items())))
    if open_decisions:
        overlay_hashes["authoring"] = _hash_json([d.to_dict() for d in open_decisions])

    return DesignGraph(
        schema=DESIGN_GRAPH_SCHEMA,
        source_ir_schema_version=project.schema_version,
        source_ir_content_hash=ir_content_hash(project),
        overlay_hashes=overlay_hashes,
        open_decisions=open_decisions,
        nodes=tuple(nodes),
        edges=tuple(edges),
        objects=tuple(objects),
    )


def _diag_to_dict(d: ProjectionDiagnostic) -> Dict[str, Any]:
    return {
        "severity": d.severity,
        "message": d.message,
        "code": d.code,
        "target": asdict(d.target) if d.target else None,
        "location": asdict(d.location) if d.location else None,
    }
