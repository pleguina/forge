"""arc.analyze.latency_static.checker
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Detect latency mismatches at merge points in the pipeline DAG.

A *merge point* is any node that has more than one distinct predecessor.
All inputs arriving at a merge point must have accumulated the same total
latency; otherwise the pipeline will sample stale data on one or more paths.

For each mismatch the checker emits a suggested ``signal_delay`` insertion
with the required depth.
"""
from __future__ import annotations

import dataclasses
from typing import List, Optional

from arc.analyze.latency_static.graph import LatencyGraph


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class PathLatency:
    """Latency accumulated along a single input path to a merge node."""
    path: List[str]               # node names from immediate predecessor to merge
    total_cycles: Optional[int]   # None when any node on the path is unknown
    has_unknown: bool = False


@dataclasses.dataclass
class MismatchReport:
    merge_node: str
    paths: List[PathLatency]
    max_latency: Optional[int]
    min_latency: Optional[int]
    delta: Optional[int]          # max − min  (0 = balanced)
    suggestion: Optional[str]     # e.g. "Insert signal_delay DEPTH=4 on …"

    @property
    def is_mismatch(self) -> bool:
        return self.delta is not None and self.delta > 0

    @property
    def has_unknowns(self) -> bool:
        return any(p.has_unknown for p in self.paths)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_merge_points(graph: LatencyGraph) -> List[MismatchReport]:
    """Return one :class:`MismatchReport` per merge node in *graph*.

    Only nodes with ≥ 2 predecessors are analysed.  Nodes with exactly one
    predecessor (or none) are skipped — they cannot produce mismatches.
    """
    reports: List[MismatchReport] = []

    for node_name in graph.nodes:
        preds = graph.predecessors(node_name)
        if len(preds) < 2:
            continue

        path_lats: List[PathLatency] = []
        for src in sorted(preds):
            src_node = graph.nodes[src]
            lat = src_node.latency_cycles
            unknown = (lat is None) or src_node.is_variable
            path_lats.append(PathLatency(
                path=[src, node_name],
                total_cycles=lat if not unknown else None,
                has_unknown=unknown,
            ))

        known = [p.total_cycles for p in path_lats if p.total_cycles is not None]
        if not known:
            max_lat = min_lat = delta = None
        else:
            max_lat = max(known)
            min_lat = min(known)
            delta = max_lat - min_lat

        suggestion: Optional[str] = None
        if delta and delta > 0:
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
        ))

    return reports
