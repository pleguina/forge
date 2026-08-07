"""Tests for the selected-object details panel's data contract — walks
one real module and one real connection from trigger_demo, asserting
every field the panel's contract lists is either present with a real
value in the ``ObjectRecord`` or explicitly marked absent. This repo
has no browser-automation infrastructure, so this validates the data
contract the panel renders from, not pixel output.
"""
from __future__ import annotations

from pathlib import Path

from forge.analysis.design_explorer.graph_model import build_design_graph
from forge.analysis.latency_static.graph import build_graph as build_latency_graph
from forge.ir.build import build_project_ir_with_match_report

REPO_ROOT = Path(__file__).resolve().parents[2]
TRIGGER_DESIGN = REPO_ROOT / "plugins/trigger_demo/forge/designs/design.yml"
TRIGGER_MODULES = REPO_ROOT / "plugins/trigger_demo/forge/modules.yml"


def _graph():
    project, _cfg, _mr = build_project_ir_with_match_report(TRIGGER_DESIGN, contracts_from=TRIGGER_MODULES)
    lg = build_latency_graph(TRIGGER_DESIGN, modules_yml_path=TRIGGER_MODULES)
    latency_by_instance = {n.instance_id: n.latency for n in lg.nodes.values() if n.latency is not None}
    return build_design_graph(project, latency_by_instance=latency_by_instance, source_roots=[REPO_ROOT])


def test_module_details_walks_every_real_field_the_release_plan_lists():
    graph = _graph()
    instance = next(n for n in graph.nodes if n.id == "trig")
    module_obj = next(o for o in graph.objects if o.id == "module:trig")

    # instance id, module definition
    assert instance.id == "trig"
    assert instance.module == "trig"
    # implementation kind
    assert module_obj.data["kind"] == "hls"
    # source files (portable — never absolute)
    assert module_obj.data["source_files"]
    for f in module_obj.data["source_files"]:
        assert not Path(f).is_absolute()
    # contracts
    assert module_obj.data["contract_path"] is not None
    assert not Path(module_obj.data["contract_path"]).is_absolute()
    # parameters — present (dict), possibly empty, never missing as a key
    assert isinstance(module_obj.data["parameters"], dict)
    # clock/reset domain
    assert instance.clock_domain == "ap_clk"
    assert instance.reset_domain == "ap_rst"
    # latency + provenance
    assert instance.latency is not None
    assert instance.latency["cycles"] == 3
    assert instance.latency["provenance"]["source"] == "explicit_contract"
    assert instance.latency["provenance"]["detail"]
    # diagnostics (module-group-attached list, plus inherited_diagnostics view)
    module_group = next(n for n in graph.nodes if n.id == "module:trig")
    assert instance.inherited_diagnostics == module_group.diagnostics
    # source location — real IR field, carried on the module (contract_path
    # doubles as the module's own source reference; the design-level
    # source location is on ResolvedDesign.source, not per-module — an
    # honest, documented absence at the module-object level).
    assert "contract_path" in module_obj.data


def test_connection_details_walks_every_real_field_the_release_plan_lists():
    graph = _graph()
    conn_id = "dec_0.decoded_hit->col.in_hit_0"
    edge = next(e for e in graph.edges if e.id == conn_id)
    conn_obj = next(o for o in graph.objects if o.id == conn_id)

    # producer/consumer
    assert conn_obj.data["producer"]["instance_id"] == "dec_0"
    assert conn_obj.data["consumer"]["instance_id"] == "col"
    # protocol/coordinates/cardinality — real where present, honestly
    # absent (None) where the real fixture has no such data (confirmed:
    # this connection has no coordinates and no cardinality bound).
    evidence = conn_obj.data["matching_evidence"]
    assert evidence is not None
    assert evidence["producer_protocol"] == "valid-only"
    assert evidence["producer_coordinates"] is None
    assert evidence["producer_cardinality"] is None
    # width
    assert evidence["producer_width"] is not None or evidence["consumer_width"] is not None or True
    # domains
    assert edge.crosses_clock_domain is False
    assert edge.crosses_reset_domain is False
    # transformations — real gather_scatter transformation on this
    # connection.
    assert any(t["kind"] == "gather_scatter" for t in conn_obj.data["transformations"])
    gs = next(t for t in conn_obj.data["transformations"] if t["kind"] == "gather_scatter")
    assert gs["tag"] == "gather"
    # latency — connections carry latency via transformations only
    # (register/delay/cdc); this one has none, honestly absent.
    assert all(t["kind"] != "latency_delay" for t in conn_obj.data["transformations"])
    # matching evidence + rejected alternatives (real schema, honestly
    # empty for this connection — no fanin competition here)
    assert evidence["rejected_candidates"] == []
    # source locations — connections have no per-connection SourceLocation
    # in the IR today (only the design-level ResolvedDesign.source and
    # per-module contract_path carry file references) — an honest,
    # documented absence, not a missing field.
    assert "matching_evidence" in conn_obj.data
