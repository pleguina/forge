"""
Tests for forge.analyze.latency_static.checker's edge-latency fold-in
(release-plan §4.1, Phase 4 slice 1).

Before this slice, LatencyEdge carried no latency at all, so
check_merge_points summed only each predecessor node's own latency — any
register_stages/delay_cycles/CDC latency FORGE inserted on the connecting
edge was silently invisible to mismatch detection. These tests prove the
fix directly: a merge point that would previously have been reported
"balanced" (because both predecessor *nodes* have equal latency) is now
correctly flagged once one predecessor's connecting *edge* carries extra
cycles the other doesn't.
"""

from __future__ import annotations

from analyze.latency_static.checker import check_merge_points
from analyze.latency_static.graph import LatencyEdge, LatencyGraph, LatencyNode
from analyze.latency_model import LatencyProvenance, LatencyValue


def _node(name, cycles, *, is_variable=False):
    return LatencyNode(
        name=name, ref=name, instances=1, kind="rtl",
        latency=LatencyValue(cycles=cycles, provenance=LatencyProvenance("explicit_contract")),
        is_variable=is_variable,
    )


def test_edge_latency_is_folded_into_the_path_total():
    """Two predecessors with EQUAL node latency, but one edge carries extra
    cycles (e.g. register_stages) the other doesn't — must now be flagged,
    where the pre-slice checker (blind to edge latency) would have missed it."""
    graph = LatencyGraph(
        nodes={
            "a": _node("a", 5),
            "b": _node("b", 5),
            "merge": _node("merge", 1),
        },
        edges=[
            LatencyEdge(src="a", dst="merge", latency=LatencyValue(
                cycles=2, provenance=LatencyProvenance("generated_transformation", detail="register_stages=2"),
            )),
            LatencyEdge(src="b", dst="merge", latency=None),
        ],
    )
    reports = check_merge_points(graph)
    assert len(reports) == 1
    r = reports[0]
    assert r.merge_node == "merge"
    assert r.is_mismatch
    assert r.delta == 2  # (5+2) vs (5+0)
    totals = {p.path[0]: p.total_cycles for p in r.paths}
    assert totals == {"a": 7, "b": 5}
    for p in r.paths:
        assert p.provenance is not None
        assert p.provenance.source == "inferred"


def test_equal_totals_including_edge_latency_stay_balanced():
    """Both predecessors have the same node+edge total (via different
    node/edge splits) — must NOT be flagged, proving the fold-in doesn't
    make the checker over-eager."""
    graph = LatencyGraph(
        nodes={
            "a": _node("a", 5),
            "b": _node("b", 3),
            "merge": _node("merge", 1),
        },
        edges=[
            LatencyEdge(src="a", dst="merge", latency=None),
            LatencyEdge(src="b", dst="merge", latency=LatencyValue(
                cycles=2, provenance=LatencyProvenance("generated_transformation", detail="delay_cycles=2"),
            )),
        ],
    )
    reports = check_merge_points(graph)
    assert len(reports) == 1
    assert not reports[0].is_mismatch
    assert reports[0].delta == 0


def test_unknown_edge_latency_defaults_to_zero_not_unknown():
    """An edge with no latency data (e.g. a topology-group-derived edge)
    contributes 0 cycles, not an unknown/None total — only the *node's*
    own latency can make a path unknown, matching pre-slice behavior for
    the node side exactly."""
    graph = LatencyGraph(
        nodes={
            "a": _node("a", 4),
            "b": _node("b", 4),
            "merge": _node("merge", 1),
        },
        edges=[
            LatencyEdge(src="a", dst="merge", latency=None),
            LatencyEdge(src="b", dst="merge", latency=None),
        ],
    )
    reports = check_merge_points(graph)
    assert len(reports) == 1
    assert not reports[0].is_mismatch
    assert not reports[0].has_unknowns


