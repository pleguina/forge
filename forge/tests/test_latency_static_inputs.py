"""Connections and topology groups declared ``static: true`` take no part in
merge-point alignment or in sizing ``delay_cycles: auto``."""

from __future__ import annotations

import pytest

from forge.analysis.latency_static.balance import resolve_auto_delays, resolve_design_auto_delays
from forge.analysis.latency_static.checker import check_merge_points
from forge.analysis.latency_static.graph import build_graph
from forge.analysis.latency_static.reporter import render_markdown
from forge.contracts.config import DesignConfig


def _write_design(tmp_path, cfg_link, fast_delay="5"):
    (tmp_path / "modules.yml").write_text(
        "modules:\n"
        "  - {name: slow, kind: hls, top: slow_top, src: [slow.cpp], latency_cycles: 9}\n"
        "  - {name: fast, kind: hls, top: fast_top, src: [fast.cpp], latency_cycles: 4}\n"
        "  - {name: merge, kind: hls, top: merge_top, src: [merge.cpp], latency_cycles: 1}\n"
        # a configuration sampler with no known latency
        "  - {name: cfg64, kind: rtl, top: cfg64, src: [cfg64.v]}\n"
    )
    design = tmp_path / "design.yml"
    design.write_text(
        "part: xcvu13p\n"
        "clock_period: 4.0\n"
        "registry: modules.yml\n"
        "modules:\n"
        "  - {name: slow, ref: slow}\n"
        "  - {name: fast, ref: fast}\n"
        "  - {name: merge, ref: merge}\n"
        "  - {name: cfg, ref: cfg64}\n"
        "connections:\n"
        "  - {from: slow, to: merge, port_map: [[dout, in_slow]]}\n"
        f"  - {{from: fast, to: merge, port_map: [[dout, in_fast]], delay_cycles: {fast_delay}}}\n"
        + cfg_link
    )
    return design


_TG = ("topology_groups:\n"
       "  - {{name: cfg_to_merge, family: configuration, from: cfg, to: merge,\n"
       "     role_pairs: [[cfg_word_out, cfg_word]]{extra}}}\n")
_CONN = "  - {{from: cfg, to: merge, port_map: [[cfg_out, cfg_in]]{extra}}}\n"


def _merge_report(design):
    return next(r for r in check_merge_points(build_graph(design)) if r.merge_node == "merge")


def test_an_unknown_configuration_input_makes_the_merge_unknown(tmp_path):
    report = _merge_report(_write_design(tmp_path, _TG.format(extra="")))
    assert report.has_unknowns and not report.is_mismatch


@pytest.mark.parametrize("link", [_TG, _CONN])
def test_a_static_input_is_not_aligned(tmp_path, link):
    design = _write_design(tmp_path, link.format(extra=", static: true"))
    report = _merge_report(design)
    assert not report.has_unknowns and not report.is_mismatch
    assert {p.path[0] for p in report.paths} == {"slow", "fast"}


def test_a_static_input_still_lets_a_data_mismatch_show(tmp_path):
    design = _write_design(tmp_path, _TG.format(extra=", static: true"), fast_delay="0")
    report = _merge_report(design)
    assert report.is_mismatch and report.delta == 5


def test_auto_delay_is_sized_past_a_static_input(tmp_path):
    design = _write_design(tmp_path, _TG.format(extra=", static: true"), fast_delay="auto")
    result = resolve_design_auto_delays(design)
    assert not result.problems
    assert result.by_ref() == {("connection", 1): 5}


def test_a_data_connection_beside_a_static_one_keeps_the_edge_aligned(tmp_path):
    link = _TG.format(extra=", static: true").replace(
        "topology_groups:", "  - {from: cfg, to: merge, port_map: [[dout, in_cfg]]}\ntopology_groups:"
    )
    graph = build_graph(_write_design(tmp_path, link))
    edge = next(e for e in graph.edges if e.src == "cfg" and e.dst == "merge")
    assert not edge.static and edge.aligned


@pytest.mark.parametrize("link", [_TG, _CONN])
def test_static_with_auto_delay_is_rejected(tmp_path, link):
    design = _write_design(tmp_path, link.format(extra=", static: true, delay_cycles: auto"))
    with pytest.raises(ValueError, match="static"):
        DesignConfig.load_relaxed(design)


def test_the_report_lists_static_inputs(tmp_path):
    graph = build_graph(_write_design(tmp_path, _TG.format(extra=", static: true")))
    out = tmp_path / "latency.md"
    render_markdown(graph, check_merge_points(graph), out, auto_delays=resolve_auto_delays(graph))
    text = out.read_text()
    assert "## Static inputs" in text
    assert "1 connection(s) declared `static: true` into 1 instance(s)" in text
    assert "Unknown latency at `merge`" not in text
