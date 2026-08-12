"""
Proofs that forge.analysis.latency_static.graph.build_graph's
DesignConfig-driven implementation produces results identical to an
independent raw-YAML re-parse, on real designs.
"""

from __future__ import annotations

from pathlib import Path

from forge.analysis.latency_static.graph import (
    build_graph, _build_graph_legacy, _build_graph_from_ir, _edge_latency_from_connection,
)
from forge.analysis.latency_static.checker import check_merge_points
from forge.analysis.latency_static.reporter import render_markdown
from forge.contracts.config import Connection

REPO_ROOT = Path(__file__).resolve().parents[2]
TRIGGER_DESIGN = REPO_ROOT / "plugins/trigger_demo/forge/designs/design.yml"
PASSTHROUGH_DESIGN = REPO_ROOT / "plugins/passthrough_demo/forge/designs/design.yml"


def _assert_equivalent(design_path: Path) -> None:
    new_g = _build_graph_from_ir(design_path)
    old_g = _build_graph_legacy(design_path, None, None)

    assert new_g.nodes == old_g.nodes
    assert {(e.src, e.dst) for e in new_g.edges} == {(e.src, e.dst) for e in old_g.edges}


def test_trigger_demo_nodes_and_edges_equivalent():
    _assert_equivalent(TRIGGER_DESIGN)


def test_trigger_demo_real_edge_latency_is_folded_in():
    """Real register_stages/
    delay_cycles values already declared on trigger_demo's design.yml
    connections (col->trig: register_stages: 2; trig->tfan: delay_cycles:
    3) must now be visible on the LatencyEdge, not silently discarded."""
    g = _build_graph_from_ir(TRIGGER_DESIGN)
    edges = {(e.src, e.dst): e for e in g.edges}

    col_trig = edges[("col", "trig")]
    assert col_trig.latency is not None
    assert col_trig.latency.cycles == 2
    assert col_trig.latency.provenance.source == "generated_transformation"

    trig_tfan = edges[("trig", "tfan")]
    assert trig_tfan.latency is not None
    assert trig_tfan.latency.cycles == 3
    assert trig_tfan.latency.provenance.source == "generated_transformation"

    # tfan->tsink is contract_wiring only, no register_stages/delay_cycles/
    # cdc declared -> honestly no edge latency, not a fabricated 0.
    tfan_tsink = edges[("tfan", "tsink")]
    assert tfan_tsink.latency is None


def test_passthrough_demo_nodes_and_edges_equivalent():
    _assert_equivalent(PASSTHROUGH_DESIGN)


class TestEdgeLatencyFromCdcConnection:
    """_edge_latency_from_connection's CDC-kind
    treatment, extended from the single 2ff_sync case above to the full
    5-kind primitive family."""

    def _conn(self, cdc):
        return Connection(from_="src", to="dst", cdc=cdc)

    def test_level_sync_folds_in_fixed_two_cycles(self):
        latency = _edge_latency_from_connection(self._conn({"kind": "level_sync"}))
        assert latency is not None
        assert latency.cycles == 2
        assert latency.provenance.source == "generated_transformation"

    def test_2ff_sync_alias_folds_in_the_same_two_cycles(self):
        latency = _edge_latency_from_connection(self._conn({"kind": "2ff_sync"}))
        assert latency is not None
        assert latency.cycles == 2

    def test_pulse_sync_folds_in_fixed_three_cycles(self):
        latency = _edge_latency_from_connection(
            self._conn({"kind": "pulse_sync", "min_spacing_cycles": 8}),
        )
        assert latency is not None
        assert latency.cycles == 3
        assert latency.provenance.source == "generated_transformation"

    def test_mailbox_transfer_stays_unknown(self):
        """Round-trip handshake timing depends on relative clock phase —
        not statically knowable, same honest treatment as async_fifo."""
        assert _edge_latency_from_connection(self._conn({"kind": "mailbox_transfer"})) is None

    def test_async_fifo_stays_unknown(self):
        assert _edge_latency_from_connection(
            self._conn({"kind": "async_fifo", "depth": 8}),
        ) is None


def test_trigger_demo_report_is_byte_identical(tmp_path):
    new_g = _build_graph_from_ir(TRIGGER_DESIGN)
    old_g = _build_graph_legacy(TRIGGER_DESIGN, None, None)

    new_out = tmp_path / "new_report.md"
    old_out = tmp_path / "old_report.md"
    render_markdown(new_g, check_merge_points(new_g), new_out)
    render_markdown(old_g, check_merge_points(old_g), old_out)

    assert new_out.read_bytes() == old_out.read_bytes()


def test_build_graph_dispatches_to_ir_path_by_default():
    """No modules_yml_path override -> the IR-driven path is used, and its
    result matches what forge inspect/build_project_ir would derive."""
    g = build_graph(TRIGGER_DESIGN)
    # 'dec' has 4 real instances -> 4 real per-instance
    # nodes, not one collapsed 'dec' bucket node.
    assert set(g.nodes) == {
        "dec[0]", "dec[1]", "dec[2]", "dec[3]",
        "col", "trig", "partmon", "tfan", "tsink", "tout",
    }
    assert g.nodes["trig"].latency_cycles == 3
    # 'trig' migrated to a structured latency: {kind:
    # fixed, cycles: 3} declaration (a real, known-fixed HLS latency) —
    # provenance is now explicit_contract, not user_hint.
    assert g.nodes["trig"].latency_source == "explicit_contract"
    assert g.nodes["trig"].latency.kind == "fixed"