def test_real_reference_designs_report_zero_false_mismatches():
    """Real-design regression guard: neither reference design should
    report a *mismatch* as a side effect of folding edge latency in
    (Phase 4 slice 1) or per-instance graph granularity (Phase 4 slice
    3). passthrough_demo has zero merge points (single module). As of
    slice 3, trigger_demo now has 2 real merge points (col, partmon —
    each fed by all 4 real 'dec' instances, previously invisible when
    'dec' collapsed to one module-group node) — both correctly balanced,
    since every 'dec' instance shares the same module-level
    latency_hint: 0 and no register_stages/delay_cycles is declared on
    either topology group."""
    from pathlib import Path
    from analyze.latency_static.graph import build_graph

    repo_root = Path(__file__).resolve().parents[2]

    g_pt = build_graph(repo_root / "plugins/passthrough_demo/forge/designs/design.yml")
    assert check_merge_points(g_pt) == []

    g_td = build_graph(repo_root / "plugins/trigger_demo/forge/designs/design.yml")
    reports = check_merge_points(g_td)
    assert {r.merge_node for r in reports} == {"col", "partmon"}
    assert all(not r.is_mismatch for r in reports)
    assert all(not r.has_unknowns for r in reports)
    assert all(r.alignment == "exact_cycle" for r in reports)


# ─────────────────────────────────────────────────────────────────────────
# Release-plan Phase 10, slice 10.2 — multi-hop chain latency (found while
# wiring window_builder_rtl -> sobel_hls -> merge into a real merge point:
# the checker only ever summed the *direct* predecessor's own node latency,
# silently dropping window_builder_rtl's 10 cycles entirely since it sits
# one hop further back. Every merge point in both existing reference
# designs is single-hop, so this gap had no test surface until now.
# ─────────────────────────────────────────────────────────────────────────

def test_multi_hop_chain_latency_is_folded_into_the_path_total():
    """source -> mid -> merge (2 hops) must accumulate source+mid's own
    latencies, not just mid's -- the bug this slice found and fixed."""
    graph = LatencyGraph(
        nodes={
            "source": _node("source", 3),
            "mid": _node("mid", 10),
            "direct": _node("direct", 5),
            "merge": _node("merge", 1),
        },
        edges=[
            LatencyEdge(src="source", dst="mid", latency=None),
            LatencyEdge(src="mid", dst="merge", latency=None),
            LatencyEdge(src="direct", dst="merge", latency=None),
        ],
    )
    reports = check_merge_points(graph)
    assert len(reports) == 1
    r = reports[0]
    totals = {p.path[0]: p.total_cycles for p in r.paths}
    # "mid"'s path must include "source"'s 3 cycles too (3+10=13), not just
    # mid's own 10 -- and "direct" has no chain behind it, so it's just 5.
    assert totals == {"mid": 13, "direct": 5}
    assert r.is_mismatch
    assert r.delta == 8


def test_multi_hop_chain_stops_at_a_bounded_or_elastic_node():
    """A bounded/elastic node mid-chain isn't a fixed scalar to begin
    with -- the chain-walk must stop there (treat it as an opaque,
    unknown boundary) rather than silently summing through it."""
    graph = LatencyGraph(
        nodes={
            "source": _node("source", 3),
            "mid": _node_with_kind("mid", "elastic", is_variable=True),
            "merge": _node("merge", 1),
            "other": _node("other", 5),
        },
        edges=[
            LatencyEdge(src="source", dst="mid", latency=None),
            LatencyEdge(src="mid", dst="merge", latency=None),
            LatencyEdge(src="other", dst="merge", latency=None),
        ],
    )
    reports = check_merge_points(graph)
    assert len(reports) == 1
    assert reports[0].alignment == "elastic_buffer"
    assert not reports[0].is_mismatch  # elastic predecessor is always tolerant


