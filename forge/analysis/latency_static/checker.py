"""forge.analysis.latency_static.checker
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Detect latency mismatches at merge points in the pipeline DAG.

A *merge point* is any node that has more than one distinct predecessor.
By default, all inputs arriving at a merge point are expected to have
accumulated the same total latency; otherwise the pipeline will sample
stale data on one or more paths. This is not an unconditional assumption:
a merge point's *alignment requirement* is inferred from its predecessors'
declared timing kinds (fixed/bounded/elastic) and, best-effort,
the consuming interface's protocol — see :func:`check_merge_points`'s
``alignment`` field on :class:`MismatchReport`.

For an ``exact_cycle`` mismatch the checker emits a suggested
``signal_delay`` insertion with the required depth.

Each path's accumulated latency
includes the connecting edge's own latency (``register_stages``/
``delay_cycles``/a known-depth CDC synchronizer) in addition to the
predecessor node's latency — previously the edge contributed nothing at
all, so the checker was blind to any latency FORGE itself inserts on a
connection.

``LatencyGraph`` nodes are per-instance (not
per-module-group) — a real multi-instance fan-in (e.g. trigger_demo's
``dec`` x4 -> ``col``) now produces a genuine multi-predecessor merge
point here, where before it collapsed to a single edge and could never
be checked at all.
"""
from __future__ import annotations

import dataclasses
from typing import Dict, List, Optional, Set

from forge.analysis.latency_model import LatencyProvenance
from forge.analysis.latency_static.graph import LatencyGraph


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class PathLatency:
    """Latency accumulated along a single input path to a merge node."""
    path: List[str]               # node names from immediate predecessor to merge
    total_cycles: Optional[int]   # None when any node on the path is unknown
    has_unknown: bool = False
    # The sum of a node's latency and its connecting edge's latency is
    # itself a computed value, not sourced from any single place —
    # "inferred" is the correct provenance vocabulary entry for it
    # (forge.analysis.latency_model.LATENCY_SOURCES).
    provenance: Optional[LatencyProvenance] = None
    # The [lo, hi] cycle range this
    # path contributes, populated whenever total_cycles is known — a
    # degenerate [v, v] point range for a fixed/hint/hls_report
    # predecessor, or the predecessor's real [min, max] (+ edge cycles)
    # for a declared `bounded` one. Used by bounded_skew alignment's
    # overlap check; None/None when total_cycles is None (unknown).
    range_lo: Optional[int] = None
    range_hi: Optional[int] = None


