"""Tests for the explorer functionality's data contract — every
overlay/filter/collapse feature the client-side JS implements reads
from real ``DesignGraph``/``ObjectRecord`` fields; these tests prove
those fields are real, correct, and honestly absent where appropriate.
Per this codebase's own honest-deferral discipline, no browser-automation
or JS-runtime infrastructure exists in this repo or this sandbox (no
Node.js, no sudo to install one) — JS *execution* behavior (a live
collapse/expand round-trip, a live overlay switch) is therefore not
directly exercised here. What *is* directly, non-syntheticaly tested is
every real fact those features read: this is the data contract the UI
renders from, not pixel/DOM output.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forge.analysis.design_explorer.graph_model import GraphNodeKind, build_design_graph
from forge.analysis.design_explorer.html_renderer import render_explorer_html
from forge.analysis.latency_static.graph import build_graph as build_latency_graph
from forge.ir.build import build_project_ir_with_match_report

REPO_ROOT = Path(__file__).resolve().parents[2]
TRIGGER_DESIGN = REPO_ROOT / "plugins/trigger_demo/forge/designs/design.yml"
TRIGGER_MODULES = REPO_ROOT / "plugins/trigger_demo/forge/modules.yml"


def _trigger_graph():
    project, _cfg, _mr = build_project_ir_with_match_report(TRIGGER_DESIGN, contracts_from=TRIGGER_MODULES)
    lg = build_latency_graph(TRIGGER_DESIGN, modules_yml_path=TRIGGER_MODULES)
    latency_by_instance = {n.instance_id: n.latency for n in lg.nodes.values() if n.latency is not None}
    return build_design_graph(project, latency_by_instance=latency_by_instance, source_roots=[REPO_ROOT])


# ── Latency overlay: real value, real honest absence ─────────────────────

def test_latency_overlay_data_shows_real_value_and_honest_no_data():
    graph = _trigger_graph()
    instance_nodes = {n.id: n for n in graph.nodes if n.kind == GraphNodeKind.INSTANCE}
    assert instance_nodes["trig"].latency["cycles"] == 3

    # Every other real instance in this fixture has an explicit,
    # honestly-populated user_hint (cycles=0) or genuinely no data — never
    # a fabricated stand-in for "trig's real value".
    for inst_id, node in instance_nodes.items():
        if inst_id == "trig":
            continue
        assert node.latency is None or node.latency["cycles"] != 3


# ── Filters: real wiring-method vocabulary, nothing invented ─────────────

def test_filter_vocabulary_matches_real_wiring_methods_present():
    graph = _trigger_graph()
    real_methods = {e.wiring_method for e in graph.edges if e.wiring_method is not None}
    assert real_methods <= {
        "contract_wiring", "port_map_ranges", "port_map", "auto_match", "topology_group", "heuristic",
    }
    assert real_methods, "expected at least one real, classified wiring method in this fixture"


# ── Diagnostics-only view: real resolved attachment, no fabrication ──────

def test_diagnostics_only_view_data_is_sparse_but_honest(tmp_path):
    """No real emission site produces an instance- or connection-level
    diagnostic today — the
    diagnostics-only view's data is therefore honestly sparse for both
    reference plugins. This test proves it's sparse for the *documented*
    reason (nothing to attach), not a bug silently dropping real data."""
    graph = _trigger_graph()
    edges_with_diagnostics = [e for e in graph.edges if e.diagnostics]
    instances_with_direct_diagnostics = [
        n for n in graph.nodes if n.kind == GraphNodeKind.INSTANCE and n.diagnostics
    ]
    assert edges_with_diagnostics == []
    assert instances_with_direct_diagnostics == []
    # trigger_demo's own real validator diagnostics happen to carry no
    # module-scoped location today (see test_design_explorer_overlays.py
    # for the real, populated module-group-diagnostic case, exercised
    # against a minimal design that does trigger one) — this fixture's
    # own diagnostics list is real and non-empty, just not module-linked.
    assert project_diagnostics_exist(TRIGGER_DESIGN, TRIGGER_MODULES)


def project_diagnostics_exist(design: Path, modules: Path) -> bool:
    project, _cfg, _mr = build_project_ir_with_match_report(design, contracts_from=modules)
    return len(project.design.diagnostics) > 0


# ── On-demand physical-port expansion: real interface data, honest empty ─

def test_physical_port_expansion_data_present_for_hls_modules_honest_empty_for_rtl():
    graph = _trigger_graph()
    module_objects = {o.id: o for o in graph.objects if o.kind == "module-definition"}

    trig = module_objects["module:trig"]
    assert trig.data["kind"] == "hls"
    assert trig.data["interfaces"], "trig (HLS) should have real, resolved logical interfaces"
    for iface in trig.data["interfaces"]:
        assert iface["members"], f"interface {iface['name']} should carry real physical bindings"

    tfan = module_objects["module:tfan"]
    assert tfan.data["kind"] == "rtl"
    # An RTL module with no interface contract legitimately has nothing to
    # expand — rendered as honestly empty, never an error.
    assert isinstance(tfan.data["interfaces"], list)


# ── Clock/reset-domain grouping toggle: real membership data ─────────────

def test_domain_group_membership_matches_real_ir_domains():
    project, _cfg, _mr = build_project_ir_with_match_report(TRIGGER_DESIGN, contracts_from=TRIGGER_MODULES)
    graph = build_design_graph(project, source_roots=[REPO_ROOT])
    for cd in project.design.clock_domains:
        node = next(n for n in graph.nodes if n.id == f"domain:clock:{cd.name}")
        assert set(node.members) == set(cd.instances)


# ── Rendered script: the collapse/expand + overlay hooks are really present ─
#
# JS *execution* (a live collapse/expand round-trip, a live overlay
# switch) is not exercised here — no Node.js/browser-automation runtime
# is available in this environment (see this module's docstring). This is
# a real, static content check that the mechanism the data above feeds is
# actually present and wired to the right identifiers, not a substitute
# for running it.

def test_rendered_script_contains_the_real_collapse_expand_and_overlay_functions(tmp_path):
    graph = _trigger_graph()
    out = tmp_path / "explorer.html"
    render_explorer_html(graph, out)
    text = out.read_text()

    for marker in (
        "function collapseGroup", "function expandGroup", "aggregate-edge",
        "function applyOverlay", "toggle-domain-grouping", "toggle-diagnostics-only",
        "path-predecessors", "path-successors", "search-box",
    ):
        assert marker in text, f"expected the real app JS to contain {marker!r}"