def test_vision_pipeline_demo_pixel_result_design_has_zero_merge_skew():
    """Real-design proof (release-plan Phase 10, slice 10.2): the Sobel
    branch (window_builder_rtl 10 cycles + sobel_hls 4 cycles, 2 hops
    into `merge`) and the threshold branch (threshold_rtl 2 cycles + a
    12-cycle Connection.delay_cycles alignment delay) must land on
    edge_mask_merge_rtl at the exact same cycle -- the whole point of
    slice 10.2's design. Before this slice's chain-walk fix, the Sobel
    path would have been silently under-reported as 4 cycles (just
    sobel_hls's own latency), producing a false "balanced" or a bogus
    delta depending on what the threshold branch happened to total."""
    from pathlib import Path
    from analyze.latency_static.graph import build_graph

    repo_root = Path(__file__).resolve().parents[2]
    g = build_graph(
        repo_root / "plugins/vision_pipeline_demo/forge/designs/design_pixel_result.yml"
    )
    reports = check_merge_points(g)
    assert len(reports) == 1
    r = reports[0]
    assert r.merge_node == "merge"
    assert r.alignment == "exact_cycle"
    assert not r.is_mismatch
    assert r.delta == 0
    totals = {p.path[0]: p.total_cycles for p in r.paths}
    assert totals["sobel"] == totals["thresh"] == 17  # norm(3) common to both


# ─────────────────────────────────────────────────────────────────────────
# Phase 4 slice 3 — per-instance graph granularity (release-plan §4.3)
# ─────────────────────────────────────────────────────────────────────────

def test_multi_instance_module_produces_real_merge_point():
    """A 2-instance producer feeding a 1-instance consumer must become a
    genuine 2-predecessor merge point — before this slice, LatencyGraph
    collapsed every instance of a module into one node, so this could
    never be detected at all."""
    graph = LatencyGraph(
        nodes={
            "p[0]": _node("p[0]", 3),
            "p[1]": _node("p[1]", 3),
            "c": _node("c", 1),
        },
        edges=[
            LatencyEdge(src="p[0]", dst="c", latency=None),
            LatencyEdge(src="p[1]", dst="c", latency=None),
        ],
    )
    reports = check_merge_points(graph)
    assert len(reports) == 1
    assert reports[0].merge_node == "c"
    assert not reports[0].is_mismatch


# ─────────────────────────────────────────────────────────────────────────
# Phase 4 slice 3 — alignment-kind inference (release-plan §4.3)
#
# Neither reference design declares kind: bounded/elastic today (only
# kind: fixed, via the real trigger_logic migration in slice 2) — these
# cases are necessarily synthetic-only, same honesty as every other
# "no real usage exists yet" item in this project's deferral lists.
# ─────────────────────────────────────────────────────────────────────────

def _node_with_kind(name, kind, *, cycles=None, min_cycles=None, max_cycles=None, is_variable=False):
    return LatencyNode(
        name=name, ref=name, instances=1, kind="rtl",
        latency=LatencyValue(
            kind=kind, cycles=cycles, min_cycles=min_cycles, max_cycles=max_cycles,
            provenance=LatencyProvenance("explicit_contract"),
        ),
        is_variable=is_variable,
    )


def test_elastic_predecessor_suppresses_mismatch_despite_unequal_cycles():
    """The checker must not assume every reconvergence requires identical
    scalar latency (release-plan §4.3) — an elastic predecessor makes the
    merge point tolerant, even though the fixed predecessor's cycle count
    differs wildly."""
    graph = LatencyGraph(
        nodes={
            "a": _node_with_kind("a", "fixed", cycles=2),
            "b": _node_with_kind("b", "elastic", is_variable=True),
            "merge": _node("merge", 1),
        },
        edges=[
            LatencyEdge(src="a", dst="merge", latency=None),
            LatencyEdge(src="b", dst="merge", latency=None),
        ],
    )
    reports = check_merge_points(graph)
    assert len(reports) == 1
    assert reports[0].alignment == "elastic_buffer"
    assert not reports[0].is_mismatch