@dataclasses.dataclass
class MismatchReport:
    merge_node: str
    paths: List[PathLatency]
    max_latency: Optional[int]
    min_latency: Optional[int]
    delta: Optional[int]          # max − min  (0 = balanced) — exact_cycle alignment only
    suggestion: Optional[str]     # e.g. "Insert signal_delay DEPTH=4 on …"
    # The alignment requirement this merge point was
    # inferred to have — "the checker must not assume every reconvergence
    # requires identical scalar latency". One of:
    #   exact_cycle       — every predecessor is fixed/hint/hls_report;
    #                        default, byte-identical to pre-slice-3 behavior.
    #   bounded_skew      — at least one predecessor declares `kind: bounded`;
    #                        mismatch iff the paths' cycle ranges don't overlap.
    #   elastic_buffer    — at least one predecessor declares `kind: elastic`;
    #                        never a mismatch — tolerant by definition.
    #   transaction_order — best-effort: the merge node's consuming interface
    #                        is `protocol: ready-valid` (only when the caller
    #                        supplies `consumer_protocols`); never a scalar
    #                        latency mismatch — ready/valid handshaking
    #                        doesn't require cycle-exact producer alignment.
    alignment: str = "exact_cycle"

    @property
    def is_mismatch(self) -> bool:
        if self.alignment in ("elastic_buffer", "transaction_order"):
            return False
        if self.alignment == "bounded_skew":
            ranges = [
                (p.range_lo, p.range_hi) for p in self.paths
                if p.range_lo is not None and p.range_hi is not None
            ]
            if len(ranges) < 2:
                return False
            lo_max = max(r[0] for r in ranges)
            hi_min = min(r[1] for r in ranges)
            return lo_max > hi_min
        return self.delta is not None and self.delta > 0

    @property
    def has_unknowns(self) -> bool:
        return any(p.has_unknown for p in self.paths)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _upstream_chain_latency(graph: LatencyGraph, name: str) -> "tuple[Optional[int], bool]":
    """Cumulative (cycles, is_unknown) from *name* back through any
    straight (single-predecessor) chain of fixed-ish nodes above it.

    Before this, a path's latency was just its *immediate* predecessor's
    own node latency + connecting edge — correct for a direct producer,
    silently wrong for a multi-hop branch (e.g. vision_pipeline_demo's
    ``window_builder_rtl -> sobel_hls -> merge``: the merge point only
    ever saw ``sobel_hls``'s own 4 cycles, never ``window_builder_rtl``'s
    10, understating that branch's real total by exactly the missing
    hop). Found by wiring a genuine 2-hop branch into a real merge point
    for the first time — every existing test/reference-design merge
    point is single-hop, so this gap had no test surface before.

    Walks backward through nodes with a single *real* predecessor
    (a predecessor reached via an
    ``unknown_cdc`` edge — mailbox_transfer/async_fifo — doesn't count
    toward this "single predecessor" test at all, since it's a legitimate
    async side-channel exempt from exact-cycle alignment, e.g. a
    frame-boundary-gated configuration register, not a second data path
    requiring reconciliation; a node with one real data predecessor plus
    one such async predecessor still has a fully well-defined, foldable
    upstream chain through its real side) whose own ``kind`` is fixed-ish
    (``None``/``fixed``/``hint``/``hls_report`` — never ``bounded``/
    ``elastic``, whose latency isn't a fixed scalar to begin with, and
    stopping there is conservative, not a regression: neither reference
    design uses those kinds mid-chain today). Stops (returns just
    *name*'s own contribution) at a true source (no real predecessors).

    A real fan-in node (>=2 real predecessors) is itself a merge point,
    independently checked by the caller's own (``check_merge_points``)
    loop — but is only treated as an opaque, non-folded-through stop
    when it *isn't* confirmed balanced. When every one of its real
    predecessors resolves to the exact same known total, the merge is
    confirmed aligned, and that agreed total (plus this node's own
    latency) is exactly the branch's real accumulated value from here
    downward — using just this node's own scalar latency instead would
    silently discard the confirmed-real upstream total, understating the
    branch same way the pre-fix single-hop case did. Found on a real
    external consumer's topology: a genuinely balanced concentrator
    merge point (two real predecessors both totalling 11) was reporting
    "0" to every node downstream of it, turning a real ~2-cycle
    discrepancy further downstream into an inflated, misleading ~13.
    Any unknown or genuinely mismatched real predecessor keeps the
    conservative, own-latency-only behavior unchanged, so
    ``check_merge_points`` still independently flags the real issue at
    the merge point itself rather than this function silently picking a
    branch to trust.
    """
    node = graph.nodes[name]
    own_cycles = node.latency_cycles
    own_unknown = (own_cycles is None) or node.is_variable
    own_kind = node.latency.kind if node.latency else None

    preds = graph.predecessors(name)
    edges_by_pred = {e.src: e for e in graph.edges if e.dst == name}
    # Same exclusion check_merge_points applies to its own top-level
    # predecessor count — a control_strobe edge is not a data path
    # predecessor for chain-folding purposes either.
    real_preds = [
        p for p in preds
        if not (edges_by_pred.get(p) and (edges_by_pred[p].unknown_cdc or edges_by_pred[p].control_strobe))
    ]

    if own_unknown or own_kind in ("bounded", "elastic"):
        return (None if own_unknown else own_cycles, own_unknown)

    if len(real_preds) == 0:
        return (own_cycles, False)

    if len(real_preds) >= 2:
        totals = []
        for p in real_preds:
            p_edge = edges_by_pred.get(p)
            p_edge_cycles = p_edge.latency.cycles if (p_edge and p_edge.latency and p_edge.latency.cycles) else 0
            p_upstream, p_unknown = _branch_upstream(graph, p, p_edge)
            if p_unknown:
                return (own_cycles, False)  # can't confirm balance — stay conservative
            totals.append(p_upstream + p_edge_cycles)
        if len(set(totals)) == 1:
            return (totals[0] + own_cycles, False)  # confirmed balanced — fold through
        return (own_cycles, False)  # genuinely mismatched — stay conservative

    pred = real_preds[0]
    pred_node = graph.nodes.get(pred)
    if pred_node is not None and pred_node.is_variable:
        # An *explicitly* variable/elastic predecessor (e.g. an async
        # config tap feeding this node as a side input alongside its real
        # data path — cfg64_from_framework-style modules declared
        # ``variable_latency: true``) must not poison this node's own,
        # separately-known, fixed latency: this node's own declared/HLS-
        # report value already fully describes its own valid-in-to-
        # valid-out behaviour regardless of when that async signal
        # arrives. Found on a real external consumer's topology: dt_interface/
        # csc_interface instances each have exactly one graph
        # predecessor — their config tap, not their true (unmodelled,
        # top-level-external) data input — which was silently turning
        # every merge point downstream of them ``unknown`` despite dt/csc
        # both having real, known HLS-synthesised latencies.
        #
        # Deliberately narrower than "any predecessor without a usable
        # cycle count": a predecessor with no declared latency at all
        # (own_cycles is None, is_variable False — data genuinely missing,
        # not an architectural fact) still falls through to the unknown-
        # propagating path below unchanged. Only a *declared* variable/
        # elastic predecessor earns this treatment — the same "explicit
        # is a real, deliberate architectural fact worth trusting"
        # precedent this function already applies to unknown_cdc edges
        # and to this node's own bounded/elastic kind, above.
        return (own_cycles, False)

    edge = edges_by_pred.get(pred)
    edge_cycles = edge.latency.cycles if (edge and edge.latency and edge.latency.cycles) else 0

    upstream_cycles, upstream_unknown = _branch_upstream(graph, pred, edge)
    if upstream_unknown:
        return (None, True)
    return (upstream_cycles + edge_cycles + own_cycles, False)


