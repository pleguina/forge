"""
Tests for forge.analysis.latency_static.checker's edge-latency fold-in.

Before this fix, LatencyEdge carried no latency at all, so
check_merge_points summed only each predecessor node's own latency — any
register_stages/delay_cycles/CDC latency FORGE inserted on the connecting
edge was silently invisible to mismatch detection. These tests prove the
fix directly: a merge point that would previously have been reported
"balanced" (because both predecessor *nodes* have equal latency) is now
correctly flagged once one predecessor's connecting *edge* carries extra
cycles the other doesn't.
"""

from __future__ import annotations

from forge.analysis.latency_static.checker import check_merge_points
from forge.analysis.latency_static.graph import LatencyEdge, LatencyGraph, LatencyNode
from forge.analysis.latency_model import LatencyProvenance, LatencyValue


def _node(name, cycles, *, is_variable=False):
    return LatencyNode(
        name=name, ref=name, instances=1, kind="rtl",
        latency=LatencyValue(cycles=cycles, provenance=LatencyProvenance("explicit_contract")),
        is_variable=is_variable,
    )


def test_unknown_cdc_edge_is_not_silently_treated_as_zero_extra_cycles():
    """Before this fix, a
    mailbox_transfer/async_fifo edge (LatencyEdge.latency is always None
    for these kinds — see graph.py's _edge_latency_from_connection) was
    indistinguishable from a plain connection with genuinely zero extra
    cycles, so its predecessor's own real, fixed latency was folded
    straight through as if the crossing added nothing — silently wrong,
    since a mailbox_transfer/async_fifo's real latency is unbounded. A
    node fed by one real, single fixed-latency predecessor and one
    unknown_cdc predecessor must report that one real predecessor's
    total as known, and the unknown_cdc one as unknown — never a
    fabricated equal/mismatched delta between them."""
    graph = LatencyGraph(
        nodes={
            "ctrl_src": _node("ctrl_src", 1),
            "data_src": _node("data_src", 3),
            "merge": _node("merge", 2),
        },
        edges=[
            LatencyEdge(src="ctrl_src", dst="merge", latency=None, unknown_cdc=True),
            LatencyEdge(src="data_src", dst="merge", latency=None),
        ],
    )
    reports = check_merge_points(graph)
    assert len(reports) == 1
    r = reports[0]
    assert r.has_unknowns
    assert not r.is_mismatch  # only one known path — no meaningful delta
    totals = {p.path[0]: p.total_cycles for p in r.paths}
    assert totals == {"ctrl_src": None, "data_src": 3}


def test_node_with_one_real_and_one_unknown_cdc_predecessor_folds_through_the_real_side():
    """A node with two
    predecessors — one reached via an unknown_cdc edge (e.g. a
    frame-boundary-gated config register fed by a real
    cdc: {kind: mailbox_transfer} crossing), one a real single data
    predecessor — still has a fully well-defined upstream chain through
    its real side. A FURTHER downstream merge point comparing this
    node's branch against a sibling straight-line branch must see the
    real upstream latency folded all the way through (not reset to just
    this node's own isolated latency, which would silently understate
    the branch's real total and produce a false mismatch/wrong signal_delay
    suggestion) — found running a real design (vision_pipeline_demo's
    platform-wrapper fixture) where this exact shape first
    occurred: threshcfg has both a real `norm` data predecessor and an
    unknown_cdc `ctrl_mailbox` predecessor, and is itself a predecessor
    of `merge` alongside a real straight-line `sobel` branch."""
    graph = LatencyGraph(
        nodes={
            "ctrl_mailbox": _node("ctrl_mailbox", 1),
            "norm": _node("norm", 3),
            "winbld": _node("winbld", 10),
            "sobel": _node("sobel", 4),
            "threshcfg": _node("threshcfg", 2),
            "merge": _node("merge", 1),
        },
        edges=[
            LatencyEdge(src="ctrl_mailbox", dst="threshcfg", latency=None, unknown_cdc=True),
            LatencyEdge(src="norm", dst="threshcfg", latency=None),
            LatencyEdge(src="norm", dst="winbld", latency=None),
            LatencyEdge(src="winbld", dst="sobel", latency=None),
            LatencyEdge(src="sobel", dst="merge", latency=None),
            LatencyEdge(src="threshcfg", dst="merge", latency=LatencyValue(
                cycles=12, provenance=LatencyProvenance("generated_transformation", detail="delay_cycles=12"),
            )),
        ],
    )
    reports = check_merge_points(graph)
    by_node = {r.merge_node: r for r in reports}

    # threshcfg's own point: one real (norm=3), one genuinely unknown
    # (ctrl_mailbox) — no fabricated mismatch.
    thresh_report = by_node["threshcfg"]
    assert not thresh_report.is_mismatch
    thresh_totals = {p.path[0]: p.total_cycles for p in thresh_report.paths}
    assert thresh_totals == {"ctrl_mailbox": None, "norm": 3}

    # merge's own point: sobel's real chain is norm(3)+winbld(10)+sobel(4)=17;
    # threshcfg's real chain must fold through norm too — norm(3)+threshcfg(2)
    # +edge(12)=17 — not threshcfg(2)+edge(12)=14 (which would falsely
    # report a 3-cycle mismatch and a wrong signal_delay suggestion).
    merge_report = by_node["merge"]
    assert not merge_report.is_mismatch
    merge_totals = {p.path[0]: p.total_cycles for p in merge_report.paths}
    assert merge_totals == {"sobel": 17, "threshcfg": 17}


