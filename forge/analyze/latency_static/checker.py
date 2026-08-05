"""forge.analyze.latency_static.checker
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Detect latency mismatches at merge points in the pipeline DAG.

A *merge point* is any node that has more than one distinct predecessor.
By default, all inputs arriving at a merge point are expected to have
accumulated the same total latency; otherwise the pipeline will sample
stale data on one or more paths. As of Phase 4 slice 3 (release-plan
§4.3), this is no longer an unconditional assumption: a merge point's
*alignment requirement* is inferred from its predecessors' declared
timing kinds (fixed/bounded/elastic — Phase 4 slice 2) and, best-effort,
the consuming interface's protocol — see :func:`check_merge_points`'s
``alignment`` field on :class:`MismatchReport`.

For an ``exact_cycle`` mismatch the checker emits a suggested
``signal_delay`` insertion with the required depth.

As of Phase 4 slice 1 (release-plan §4.1), each path's accumulated latency
includes the connecting edge's own latency (``register_stages``/
``delay_cycles``/a known-depth CDC synchronizer) in addition to the
predecessor node's latency — previously the edge contributed nothing at
all, so the checker was blind to any latency FORGE itself inserts on a
connection.

As of Phase 4 slice 3, ``LatencyGraph`` nodes are per-instance (not
per-module-group) — a real multi-instance fan-in (e.g. trigger_demo's
``dec`` x4 -> ``col``) now produces a genuine multi-predecessor merge
point here, where before it collapsed to a single edge and could never
be checked at all.
"""
from __future__ import annotations

import dataclasses
from typing import Dict, List, Optional, Set

from forge.analyze.latency_model import LatencyProvenance
from forge.analyze.latency_static.graph import LatencyGraph


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
    # (forge.analyze.latency_model.LATENCY_SOURCES).
    provenance: Optional[LatencyProvenance] = None
    # release-plan §4.3 (Phase 4 slice 3): the [lo, hi] cycle range this
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
    # release-plan §4.3: the alignment requirement this merge point was
    # inferred to have — "the checker must not assume every reconvergence
    # requires identical scalar latency" (§4.3's own wording). One of:
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
    straight (single-predecessor) chain of fixed-ish nodes above it —
    release-plan Phase 10, slice 10.2 finding.

    Before this, a path's latency was just its *immediate* predecessor's
    own node latency + connecting edge — correct for a direct producer,
    silently wrong for a multi-hop branch (e.g. vision_pipeline_demo's
    ``window_builder_rtl -> sobel_hls -> merge``: the merge point only
    ever saw ``sobel_hls``'s own 4 cycles, never ``window_builder_rtl``'s
    10, understating that branch's real total by exactly the missing
    hop). Found by wiring a genuine 2-hop branch into a real merge point
    for the first time — every existing test/reference-design merge
    point is single-hop, so this gap had no test surface before.

    Walks backward only through nodes with a single *real* predecessor
    (release-plan Phase 10, slice 10.7B: a predecessor reached via an
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
    *name*'s own contribution) at a true source (no real predecessors) or
    a real fan-in node (>=2 real predecessors) — the latter is itself a
    merge point, independently checked by the caller's own loop, not
    something to fold through as if its alignment were already verified.
    """
    node = graph.nodes[name]
    own_cycles = node.latency_cycles
    own_unknown = (own_cycles is None) or node.is_variable
    own_kind = node.latency.kind if node.latency else None

    preds = graph.predecessors(name)
    edges_by_pred = {e.src: e for e in graph.edges if e.dst == name}
    real_preds = [p for p in preds if not (edges_by_pred.get(p) and edges_by_pred[p].unknown_cdc)]

    if len(real_preds) != 1 or own_unknown or own_kind in ("bounded", "elastic"):
        return (None if own_unknown else own_cycles, own_unknown)

    pred = real_preds[0]
    edge = edges_by_pred.get(pred)
    edge_cycles = edge.latency.cycles if (edge and edge.latency and edge.latency.cycles) else 0

    upstream_cycles, upstream_unknown = _upstream_chain_latency(graph, pred)
    if upstream_unknown:
        return (None, True)
    return (upstream_cycles + edge_cycles + own_cycles, False)


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
    ``forge.analyze.latency_static.graph`` itself preserves.
    """
    consumer_protocols = consumer_protocols or {}
    reports: List[MismatchReport] = []
    edge_by_pair = {(e.src, e.dst): e for e in graph.edges}

    for node_name in graph.nodes:
        preds = graph.predecessors(node_name)
        if len(preds) < 2:
            continue

        path_lats: List[PathLatency] = []
        kinds: Set[str] = set()
        for src in sorted(preds):
            src_node = graph.nodes[src]
            node_cycles, unknown = _upstream_chain_latency(graph, src)

            edge = edge_by_pair.get((src, node_name))
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