def _branch_upstream(graph: LatencyGraph, pred: str, edge) -> "tuple[Optional[int], bool]":
    """Upstream total to charge a branch arriving from *pred* over *edge*.

    Normally the full folded chain above *pred*. When *edge* is
    ``external_source`` the data entered the design at *pred* (through one of
    its ``external_in_ports``) instead of flowing through it, so only *pred*'s
    own latency applies — folding its predecessors in would bill this branch
    for a path it never travelled.
    """
    if edge is not None and getattr(edge, "external_source", False):
        pred_node = graph.nodes.get(pred)
        if pred_node is None:
            return (None, True)
        own = pred_node.latency_cycles
        if own is None or pred_node.is_variable:
            return (None, True)
        return (own, False)
    return _upstream_chain_latency(graph, pred)


def check_merge_points(
    graph: LatencyGraph,
    *,
    consumer_protocols: Optional[Dict[str, str]] = None,
) -> List[MismatchReport]:
    """Return one :class:`MismatchReport` per merge node in *graph*.

    Only nodes with ≥ 2 predecessors are analysed.  Nodes with exactly one
    predecessor (or none) are skipped — they cannot produce mismatches.

    ``consumer_protocols``: an optional ``{node_name: protocol}`` mapping
    (e.g. sourced from ``ResolvedLogicalInterface.protocol``) enabling
    best-effort ``transaction_order`` alignment inference for a merge
    node whose consuming interface is ``ready-valid``. Omitted by
    default — this function keeps working from a bare ``LatencyGraph``
    alone, the same "usable before synthesis" property
    ``forge.analysis.latency_static.graph`` itself preserves.
    """
    consumer_protocols = consumer_protocols or {}
    reports: List[MismatchReport] = []
    edge_by_pair = {(e.src, e.dst): e for e in graph.edges}

    for node_name in graph.nodes:
        # A control_strobe predecessor (Connection.control_strobe — a
        # reset/swap/output-stamp pulse, not a data path) is excluded
        # entirely from merge-point detection, not merely marked
        # "unknown" the way an unknown_cdc predecessor is: its timing is
        # deliberately scheduled by the receiving design, not a second
        # data source requiring exact-cycle reconciliation against a
        # sibling data path. A node fed by exactly one real data
        # predecessor plus a control strobe is therefore not a merge
        # point at all. See Connection.control_strobe's own docstring
        # for the real external-consumer example (bx_timing's new_event_*
        # strobes) this was found needed for.
        preds = [
            p for p in graph.predecessors(node_name)
            if not (edge_by_pair.get((p, node_name)) and edge_by_pair[(p, node_name)].control_strobe)
        ]
        if len(preds) < 2:
            continue

        path_lats: List[PathLatency] = []
        kinds: Set[str] = set()
        for src in sorted(preds):
            src_node = graph.nodes[src]
            edge = edge_by_pair.get((src, node_name))
            node_cycles, unknown = _branch_upstream(graph, src, edge)

            if edge and edge.unknown_cdc:
                # This path's own final hop is a mailbox_transfer/async_fifo
                # crossing into the merge node itself — genuinely unbounded
                # (LatencyEdge.unknown_cdc's own docstring), regardless of
                # how src's own upstream chain resolved.
                unknown = True
            edge_cycles = edge.latency.cycles if (edge and edge.latency and edge.latency.cycles) else 0

            kind = src_node.latency.kind if src_node.latency else None
            if kind:
                kinds.add(kind)

            total = (node_cycles + edge_cycles) if not unknown else None

            range_lo: Optional[int] = None
            range_hi: Optional[int] = None
            if kind == "bounded" and src_node.latency and src_node.latency.min_cycles is not None \
                    and src_node.latency.max_cycles is not None:
                range_lo = src_node.latency.min_cycles + edge_cycles
                range_hi = src_node.latency.max_cycles + edge_cycles
            elif total is not None:
                range_lo = range_hi = total

            provenance = (
                LatencyProvenance(
                    "inferred",
                    detail=f"{src_node.latency_source}:{node_cycles} + edge:{edge_cycles}",
                )
                if total is not None else None
            )
            path_lats.append(PathLatency(
                path=[src, node_name],
                total_cycles=total,
                has_unknown=unknown,
                provenance=provenance,
                range_lo=range_lo,
                range_hi=range_hi,
            ))

        if "elastic" in kinds:
            alignment = "elastic_buffer"
        elif "bounded" in kinds:
            alignment = "bounded_skew"
        elif consumer_protocols.get(node_name) == "ready-valid":
            alignment = "transaction_order"
        else:
            alignment = "exact_cycle"

        known = [p.total_cycles for p in path_lats if p.total_cycles is not None]
        if not known:
            max_lat = min_lat = delta = None
        else:
            max_lat = max(known)
            min_lat = min(known)
            delta = max_lat - min_lat

        suggestion: Optional[str] = None
        if alignment == "exact_cycle" and delta and delta > 0:
            for p in path_lats:
                if p.total_cycles == min_lat and p.total_cycles is not None:
                    short_src = p.path[0]
                    suggestion = (
                        f"Insert signal_delay DEPTH={delta} on the path from "
                        f"'{short_src}' into '{node_name}'"
                    )
                    break

        reports.append(MismatchReport(
            merge_node=node_name,
            paths=path_lats,
            max_latency=max_lat,
            min_latency=min_lat,
            delta=delta,
            suggestion=suggestion,
            alignment=alignment,
        ))

    return reports
