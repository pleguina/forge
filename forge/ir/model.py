"""
Canonical resolved-design IR — data model only.

This module intentionally has no dependency on any CLI, renderer, or
generator. It exists so that a design's resolved facts (modules, instances,
interfaces, connections, domains, diagnostics) can be represented once,
deterministically, and serialized/hashed/diffed — instead of each subsystem
(topology generation, verification, latency analysis, testbench-parameter
extraction) independently re-deriving them from the same source YAML.

This model covers topology/config loading, interface-contract loading, and
IP/RTL port metadata, plus read-only consumption of the existing matcher's
output for connections. It does not yet cover: multi-clock-domain modeling
(single trivial domain only, see
``ResolvedClockDomain``/``ResolvedResetDomain``), generation-plan hashing,
protocol semantics, or verification planning (``ResolvedVerificationPlan``
is present but explicitly unpopulated).

``IR_SCHEMA_VERSION`` is independent of the ``forge`` package version — see
the "Versioning" section in the repository README for why FORGE tracks
multiple, independently-evolving version numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# LatencyDeclaration is the schema/config-layer type (no CLI/generator
# dependency itself), same layering ir/build.py already relies on for
# DesignConfig/Module — reused directly here rather
# than mirrored, matching how ResolvedModuleDefinition already reuses
# Module.timing's flat fields verbatim.
from ..contracts.config import LatencyDeclaration

IR_SCHEMA_VERSION = "0.2.0"


@dataclass
class SourceLocation:
    """A source reference, file-level only in this slice (no line/column
    tracking exists upstream yet — the YAML loaders this IR is built from
    don't retain parse positions)."""
    file: str
    line: Optional[int] = None
    column: Optional[int] = None


@dataclass
class DiagnosticReference:
    """A diagnostic attached to a specific IR object, so tooling (CLI,
    future visual explorer) can link a warning/error back to what it's
    about instead of only a free-text message."""
    severity: str  # 'error' | 'warning' | 'info'
    message: str
    code: Optional[str] = None
    object_id: Optional[str] = None
    location: Optional[SourceLocation] = None


@dataclass
class ResolvedPhysicalBinding:
    """The physical RTL/HLS port binding behind a logical interface member."""
    kind: str  # 'scalar' | 'prefix_array' | 'nd_tpl'
    raw_port: Optional[str] = None
    raw_port_prefix: Optional[str] = None
    raw_port_tpl: Optional[str] = None
    count: Optional[int] = None
    dims: Optional[List[int]] = None
    width: Optional[int] = None


@dataclass
class ResolvedInterfaceMember:
    """One physical signal belonging to a logical interface.

    A contract role opts into grouping by declaring a shared
    ``interface:`` name; roles that don't declare it keep today's 1:1
    mapping (interface name == role name, one member named after the
    role) — see ``forge.ir.build._build_interfaces``.

    ``direction`` is ``None`` when the member's physical direction matches
    its parent ``ResolvedLogicalInterface.direction`` (the common case).
    It is set explicitly only for members that flow the opposite way —
    e.g. a ``ready`` handshake signal returning from the consumer of an
    otherwise-input interface.
    """
    name: str
    binding: ResolvedPhysicalBinding
    direction: Optional[str] = None


@dataclass
class ResolvedLogicalInterface:
    """A named role on a module (e.g. ``clock_primary``, ``trigger_data``)."""
    name: str
    direction: str  # 'input' | 'output'
    wiring_kind: Optional[str] = None
    coordinates: Optional[Dict[str, Any]] = None
    protocol: Optional[str] = None
    # The resolved {"producers": {"min", "max"}} or
    # {"consumers": {"min", "max"}} bound (``max`` may be the literal
    # string "many"), taken from the group's `member: data` role — or
    # None when no `cardinality:` block is declared. Descriptive only;
    # enforcement happens design-wide in
    # ``forge.contracts.cardinality.verify_cardinality``, not here.
    cardinality: Optional[Dict[str, Any]] = None
    members: List[ResolvedInterfaceMember] = field(default_factory=list)


@dataclass
class ResolvedModuleDefinition:
    """A module definition (shared across all its instances).

    ``latency_cycles``/``latency_hint``/``is_variable_latency`` are
    populated directly from ``forge.contracts.config.Module.timing`` (no
    re-parsing). This intentionally does
    **not** include the ``hls_report`` latency-source tier
    (``forge.analysis.latency_static``'s external HLS-synthesis-report
    overlay) — that's runtime data supplied only when analyzing actual
    build artifacts, not a pre-generation design/registry fact.

    ``latency`` is the structured
    ``kind: fixed|bounded|elastic`` declaration, when the module used the
    new ``latency:`` YAML syntax — a
    ``forge.contracts.config.LatencyDeclaration``, additive alongside the
    flat fields above (which stay populated exactly as before; the two
    syntaxes are mutually exclusive per module, enforced at load time in
    ``contracts.config._pop_timing``, not here).
    """
    name: str
    kind: str  # 'hls' | 'rtl'
    top: str
    source_files: List[str] = field(default_factory=list)
    contract_path: Optional[str] = None
    ports_resolved: bool = True
    interfaces: List[ResolvedLogicalInterface] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    latency_cycles: Optional[int] = None
    latency_hint: Optional[int] = None
    is_variable_latency: bool = False
    latency: Optional["LatencyDeclaration"] = None
    # The canonical registry name this module resolves to (set when a
    # design.yml entry uses `ref: <canonical>`; None for inline modules
    # that never declared one) — mirrors forge.contracts.config.Module.ip_info_key.
    ip_info_key: Optional[str] = None


@dataclass
class ResolvedInstance:
    """One instance of a module definition in the design.

    ``clock_domain``/``reset_domain`` are the resolved net name (e.g.
    ``"ap_clk"``) driving this instance, or ``None`` when the instance's
    module is clock-free/reset-free, or when nothing could be resolved
    (see ``DiagnosticReference`` for the latter case). These are the
    source of truth for domain membership — ``ResolvedClockDomain``/
    ``ResolvedResetDomain``'s ``instances`` lists are derived from them,
    not maintained independently.
    """
    id: str
    module: str
    index: Optional[int] = None
    clock_domain: Optional[str] = None
    reset_domain: Optional[str] = None


@dataclass
class ResolvedEndpoint:
    """One side of a connection — a concrete instance/interface/port."""
    instance_id: str
    interface_name: Optional[str] = None
    port: Optional[str] = None


@dataclass
class ResolvedTransformation:
    """A generated (or declared-and-approved) element sitting on a
    connection.

    Kinds with real, generated-or-declared instances (``forge/ir/build.py``):

    - ``pipeline_register`` — from ``Connection.register_stages`` (was
      ``'register'`` before the IR schema 0.2.0 kind-vocabulary expansion).
    - ``latency_delay`` — from ``Connection.delay_cycles`` with no
      ``boundary`` tag (was ``'delay'``).
    - ``slr_crossing`` — from ``Connection.delay_cycles`` *with* a
      ``boundary`` tag (the generator emits ``slr_crossing_delay`` instead
      of ``signal_delay`` for this case — one RTL instance, one
      transformation, not both `latency_delay` and `slr_crossing`).
      ``tag`` carries the boundary string.
    - ``fanout`` — computed generically (no new schema) whenever a
      producer pin drives more than one connection; excludes clock/reset
      global-net fan-out (universal, not a meaningful signal there).
    - ``cdc_synchronizer`` — from ``Connection.cdc: {kind: level_sync}``
      (``2ff_sync`` is a backwards-compatible alias for ``level_sync`` —
      both map to this same kind) (real RTL —
      ``cdc_sync2ff``).
    - ``pulse_sync`` — from ``Connection.cdc: {kind: pulse_sync,
      min_spacing_cycles: N}`` (real RTL —
      ``cdc_pulse_sync``, a toggle + double-flop + edge-detect
      synchronizer for a one-cycle source-domain pulse).
    - ``mailbox_transfer`` — from ``Connection.cdc: {kind:
      mailbox_transfer}`` (real RTL — ``cdc_mailbox``,
      a request/acknowledge handshake for a coherent multi-bit payload;
      one outstanding transaction at a time).
    - ``async_fifo`` — from ``Connection.cdc: {kind: async_fifo, depth:
      N}``. Real dual-clock FIFO RTL (``cdc_async_fifo``, Gray-code
      pointer synchronization); this kind now always has a corresponding
      generated instance (previously a documented limitation, no longer
      the case).
    - ``reset_synchronizer`` — from ``reset_domains.<name>.sync:
      reset_sync`` (real RTL — ``cdc_reset_sync``,
      async-assert/sync-deassert). Domain-keyed, not connection-keyed —
      see ``ResolvedResetDomain.transformations``, not this list.
    - ``tie_off`` — synthesized post-generation from the generator's
      ``tied_to_zero`` report (a ``"$tie_off"`` producer sentinel, mirrors
      ``"$external"``) — see ``forge.ir.build.build_tie_off_connections``.
      Absent from ``forge inspect``'s pre-generation IR (only known after
      generation runs), present only in ``gen-top``'s emitted
      ``design.ir.json`` — same asymmetry as ``ResolvedTopLevelPort``.
    - ``gather_scatter`` — matching-evidence expansion:
      ``forge.contracts.topology_deriver``'s scatter/gather classification
      (previously discarded before reaching ``MatchReport``) is now
      surfaced via ``MatchReport.gather_scatter_evidence`` and synthesized
      here as a real transformation, ``tag`` carrying ``"scatter"`` or
      ``"gather"``. Real, non-synthetic instances exist in
      ``plugins/trigger_demo`` (the ``decoder_to_collector`` gather group).

    Kinds defined in the vocabulary with **zero live instances today**
    (documented, not fabricated — same "reserved, no effect yet" precedent
    as the ``clock_secondary``/``reset_secondary`` contract roles, see
    docs/IP_INTERFACE_POLICY.md "Reserved roles"):

    - ``width_adapter`` / ``protocol_adapter`` — nothing in the matcher or
      generators ever inserts an adapter for a width or protocol mismatch
      today; mismatches simply fail to auto-match or are wired as-is.
    - ``constant_source`` — FORGE has no mechanism for declaring a
      non-zero constant source; only tie-to-zero (``tie_off``) exists.
    """
    id: str
    kind: str
    cycles: Optional[int] = None
    tag: Optional[str] = None


@dataclass
class RejectedCandidate:
    """A losing producer for a connection's consumer pin, sourced read-only
    from ``forge.contracts.matcher.MatchReport.rejected_fanin`` (never a
    second source of truth for it)."""
    producer: ResolvedEndpoint
    reason: str = "first-driver-wins: another producer connected first"


@dataclass
class CardinalityCheckResult:
    """The resolved cardinality bound an endpoint's interface declared,
    checked against how many connections actually exist. Descriptive only
    — ``forge.contracts.cardinality.verify_cardinality`` is what actually
    enforces this design-wide under ``--strict``."""
    bound: Dict[str, Any]  # {"min": ..., "max": ...}, same shape as ResolvedLogicalInterface.cardinality
    actual_count: int
    satisfied: bool


@dataclass
class MatchingEvidence:
    """Evidence for one connection: matching keys,
    coordinates, protocol, width, cardinality result, clock-domain result,
    gather/scatter pattern, and rejected candidates. Every field is
    ``Optional``/empty-default and left unset whenever the underlying
    contract role or bound doesn't exist for that side — the common case
    for handshake (``ap_*``) pins, clock/reset, and no-contract
    auto-matched connections. Never fabricated when the data isn't there.

    ``producer``/``consumer``/``wiring_method`` are **not** duplicated here
    — they already live on the owning ``ResolvedConnection``.
    """
    producer_wiring_kind: Optional[str] = None
    consumer_wiring_kind: Optional[str] = None
    producer_coordinates: Optional[Dict[str, Any]] = None
    consumer_coordinates: Optional[Dict[str, Any]] = None
    producer_protocol: Optional[str] = None
    consumer_protocol: Optional[str] = None
    # Deliberately not collapsed into one `width` field — no width_adapter
    # exists (documented reserved), so a producer/
    # consumer width mismatch is a real, currently-silent fact this
    # evidence should surface, not hide.
    producer_width: Optional[int] = None
    consumer_width: Optional[int] = None
    producer_cardinality: Optional[CardinalityCheckResult] = None
    consumer_cardinality: Optional[CardinalityCheckResult] = None
    gather_scatter_pattern: Optional[str] = None  # 'scatter' | 'gather' | None
    # The connection's declared Connection.cdc.kind, if any — None even
    # when a crossing exists but nothing was declared (that's what
    # ResolvedConnection.crosses_clock_domain / verify_cdc's error signal).
    cdc_declared: Optional[str] = None
    rejected_candidates: List[RejectedCandidate] = field(default_factory=list)


@dataclass
class ResolvedConnection:
    """A resolved wire between two endpoints.

    ``wiring_method`` (one of ``contract_wiring``/``port_map_ranges``/
    ``port_map``/``auto_match``/``topology_group``/``heuristic``) is
    populated from ``forge.contracts.matcher.MatchReport.connection_evidence``.

    ``matching_evidence`` (``MatchingEvidence``) is
    populated in ``forge/ir/build.py`` and carries coordinates/protocol/
    width/cardinality-result/clock-domain-result/gather-scatter-pattern/
    rejected-candidates — ``None`` whenever the connection has no
    contract-role-level detail to report (e.g. a handshake pin).

    ``emission_order`` records the connection's position in the *original*
    ``conn_map``/``global_nets`` construction order (before the stored list
    is sorted by ``id`` for reproducible hashing/diffing/visualization).
    It exists solely so ``forge.ir.project.project_to_conn_map`` can
    reproduce the exact legacy iteration order generators rely on for
    naming (e.g. ``reg_stage_N``/``delay_N`` instance counters). Nothing
    about ID-based sort order, hashing, or diffing changes.

    ``crosses_clock_domain``/``crosses_reset_domain`` are
    descriptive only — computed from the two endpoints' resolved domains
    (``ResolvedInstance.clock_domain``/``.reset_domain``) whenever both are
    known and differ. They report a fact; they don't enforce anything —
    ``forge.contracts.cdc.verify_cdc`` is what actually rejects an
    undeclared crossing under ``--strict``.
    """
    id: str
    producer: ResolvedEndpoint
    consumer: ResolvedEndpoint
    wiring_method: Optional[str] = None
    transformations: List[ResolvedTransformation] = field(default_factory=list)
    emission_order: int = 0
    crosses_clock_domain: bool = False
    crosses_reset_domain: bool = False
    matching_evidence: Optional[MatchingEvidence] = None


@dataclass
class ResolvedClockDomain:
    """A named clock domain — one per distinct resolved clock net.

    ``name`` is the resolved net's raw port name (e.g.
    ``"ap_clk"``), not a hardcoded literal. ``instances`` is *derived* from
    each ``ResolvedInstance.clock_domain`` (the per-instance scalar is the
    source of truth — this list is a grouping view of it, not maintained
    independently). Instances whose clock could not be resolved (or whose
    module is clock-free) do not appear in any domain here.

    ``derived_from``/``ratio`` are purely descriptive,
    populated from ``design.yml``'s optional ``clock_domains:`` block
    (``forge.contracts.config.DesignConfig.clock_domains``) when the design
    documents a relationship to another domain. They do **not** auto-
    approve a crossing between related domains — every crossing still
    needs an explicit per-connection ``cdc:`` declaration, checked by
    ``forge.contracts.cdc.verify_cdc``.
    """
    name: str = "default"
    instances: List[str] = field(default_factory=list)
    derived_from: Optional[str] = None
    ratio: Optional[int] = None


@dataclass
class ResolvedResetDomain:
    """A named reset domain — see ``ResolvedClockDomain``.

    ``sync`` is populated from ``reset_domains.<name>.sync``
    (currently only ``"reset_sync"`` is supported) — unlike
    ``derived_from``/``ratio``, this field DOES trigger real RTL
    generation: a ``cdc_reset_sync`` instance is emitted for this domain
    by the structural generator. See ``forge.contracts.cdc``'s module
    docstring for why reset synchronization is a domain property, not a
    ``Connection.cdc`` declaration.
    """
    name: str = "default"
    instances: List[str] = field(default_factory=list)
    derived_from: Optional[str] = None
    ratio: Optional[int] = None
    sync: Optional[str] = None
    # A real 'reset_synchronizer' ResolvedTransformation when sync is set
    # — a domain-level transformation, unlike every
    # other kind in ResolvedTransformation's vocabulary, which is keyed by
    # a connection pair; a reset crossing has no connection to attach to.
    transformations: List[ResolvedTransformation] = field(default_factory=list)


@dataclass
class ResolvedTopLevelPort:
    """A resolved top-level (e.g. ``algo_top``) port — name, direction
    (``in``/``out``, matching ``forge.core.utils.hdl_parser._scan_verilog_ports``'s
    convention), and width.

    Populated from ``write_structural_verilog``'s ``report["top_ports"]``
    *after* generation runs, since
    the top-level port list is a result of that generator's clock/reset/
    control-signal/global-net/external-port lifting logic, not a
    pre-generation design fact. ``assemble_project_ir`` therefore leaves
    this empty — ``forge inspect`` never runs the generator, so it has no
    way to know these ports; only ``topgen gen-top`` (verilog mode)
    attaches them to the IR it already built, right before emitting
    ``design.ir.json``. This intentionally avoids reimplementing the
    lifting logic a second time in the IR builder — there remains exactly
    one implementation, in the generator.
    """
    name: str
    direction: str
    width: int


@dataclass
class ResolvedVerificationPlan:
    """Placeholder for verification planning/bindings.

    Deliberately unpopulated in this slice — ``populated`` is always
    ``False`` here so consumers can tell "not yet migrated" apart from
    "migrated and genuinely empty."
    """
    populated: bool = False
    note: str = "Verification planning is not yet migrated to the canonical IR."


@dataclass
class ResolvedDesign:
    """The resolved design: modules, instances, connections, domains,
    diagnostics — everything derivable from ``design.yml``/``modules.yml``/
    interface contracts without touching generator- or verify-specific
    logic."""
    name: str
    modules: List[ResolvedModuleDefinition] = field(default_factory=list)
    instances: List[ResolvedInstance] = field(default_factory=list)
    connections: List[ResolvedConnection] = field(default_factory=list)
    clock_domains: List[ResolvedClockDomain] = field(default_factory=list)
    reset_domains: List[ResolvedResetDomain] = field(default_factory=list)
    top_ports: List[ResolvedTopLevelPort] = field(default_factory=list)
    verification_plan: ResolvedVerificationPlan = field(default_factory=ResolvedVerificationPlan)
    diagnostics: List[DiagnosticReference] = field(default_factory=list)
    source: Optional[SourceLocation] = None


@dataclass
class ResolvedProject:
    """Top-level IR object returned by ``forge.ir.build.build_project_ir``."""
    design: ResolvedDesign
    schema_version: str = IR_SCHEMA_VERSION
    forge_version: str = ""
    generated_from: Dict[str, Optional[str]] = field(default_factory=dict)