def test_build_graph_falls_back_to_legacy_for_a_genuinely_different_override(tmp_path):
    """A modules_yml_path override that resolves to a *different* file than
    design.yml's own registry: field must still work, via the legacy
    raw-YAML fallback (an undocumented but real part of build_graph's
    public contract)."""
    # trigger_demo's design.yml module 'dec' has `ref: hit_decoder` — the
    # alt registry must declare that same ref name for the override to
    # resolve to anything.
    alt_registry = tmp_path / "alt_modules.yml"
    alt_registry.write_text(
        "modules:\n"
        "  - name: hit_decoder\n"
        "    kind: rtl\n"
        "    top: alt_dec_top\n"
        "    src: [dec.v]\n"
        "    latency_hint: 99\n"
    )

    g = build_graph(TRIGGER_DESIGN, modules_yml_path=alt_registry)
    # The alt registry declares latency_hint: 99 for 'dec' — trigger_demo's
    # real modules.yml declares latency_hint: 0 for it. Seeing 99 here
    # proves the fallback path (not the IR path, which would have used
    # trigger_demo's real modules.yml) was actually used. 'dec' has 4 real
    # instances -> checking one representative instance
    # node is sufficient; all 4 share the same module-level latency.
    assert g.nodes["dec[0]"].latency_cycles == 99
    assert g.nodes["dec[0]"].latency_source == "user_hint"


def _write_ranged_multi_instance_design(tmp_path):
    """A 3-instance producer feeding a 3-instance relay one-to-one via a
    single ``port_map_ranges`` entry (``count`` == both sides' instance
    count — the exact shape found on OMTF's real ``csc -> csc_data_dly``
    connection, 52 instances each), then both the relay and an unrelated
    single-instance module feed a real 2-predecessor merge point.

    Before the fix this regression-tests, ``relay[i]``'s graph
    predecessors were *all 3* ``producer`` instances (a Cartesian-product
    artifact), which made ``_upstream_chain_latency`` bail out of
    chain-folding (more than one apparent real predecessor) and silently
    drop ``producer``'s own latency from ``relay``'s accumulated total —
    exactly the bug found on OMTF's ``csc -> csc_data_dly -> subdet``
    path.
    """
    registry = tmp_path / "modules.yml"
    registry.write_text(
        "modules:\n"
        "  - name: producer\n"
        "    kind: hls\n"
        "    top: producer_top\n"
        "    src: [producer.cpp]\n"
        "    latency_cycles: 9\n"
        "  - name: relay\n"
        "    kind: rtl\n"
        "    top: relay_top\n"
        "    src: [relay.v]\n"
        "    latency_cycles: 2\n"
        "  - name: direct\n"
        "    kind: hls\n"
        "    top: direct_top\n"
        "    src: [direct.cpp]\n"
        "    latency_cycles: 8\n"
        "  - name: merge\n"
        "    kind: hls\n"
        "    top: merge_top\n"
        "    src: [merge.cpp]\n"
    )
    design = tmp_path / "design.yml"
    design.write_text(
        "part: xcvu13p\n"
        "clock_period: 4.0\n"
        "registry: modules.yml\n"
        "modules:\n"
        "  - {name: producer, ref: producer, instances: 3}\n"
        "  - {name: relay, ref: relay, instances: 3}\n"
        "  - {name: direct, ref: direct}\n"
        "  - {name: merge, ref: merge}\n"
        "connections:\n"
        "  - from: producer\n"
        "    to: relay\n"
        "    port_map_ranges:\n"
        "      - {src_prefix: dout, dst_prefix: din, count: 3}\n"
        "  - from: relay\n"
        "    to: merge\n"
        "    port_map: [[dout, in_relay]]\n"
        "  - from: direct\n"
        "    to: merge\n"
        "    port_map: [[dout, in_direct]]\n"
    )
    return design


def test_ranged_multi_instance_connection_pairs_positionally_not_cartesian(tmp_path):
    """Direct proof of the pairing itself: relay[0]'s only real graph
    predecessor is producer[0] — not all 3 producer instances."""
    design = _write_ranged_multi_instance_design(tmp_path)
    g = _build_graph_from_ir(design)
    assert sorted(g.predecessors("relay[0]")) == ["producer[0]"]
    assert sorted(g.predecessors("relay[1]")) == ["producer[1]"]


def test_ranged_multi_instance_chain_folds_through_into_a_real_merge_point(tmp_path):
    """End-to-end: relay's upstream chain must include producer's 9 cycles
    (9 + 2 = 11), correctly detected as a 3-cycle mismatch against the
    direct path's 8 — silently invisible before the fix, since relay's
    chain-folding bailed out and reported only its own 2 cycles."""
    design = _write_ranged_multi_instance_design(tmp_path)
    g = build_graph(design)
    reports = check_merge_points(g)
    merge_reports = [r for r in reports if r.merge_node == "merge"]
    assert len(merge_reports) == 1
    report = merge_reports[0]
    assert report.is_mismatch
    assert report.delta == 3  # 11 (producer+relay) - 8 (direct)

    # Both code paths (IR-driven and the raw-YAML legacy fallback) must
    # agree — this is exactly what _assert_equivalent checks for the real
    # reference designs; explicit here since this is the one shape
    # neither reference design exercises.
    old_g = _build_graph_legacy(design, None, None)
    assert {(e.src, e.dst) for e in g.edges} == {(e.src, e.dst) for e in old_g.edges}