def test_bounded_predecessors_with_overlapping_ranges_are_not_a_mismatch():
    """Two bounded predecessors whose [min,max] ranges overlap ([3,6] and
    [5,8], overlapping at [5,6]) tolerate the skew — not every
    reconvergence needs identical scalar latency."""
    graph = LatencyGraph(
        nodes={
            "a": _node_with_kind("a", "bounded", min_cycles=3, max_cycles=6),
            "b": _node_with_kind("b", "bounded", min_cycles=5, max_cycles=8),
            "merge": _node("merge", 1),
        },
        edges=[
            LatencyEdge(src="a", dst="merge", latency=None),
            LatencyEdge(src="b", dst="merge", latency=None),
        ],
    )
    reports = check_merge_points(graph)
    assert len(reports) == 1
    assert reports[0].alignment == "bounded_skew"
    assert not reports[0].is_mismatch


def test_bounded_predecessors_with_non_overlapping_ranges_are_a_mismatch():
    """Two bounded predecessors whose ranges [1,2] and [5,8] never overlap
    — the checker must still catch a genuine, real skew problem, proving
    the tolerance mechanism isn't blanket-permissive."""
    graph = LatencyGraph(
        nodes={
            "a": _node_with_kind("a", "bounded", min_cycles=1, max_cycles=2),
            "b": _node_with_kind("b", "bounded", min_cycles=5, max_cycles=8),
            "merge": _node("merge", 1),
        },
        edges=[
            LatencyEdge(src="a", dst="merge", latency=None),
            LatencyEdge(src="b", dst="merge", latency=None),
        ],
    )
    reports = check_merge_points(graph)
    assert len(reports) == 1
    assert reports[0].alignment == "bounded_skew"
    assert reports[0].is_mismatch


def test_bounded_vs_fixed_mixed_merge_point_uses_range_overlap():
    """A bounded predecessor mixed with a fixed one — the fixed
    predecessor's exact value must be checked against the bounded one's
    range (a degenerate [v, v] point range), not skipped."""
    graph = LatencyGraph(
        nodes={
            "a": _node_with_kind("a", "fixed", cycles=4),  # inside [3,6]
            "b": _node_with_kind("b", "bounded", min_cycles=3, max_cycles=6),
            "merge": _node("merge", 1),
        },
        edges=[
            LatencyEdge(src="a", dst="merge", latency=None),
            LatencyEdge(src="b", dst="merge", latency=None),
        ],
    )
    reports = check_merge_points(graph)
    assert len(reports) == 1
    assert reports[0].alignment == "bounded_skew"
    assert not reports[0].is_mismatch  # 4 is inside [3,6]


def test_ready_valid_protocol_infers_transaction_order_alignment():
    """Best-effort transaction_order inference via an optional
    consumer_protocols mapping — a ready-valid consumer tolerates
    non-cycle-exact producer alignment."""
    graph = LatencyGraph(
        nodes={
            "a": _node("a", 2),
            "b": _node("b", 9),
            "merge": _node("merge", 1),
        },
        edges=[
            LatencyEdge(src="a", dst="merge", latency=None),
            LatencyEdge(src="b", dst="merge", latency=None),
        ],
    )
    reports = check_merge_points(graph, consumer_protocols={"merge": "ready-valid"})
    assert len(reports) == 1
    assert reports[0].alignment == "transaction_order"
    assert not reports[0].is_mismatch


def test_consumer_protocols_defaults_to_no_effect():
    """Omitting consumer_protocols (the default) must behave identically
    to before — checker stays usable from a bare LatencyGraph alone."""
    graph = LatencyGraph(
        nodes={
            "a": _node("a", 2),
            "b": _node("b", 9),
            "merge": _node("merge", 1),
        },
        edges=[
            LatencyEdge(src="a", dst="merge", latency=None),
            LatencyEdge(src="b", dst="merge", latency=None),
        ],
    )
    reports = check_merge_points(graph)
    assert reports[0].alignment == "exact_cycle"
    assert reports[0].is_mismatch