def test_explicitly_variable_predecessor_does_not_poison_a_node_with_its_own_known_latency():
    """A node whose *only* real graph predecessor is an explicitly
    variable-latency config tap (e.g. cfg64_from_framework-style async
    config synchronizer) must keep its own, separately-known, fixed
    latency — not have it silently overwritten to "unknown" just because
    its one modeled predecessor happens to be async.

    Found on OMTF's real topology: dt_interface/csc_interface each have
    exactly one graph predecessor (their config tap `cfg`, not their true
    top-level-external detector-hit input, which isn't modeled as an edge
    at all), which was turning `subdet` — a real, balanced merge point —
    into a false "unknown", even though dt/csc both carry real,
    HLS-synthesised latencies (8/9 cycles)."""
    graph = LatencyGraph(
        nodes={
            "cfg": _node("cfg", None, is_variable=True),
            "dt": _node("dt", 8),
            "direct": _node("direct", 8),
            "subdet": _node("subdet", 0),
        },
        edges=[
            LatencyEdge(src="cfg", dst="dt", latency=None),
            LatencyEdge(src="dt", dst="subdet", latency=None),
            LatencyEdge(src="direct", dst="subdet", latency=None),
        ],
    )
    reports = check_merge_points(graph)
    by_node = {r.merge_node: r for r in reports}

    subdet_report = by_node["subdet"]
    subdet_totals = {p.path[0]: p.total_cycles for p in subdet_report.paths}
    assert subdet_totals == {"dt": 8, "direct": 8}
    assert not subdet_report.is_mismatch
    assert not subdet_report.has_unknowns


