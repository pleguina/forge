"""
Proofs for migration step 7 (docs/development/release-readiness.md):
forge.analyze.latency_static.graph.build_graph's new DesignConfig-driven
implementation must produce results identical to the pre-migration
independent raw-YAML re-parse, on real designs — before treating the
migration as done.
"""

from __future__ import annotations

from pathlib import Path

from analyze.latency_static.graph import build_graph, _build_graph_legacy, _build_graph_from_ir
from analyze.latency_static.checker import check_merge_points
from analyze.latency_static.reporter import render_markdown

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
    """Phase 4 slice 1 (release-plan §4.1): real register_stages/
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
    # Phase 4 slice 3: 'dec' has 4 real instances -> 4 real per-instance
    # nodes (release-plan §4.3), not one collapsed 'dec' bucket node.
    assert set(g.nodes) == {
        "dec[0]", "dec[1]", "dec[2]", "dec[3]",
        "col", "trig", "partmon", "tfan", "tsink", "tout",
    }
    assert g.nodes["trig"].latency_cycles == 3
    # Phase 4 slice 2: 'trig' migrated to a structured latency: {kind:
    # fixed, cycles: 3} declaration (a real, known-fixed HLS latency) —
    # provenance is now explicit_contract, not user_hint (release-plan §4.2).
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
    # instances (Phase 4 slice 3) -> checking one representative instance
    # node is sufficient; all 4 share the same module-level latency.
    assert g.nodes["dec[0]"].latency_cycles == 99
    assert g.nodes["dec[0]"].latency_source == "user_hint"
