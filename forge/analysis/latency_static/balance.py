"""forge.analysis.latency_static.balance — resolve ``delay_cycles: auto``.

A connection or topology group declared with ``delay_cycles: auto`` gets the
delay that lines its data up at the merge point it feeds, computed from the
same latency graph and merge semantics as ``forge analyze latency-check``:
module latencies (``latency_cycles``, then the HLS synthesis reports, then
``latency_hint``), the register stages and delays already on the edges, and the
merge node's ``input_alignment`` (per-source offsets, ``window``, ``framed``).
A design with ``auto`` delays therefore follows every re-synthesised module
without anyone editing a depth.

Merge points are resolved in topological order and every resolved delay is
written into its edge, so the delay chosen at one merge point is part of every
path below it. Each auto input is delayed to the latest arrival (after offsets)
at its merge point; fixed inputs are never changed, so a fixed input that
arrives late is reported, not hidden.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from forge.analysis.latency_model import LatencyProvenance, LatencyValue
from forge.analysis.latency_static.checker import analyse_merge_point
from forge.analysis.latency_static.graph import LatencyEdge, LatencyGraph, build_graph

Ref = Tuple[str, int]  # ("connection" | "topology_group", index in DesignConfig)


@dataclasses.dataclass
class AutoDelay:
    kind: str          # "connection" | "topology_group"
    index: int
    cycles: int
    src: str           # an instance the delay was resolved on
    dst: str


@dataclasses.dataclass
class AutoDelayResult:
    delays: List[AutoDelay]
    problems: List[str]

    def by_ref(self) -> Dict[Ref, int]:
        return {(d.kind, d.index): d.cycles for d in self.delays}


def _data_edges(graph: LatencyGraph) -> List[LatencyEdge]:
    return [e for e in graph.edges if not e.control_strobe]


def _topological_order(graph: LatencyGraph) -> Tuple[List[str], List[str]]:
    """(order, nodes on a cycle) over the data edges."""
    edges = _data_edges(graph)
    indegree = {n: 0 for n in graph.nodes}
    succ: Dict[str, List[str]] = {n: [] for n in graph.nodes}
    for e in edges:
        if e.src in indegree and e.dst in indegree:
            indegree[e.dst] += 1
            succ[e.src].append(e.dst)
    ready = sorted(n for n, d in indegree.items() if d == 0)
    order: List[str] = []
    while ready:
        n = ready.pop(0)
        order.append(n)
        for m in succ[n]:
            indegree[m] -= 1
            if indegree[m] == 0:
                ready.append(m)
    on_cycle = sorted(n for n, d in indegree.items() if d > 0)
    return order, on_cycle


def _set_auto_cycles(edge: LatencyEdge, extra: int) -> None:
    base = edge.latency.cycles if (edge.latency and edge.latency.cycles) else 0
    detail = f"delay_cycles=auto ({extra})"
    if edge.latency and edge.latency.provenance and edge.latency.provenance.detail:
        detail = f"{edge.latency.provenance.detail}; {detail}"
    edge.latency = LatencyValue(
        cycles=base + extra,
        provenance=LatencyProvenance("generated_transformation", detail=detail),
    )


def resolve_auto_delays(graph: LatencyGraph) -> AutoDelayResult:
    """Resolve every auto edge of *graph* in place; see the module docstring."""
    problems: List[str] = []
    resolved: Dict[Ref, Dict[int, Tuple[str, str]]] = {}
    order, on_cycle = _topological_order(graph)
    edge_by_pair = {(e.src, e.dst): e for e in graph.edges}

    for node in on_cycle:
        if any(e.auto for e in graph.edges if e.dst == node and not e.control_strobe):
            problems.append(f"'{node}' is on a feedback loop; delay_cycles: auto into it cannot be resolved")

    for node in order:
        auto_in = [e for e in _data_edges(graph) if e.dst == node and e.auto]
        if not auto_in:
            continue

        mixed = [e for e in auto_in if not all(is_auto for _, _, is_auto in e.refs)]
        for e in mixed:
            problems.append(
                f"the connections from '{e.src}' into '{e.dst}' mix delay_cycles: auto with a fixed "
                "delay; declare them all auto or all fixed"
            )
        if mixed:
            continue

        report = analyse_merge_point(graph, node, edge_by_pair)
        extra: Dict[str, int] = {}
        if report is None or report.alignment in ("framed", "elastic_buffer", "transaction_order"):
            # One data input, or inputs that need no cycle relation.
            extra = {e.src: 0 for e in auto_in}
        else:
            unknown = [p.path[0] for p in report.paths if p.total_cycles is None]
            if unknown:
                problems.append(
                    f"cannot resolve delay_cycles: auto into '{node}': the latency of the path from "
                    f"{', '.join(repr(u) for u in sorted(unknown))} is unknown (declare latency_cycles "
                    "or give the HLS build root)"
                )
                continue
            target = max(p.range_hi if p.range_hi is not None else p.total_cycles for p in report.paths)
            arrival = {p.path[0]: p.total_cycles for p in report.paths}
            extra = {e.src: target - arrival[e.src] for e in auto_in}

        for e in auto_in:
            _set_auto_cycles(e, extra[e.src])
            for kind, index, _ in e.refs:
                resolved.setdefault((kind, index), {})[extra[e.src]] = (e.src, e.dst)

    delays: List[AutoDelay] = []
    for (kind, index), values in sorted(resolved.items()):
        if len(values) > 1:
            detail = ", ".join(f"{c} ({s} -> {d})" for c, (s, d) in sorted(values.items()))
            problems.append(
                f"{kind} {index} needs a different delay on different instances: {detail}; split it "
                "into connections whose instances line up the same way"
            )
            continue
        (cycles, (src, dst)), = values.items()
        delays.append(AutoDelay(kind=kind, index=index, cycles=cycles, src=src, dst=dst))
    return AutoDelayResult(delays=delays, problems=problems)


def resolve_design_auto_delays(
    design_path: Path,
    hls_build_root: Optional[Path] = None,
    conn_map: "Optional[Dict[tuple, list]]" = None,
) -> AutoDelayResult:
    """Build the latency graph of *design_path* (HLS report latencies from
    *hls_build_root* when it exists) and resolve its auto delays."""
    hls_reports = None
    if hls_build_root is not None and Path(hls_build_root).is_dir():
        from forge.analysis.hls_reports.extractor import collect_reports, latency_map_from_reports
        hls_reports = latency_map_from_reports(collect_reports(Path(hls_build_root)))
    graph = build_graph(Path(design_path), hls_reports=hls_reports, conn_map=conn_map)
    return resolve_auto_delays(graph)


def apply_auto_delays(cfg, result: AutoDelayResult) -> None:
    """Write the resolved depths into *cfg*'s connections and topology groups
    (DesignConfig indices, the same file the graph was built from)."""
    for d in result.delays:
        target = cfg.connections[d.index] if d.kind == "connection" else cfg.topology_groups[d.index]
        if not target.delay_auto:
            raise ValueError(f"{d.kind} {d.index} is not declared delay_cycles: auto")
        target.delay_cycles = d.cycles