def test_chain_folds_through_a_confirmed_balanced_merge_point():
    """A merge point (>=2 real predecessors) that is itself confirmed
    balanced (all real predecessors agree on total) must have its real
    accumulated total folded through to a FURTHER downstream merge
    point — not reset to just its own isolated latency, which would
    silently discard the confirmed-real upstream total and inflate an
    unrelated downstream comparison.

    Found on a real external consumer's topology: `concentrator` (own
    latency 0) has two real predecessors both totalling 11 (a genuinely
    balanced merge) — but a further downstream merge point comparing
    `concentrator`'s branch against a sibling `rpc`-array branch (real
    total 13) was seeing `concentrator` contribute only its own 0
    cycles, inflating a real ~2-cycle discrepancy into a misleading 13."""
    graph = LatencyGraph(
        nodes={
            "dt": _node("dt", 11),
            "csc": _node("csc", 9),
            "csc_delay": _node("csc_delay", 2),
            "concentrator": _node("concentrator", 0),
            "rpc": _node("rpc", 13),
            "rgf": _node("rgf", 2),
        },
        edges=[
            LatencyEdge(src="dt", dst="concentrator", latency=None),
            LatencyEdge(src="csc", dst="csc_delay", latency=None),
            LatencyEdge(src="csc_delay", dst="concentrator", latency=None),
            LatencyEdge(src="concentrator", dst="rgf", latency=None),
            LatencyEdge(src="rpc", dst="rgf", latency=None),
        ],
    )
    reports = check_merge_points(graph)
    by_node = {r.merge_node: r for r in reports}

    # concentrator itself: dt(11) vs csc(9)+csc_delay(2)=11 -> balanced.
    concentrator_report = by_node["concentrator"]
    assert not concentrator_report.is_mismatch
    concentrator_totals = {p.path[0]: p.total_cycles for p in concentrator_report.paths}
    assert concentrator_totals == {"dt": 11, "csc_delay": 11}

    # rgf: concentrator's real folded total (11, its input-side
    # accumulated latency — rgf's own 2 cycles are not part of this
    # input-side comparison) vs rpc's 13 -> a real 2-cycle mismatch, not
    # the false 13-cycle one the unfolded (concentrator contributing
    # just its own isolated 0) computation would have shown.
    rgf_report = by_node["rgf"]
    rgf_totals = {p.path[0]: p.total_cycles for p in rgf_report.paths}
    assert rgf_totals == {"concentrator": 11, "rpc": 13}
    assert rgf_report.is_mismatch
    assert rgf_report.delta == 2


def test_chain_stays_conservative_through_a_genuinely_mismatched_merge_point():
    """Contrast: when the upstream merge point is NOT balanced, folding
    through it must not happen — a further downstream comparison keeps
    seeing just the merge point's own isolated latency, so
    check_merge_points' own loop is the one place that flags the real
    mismatch, not a fabricated pick of one branch over another."""
    graph = LatencyGraph(
        nodes={
            "dt": _node("dt", 8),
            "csc": _node("csc", 9),
            "csc_delay": _node("csc_delay", 2),
            "concentrator": _node("concentrator", 0),
            "rpc": _node("rpc", 13),
            "rgf": _node("rgf", 2),
        },
        edges=[
            LatencyEdge(src="dt", dst="concentrator", latency=None),
            LatencyEdge(src="csc", dst="csc_delay", latency=None),
            LatencyEdge(src="csc_delay", dst="concentrator", latency=None),
            LatencyEdge(src="concentrator", dst="rgf", latency=None),
            LatencyEdge(src="rpc", dst="rgf", latency=None),
        ],
    )
    reports = check_merge_points(graph)
    by_node = {r.merge_node: r for r in reports}

    # concentrator: dt(8) vs csc(9)+csc_delay(2)=11 -> a real mismatch.
    assert by_node["concentrator"].is_mismatch

    # rgf: concentrator contributes only its own isolated 0 (not folded
    # through an unresolved merge) vs rpc's 13.
    rgf_totals = {p.path[0]: p.total_cycles for p in by_node["rgf"].paths}
    assert rgf_totals == {"concentrator": 0, "rpc": 13}


def test_control_strobe_predecessor_is_excluded_from_merge_point_detection():
    """A node fed by exactly one real data predecessor plus a
    control_strobe predecessor (e.g. bx_timing's new_event_arb reset
    pulse alongside arb's real upstream data path) must not be treated
    as a merge point at all — the strobe's timing is deliberately
    scheduled by the receiving design, not a second data source
    requiring exact-cycle reconciliation against the real data path.

    Found on OMTF's real topology: `arb` has exactly 2 graph
    predecessors (`bx_timing` via a control-strobe connection, `rgf` via
    real data), and was being falsely flagged as an unresolvable
    mismatch (bx_timing's own flat latency, 0, compared against rgf's
    real accumulated latency) even though bx_timing's strobe is not
    really "data" reaching `arb` in the sense the merge-balance check is
    for."""
    graph = LatencyGraph(
        nodes={
            "bx_timing": _node("bx_timing", 0),
            "rgf": _node("rgf", 2),
            "arb": _node("arb", 5),
        },
        edges=[
            LatencyEdge(src="bx_timing", dst="arb", latency=None, control_strobe=True),
            LatencyEdge(src="rgf", dst="arb", latency=None),
        ],
    )
    reports = check_merge_points(graph)
    # arb has only one *real* predecessor (rgf) once the control strobe
    # is excluded -> not a merge point, no report at all.
    assert not any(r.merge_node == "arb" for r in reports)


def test_control_strobe_alongside_two_real_predecessors_leaves_a_real_merge_point():
    """Contrast: a control_strobe predecessor must not hide a *genuine*
    2-real-predecessor merge point either — only the strobe itself is
    excluded from the comparison, not the whole node."""
    graph = LatencyGraph(
        nodes={
            "bx_timing": _node("bx_timing", 0),
            "subdet": _node("subdet", 0),
            "rpc": _node("rpc", 13),
            "rgf": _node("rgf", 2),
        },
        edges=[
            LatencyEdge(src="bx_timing", dst="rgf", latency=None, control_strobe=True),
            LatencyEdge(src="subdet", dst="rgf", latency=None),
            LatencyEdge(src="rpc", dst="rgf", latency=None),
        ],
    )
    reports = check_merge_points(graph)
    rgf_report = next(r for r in reports if r.merge_node == "rgf")
    paths = {p.path[0]: p.total_cycles for p in rgf_report.paths}
    # Only the two real data predecessors participate; bx_timing's
    # strobe is absent entirely, not just marked unknown.
    assert paths == {"subdet": 0, "rpc": 13}
    assert rgf_report.is_mismatch  # 0 vs 13 is a real data-path imbalance


def test_undeclared_zero_cycle_predecessor_still_propagates_as_unknown():
    """Contrast with the fix above: a predecessor with no declared
    latency at all (genuinely missing data — not an explicit
    variable_latency/elastic architectural declaration) must still
    poison the chain, exactly as before this fix. Only an *explicit*
    variable/elastic declaration earns the special treatment."""
    graph = LatencyGraph(
        nodes={
            "unknown_upstream": _node("unknown_upstream", None),
            "dt": _node("dt", 8),
            "direct": _node("direct", 8),
            "subdet": _node("subdet", 0),
        },
        edges=[
            LatencyEdge(src="unknown_upstream", dst="dt", latency=None),
            LatencyEdge(src="dt", dst="subdet", latency=None),
            LatencyEdge(src="direct", dst="subdet", latency=None),
        ],
    )
    reports = check_merge_points(graph)
    subdet_report = {r.merge_node: r for r in reports}["subdet"]
    subdet_totals = {p.path[0]: p.total_cycles for p in subdet_report.paths}
    assert subdet_totals == {"dt": None, "direct": 8}
    assert subdet_report.has_unknowns


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
    report a *mismatch* as a side effect of folding edge latency in or
    per-instance graph granularity. passthrough_demo has zero merge points
    (single module). trigger_demo now has 2 real merge points (col, partmon —
    each fed by all 4 real 'dec' instances, previously invisible when
    'dec' collapsed to one module-group node) — both correctly balanced,
    since every 'dec' instance shares the same module-level
    latency_hint: 0 and no register_stages/delay_cycles is declared on
    either topology group."""
    from pathlib import Path
    from forge.analysis.latency_static.graph import build_graph

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
# Multi-hop chain latency (found while
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
    """Real-design proof: the Sobel
    branch (window_builder_rtl 10 cycles + sobel_hls 4 cycles, 2 hops
    into `merge`) and the threshold branch (threshold_rtl 2 cycles + a
    12-cycle Connection.delay_cycles alignment delay) must land on
    edge_mask_merge_rtl at the exact same cycle -- the whole point of
    this design. Before the chain-walk fix, the Sobel
    path would have been silently under-reported as 4 cycles (just
    sobel_hls's own latency), producing a false "balanced" or a bogus
    delta depending on what the threshold branch happened to total."""
    from pathlib import Path
    from forge.analysis.latency_static.graph import build_graph

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
# Per-instance graph granularity
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
# Alignment-kind inference
#
# Neither reference design declares kind: bounded/elastic today (only
# kind: fixed, via the real trigger_logic migration) — these
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
    scalar latency — an elastic predecessor makes the
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
