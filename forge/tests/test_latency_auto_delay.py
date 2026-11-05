"""
Tests for ``delay_cycles: auto`` (forge.analysis.latency_static.balance) and
the merge-point ``input_alignment`` declarations it and the checker share.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from forge.analysis.latency_model import LatencyProvenance, LatencyValue
from forge.analysis.latency_static.balance import (
    apply_auto_delays, resolve_auto_delays, resolve_design_auto_delays,
)
from forge.analysis.latency_static.checker import check_merge_points
from forge.analysis.latency_static.graph import LatencyEdge, LatencyGraph, LatencyNode, build_graph
from forge.analysis.latency_static.reporter import render_markdown
from forge.contracts.config import DesignConfig, InputAlignment
from forge.generation.validation import validate_registry


def _node(name, cycles, *, module=None, alignment=None):
    latency = None
    if cycles is not None:
        latency = LatencyValue(cycles=cycles, provenance=LatencyProvenance("explicit_contract"))
    return LatencyNode(
        name=name, ref=name, instances=1, kind="rtl", latency=latency,
        module=module or name, alignment=alignment,
    )


def _edge(src, dst, *, ref=None, auto=False, cycles=None):
    latency = None
    if cycles is not None:
        latency = LatencyValue(
            cycles=cycles,
            provenance=LatencyProvenance("generated_transformation", detail=f"delay_cycles={cycles}"),
        )
    refs = [ref + (auto,)] if ref is not None else []
    return LatencyEdge(src=src, dst=dst, latency=latency, refs=refs)


def _totals(report):
    return {p.path[0]: p.total_cycles for p in report.paths}


# ── InputAlignment / config parsing ─────────────────────────────────────────

class TestInputAlignment:
    def test_default_is_exact(self):
        assert InputAlignment().kind == "exact"

    def test_unknown_kind_is_rejected(self):
        with pytest.raises(ValueError, match="kind"):
            InputAlignment(kind="loose")

    def test_window_requires_window_cycles(self):
        with pytest.raises(ValueError, match="window_cycles"):
            InputAlignment(kind="window")

    def test_window_cycles_only_with_window(self):
        with pytest.raises(ValueError, match="window_cycles"):
            InputAlignment(kind="exact", window_cycles=3)

    def test_offsets_must_be_integers(self):
        with pytest.raises(ValueError, match="offset"):
            InputAlignment(offsets={"a": "4"})


def _write_registry(tmp_path, merge_alignment=""):
    registry = tmp_path / "modules.yml"
    registry.write_text(
        "modules:\n"
        "  - name: slow\n"
        "    kind: hls\n"
        "    top: slow_top\n"
        "    src: [slow.cpp]\n"
        "    latency_cycles: 9\n"
        "  - name: fast\n"
        "    kind: hls\n"
        "    top: fast_top\n"
        "    src: [fast.cpp]\n"
        "    latency_cycles: 4\n"
        "  - name: merge\n"
        "    kind: hls\n"
        "    top: merge_top\n"
        "    src: [merge.cpp]\n"
        "    latency_cycles: 1\n"
        + merge_alignment
    )
    return registry


def _write_design(tmp_path, fast_delay="auto", merge_alignment="", extra_connections=""):
    _write_registry(tmp_path, merge_alignment)
    design = tmp_path / "design.yml"
    design.write_text(
        "part: xcvu13p\n"
        "clock_period: 4.0\n"
        "registry: modules.yml\n"
        "modules:\n"
        "  - {name: slow, ref: slow}\n"
        "  - {name: fast, ref: fast}\n"
        "  - {name: merge, ref: merge}\n"
        "connections:\n"
        "  - from: slow\n"
        "    to: merge\n"
        "    port_map: [[dout, in_slow]]\n"
        "  - from: fast\n"
        "    to: merge\n"
        "    port_map: [[dout, in_fast]]\n"
        f"    delay_cycles: {fast_delay}\n"
        + extra_connections
    )
    return design


class TestConfigParsing:
    def test_auto_sets_delay_auto(self, tmp_path):
        cfg = DesignConfig.load_relaxed(_write_design(tmp_path))
        fast = cfg.connections[1]
        assert fast.delay_auto and fast.delay_cycles == 0
        assert not cfg.connections[0].delay_auto

    def test_auto_with_boundary_is_rejected(self, tmp_path):
        design = _write_design(tmp_path, fast_delay="auto\n    boundary: slr0_slr1")
        with pytest.raises(ValueError, match="boundary"):
            DesignConfig.load_relaxed(design)

    def test_auto_after_a_register_stage_crossing_is_accepted(self, tmp_path):
        design = _write_design(tmp_path, fast_delay="auto\n    boundary: slr0_slr1\n    register_stages: 3")
        fast = DesignConfig.load_relaxed(design).connections[1]
        assert fast.delay_auto and fast.register_stages == 3 and fast.boundary == "slr0_slr1"

    def test_non_integer_delay_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="integer or 'auto'"):
            DesignConfig.load_relaxed(_write_design(tmp_path, fast_delay="many"))

    def test_input_alignment_reaches_the_graph(self, tmp_path):
        design = _write_design(
            tmp_path,
            merge_alignment="    input_alignment: {kind: window, window_cycles: 2, offsets: {slow: 1}}\n",
        )
        alignment = build_graph(design).nodes["merge"].alignment
        assert alignment.kind == "window"
        assert alignment.window_cycles == 2
        assert alignment.offsets == {"slow": 1}


@pytest.mark.parametrize("block, message", [
    ("{kind: window, window_cycles: 2, offsets: {slow: 1}}", None),
    ("{kind: framed}", None),
    ("{kind: loose}", "must be one of"),
    ("{kind: window}", "window_cycles"),
    ("{kind: exact, window_cycles: 2}", "applies only to kind='window'"),
    ("{offsets: {slow: two}}", "must be an integer"),
    ("{offsets: [slow]}", "not a mapping"),
    ("{kind: exact, skew: 1}", "unknown key"),
])
def test_registry_validator_checks_input_alignment(tmp_path, block, message):
    validator = validate_registry(_write_registry(tmp_path, f"    input_alignment: {block}\n"))
    if message is None:
        assert not validator.has_errors()
    else:
        assert any(message in str(e) for e in validator.errors)


# ── checker: offsets and declared alignment ─────────────────────────────────

def _two_branch_graph(slow, fast, alignment=None):
    return LatencyGraph(
        nodes={
            "slow": _node("slow", slow),
            "fast": _node("fast", fast),
            "merge": _node("merge", 1, alignment=alignment),
        },
        edges=[_edge("slow", "merge"), _edge("fast", "merge")],
    )


class TestCheckerAlignment:
    def test_offset_balances_a_declared_skew(self):
        graph = _two_branch_graph(9, 5, InputAlignment(offsets={"slow": 4}))
        (report,) = check_merge_points(graph)
        assert not report.is_mismatch
        assert _totals(report) == {"slow": 5, "fast": 5}

    def test_offset_can_name_the_registry_entry(self):
        graph = _two_branch_graph(9, 5, InputAlignment(offsets={"slow_ref": 4}))
        graph.nodes["slow"].ref = "slow_ref"
        (report,) = check_merge_points(graph)
        assert not report.is_mismatch

    def test_offset_does_not_hide_a_different_skew(self):
        graph = _two_branch_graph(9, 6, InputAlignment(offsets={"slow": 4}))
        (report,) = check_merge_points(graph)
        assert report.is_mismatch and report.delta == 1

    def test_framed_inputs_are_never_a_mismatch(self):
        (report,) = check_merge_points(_two_branch_graph(30, 2, InputAlignment(kind="framed")))
        assert report.alignment == "framed"
        assert not report.is_mismatch

    def test_window_allows_skew_up_to_window_cycles(self):
        (inside,) = check_merge_points(_two_branch_graph(7, 4, InputAlignment(kind="window", window_cycles=3)))
        assert inside.alignment == "window" and not inside.is_mismatch
        (outside,) = check_merge_points(_two_branch_graph(8, 4, InputAlignment(kind="window", window_cycles=3)))
        assert outside.is_mismatch and outside.window_cycles == 3


# ── balance: resolve_auto_delays ────────────────────────────────────────────

class TestResolveAutoDelays:
    def test_short_branch_is_delayed_to_the_long_one(self):
        graph = LatencyGraph(
            nodes={"slow": _node("slow", 9), "fast": _node("fast", 4), "merge": _node("merge", 1)},
            edges=[_edge("slow", "merge"), _edge("fast", "merge", ref=("connection", 1), auto=True)],
        )
        result = resolve_auto_delays(graph)
        assert not result.problems
        assert result.by_ref() == {("connection", 1): 5}
        (report,) = check_merge_points(graph)
        assert not report.is_mismatch

    def test_auto_on_the_long_branch_resolves_to_zero(self):
        graph = LatencyGraph(
            nodes={"slow": _node("slow", 9), "fast": _node("fast", 4), "merge": _node("merge", 1)},
            edges=[
                _edge("slow", "merge", ref=("connection", 0), auto=True),
                _edge("fast", "merge", ref=("connection", 1), auto=True),
            ],
        )
        assert resolve_auto_delays(graph).by_ref() == {("connection", 0): 0, ("connection", 1): 5}

    def test_fixed_delay_already_on_the_edge_is_kept(self):
        graph = LatencyGraph(
            nodes={"slow": _node("slow", 9), "fast": _node("fast", 2), "merge": _node("merge", 1)},
            edges=[
                _edge("slow", "merge"),
                _edge("fast", "merge", ref=("connection", 1), auto=True, cycles=3),
            ],
        )
        # A register_stages on the same connection stays; auto adds the rest.
        assert resolve_auto_delays(graph).by_ref() == {("connection", 1): 4}
        assert {(e.src, e.latency.cycles if e.latency else None) for e in graph.edges} == {
            ("slow", None), ("fast", 7),
        }

    def test_offset_is_honoured(self):
        graph = LatencyGraph(
            nodes={
                "slow": _node("slow", 9),
                "fast": _node("fast", 2),
                "merge": _node("merge", 1, alignment=InputAlignment(offsets={"slow": 4})),
            },
            edges=[_edge("slow", "merge"), _edge("fast", "merge", ref=("connection", 1), auto=True)],
        )
        assert resolve_auto_delays(graph).by_ref() == {("connection", 1): 3}

    def test_framed_merge_needs_no_delay(self):
        graph = LatencyGraph(
            nodes={
                "slow": _node("slow", 9),
                "fast": _node("fast", 2),
                "merge": _node("merge", 1, alignment=InputAlignment(kind="framed")),
            },
            edges=[_edge("slow", "merge"), _edge("fast", "merge", ref=("connection", 1), auto=True)],
        )
        assert resolve_auto_delays(graph).by_ref() == {("connection", 1): 0}

    def test_a_resolved_delay_is_part_of_the_paths_below_it(self):
        # a(3) ─┬─────────────────────── m1 ── m2
        # b(8) ─┘ (auto on a->m1)              │
        # c(20) ───────────────────────────────┘ (auto on m1->m2)
        graph = LatencyGraph(
            nodes={
                "a": _node("a", 3), "b": _node("b", 8), "c": _node("c", 20),
                "m1": _node("m1", 2), "m2": _node("m2", 1),
            },
            edges=[
                _edge("a", "m1", ref=("connection", 0), auto=True),
                _edge("b", "m1"),
                _edge("m1", "m2", ref=("connection", 2), auto=True),
                _edge("c", "m2"),
            ],
        )
        result = resolve_auto_delays(graph)
        # m1 balanced at 8, so the m1 branch arrives at m2 after 8 + 2.
        assert result.by_ref() == {("connection", 0): 5, ("connection", 2): 10}
        assert not any(r.is_mismatch for r in check_merge_points(graph))

    def test_fixed_input_that_arrives_late_is_not_hidden(self):
        # The auto input is the slow one; the fixed fast one stays short.
        graph = LatencyGraph(
            nodes={"slow": _node("slow", 9), "fast": _node("fast", 4), "merge": _node("merge", 1)},
            edges=[_edge("slow", "merge", ref=("connection", 0), auto=True), _edge("fast", "merge")],
        )
        assert resolve_auto_delays(graph).by_ref() == {("connection", 0): 0}
        (report,) = check_merge_points(graph)
        assert report.is_mismatch and report.delta == 5

    def test_unknown_latency_is_a_problem(self):
        graph = LatencyGraph(
            nodes={"slow": _node("slow", None), "fast": _node("fast", 4), "merge": _node("merge", 1)},
            edges=[_edge("slow", "merge"), _edge("fast", "merge", ref=("connection", 1), auto=True)],
        )
        result = resolve_auto_delays(graph)
        assert not result.delays
        assert len(result.problems) == 1 and "'slow'" in result.problems[0]

    def test_mixed_auto_and_fixed_on_one_pair_is_a_problem(self):
        edge = _edge("fast", "merge", ref=("connection", 1), auto=True)
        edge.refs.append(("connection", 2, False))
        graph = LatencyGraph(
            nodes={"slow": _node("slow", 9), "fast": _node("fast", 4), "merge": _node("merge", 1)},
            edges=[_edge("slow", "merge"), edge],
        )
        result = resolve_auto_delays(graph)
        assert not result.delays
        assert "mix" in result.problems[0]

    def test_one_connection_needing_two_depths_is_a_problem(self):
        # One connection fans out to two instances that need different delays.
        graph = LatencyGraph(
            nodes={
                "src": _node("src", 1, module="src"),
                "x0": _node("x0", 5), "x1": _node("x1", 7),
                "m[0]": _node("m[0]", 1, module="m"), "m[1]": _node("m[1]", 1, module="m"),
            },
            edges=[
                _edge("src", "m[0]", ref=("connection", 0), auto=True),
                _edge("src", "m[1]", ref=("connection", 0), auto=True),
                _edge("x0", "m[0]"),
                _edge("x1", "m[1]"),
            ],
        )
        result = resolve_auto_delays(graph)
        assert not result.delays
        assert "different delay" in result.problems[0]

    def test_feedback_loop_is_a_problem(self):
        graph = LatencyGraph(
            nodes={"a": _node("a", 1), "b": _node("b", 1), "c": _node("c", 1)},
            edges=[
                _edge("c", "a"),
                _edge("a", "b"),
                _edge("b", "a", ref=("connection", 2), auto=True),
            ],
        )
        result = resolve_auto_delays(graph)
        assert any("feedback loop" in p for p in result.problems)


# ── whole design ────────────────────────────────────────────────────────────

class TestDesignAutoDelays:
    def test_resolves_from_the_registry_latencies(self, tmp_path):
        result = resolve_design_auto_delays(_write_design(tmp_path))
        assert not result.problems
        assert result.by_ref() == {("connection", 1): 5}

    def test_follows_a_changed_module_latency(self, tmp_path):
        design = _write_design(tmp_path)
        registry = tmp_path / "modules.yml"
        registry.write_text(registry.read_text().replace("latency_cycles: 9", "latency_cycles: 6"))
        assert resolve_design_auto_delays(design).by_ref() == {("connection", 1): 2}

    def test_apply_writes_the_depth_into_the_config(self, tmp_path):
        design = _write_design(tmp_path)
        cfg = DesignConfig.load_relaxed(design)
        apply_auto_delays(cfg, resolve_design_auto_delays(design))
        assert cfg.connections[1].delay_cycles == 5
        assert cfg.connections[0].delay_cycles == 0

    def test_apply_refuses_a_fixed_connection(self, tmp_path):
        design = _write_design(tmp_path, fast_delay="0")
        cfg = DesignConfig.load_relaxed(design)
        result = resolve_design_auto_delays(design)
        result.delays.append(SimpleNamespace(kind="connection", index=1, cycles=5, src="fast", dst="merge"))
        with pytest.raises(ValueError, match="not declared delay_cycles: auto"):
            apply_auto_delays(cfg, result)

    def test_report_lists_the_resolved_depths(self, tmp_path):
        graph = build_graph(_write_design(tmp_path))
        result = resolve_auto_delays(graph)
        out = tmp_path / "latency_check.md"
        render_markdown(graph, check_merge_points(graph), out, auto_delays=result)
        text = out.read_text()
        assert "## delay_cycles: auto" in text
        assert "| connection 1 | `fast` → `merge` | 5 |" in text
        assert "✅ Balanced at `merge`" in text

    def test_latency_check_cli_balances_auto_delays(self, tmp_path, capsys):
        from forge.core.cli.groups.analyze import cmd_latency_check

        args = SimpleNamespace(
            design=str(_write_design(tmp_path)), output=str(tmp_path / "out.md"),
            contracts_from=None, hls_build_root=None, json=True,
        )
        with pytest.raises(SystemExit) as exc:
            cmd_latency_check(args)
        assert exc.value.code == 0
        out = capsys.readouterr().out
        envelope, _ = json.JSONDecoder().raw_decode(out[out.index("{"):])
        assert envelope["metrics"]["auto_delays"] == {"connection[1]": 5}


def _write_contract_design(tmp_path, fast_conn="delay_cycles: auto"):
    (tmp_path / "interfaces").mkdir()
    roles = {
        "slow": {"dout": "output"},
        "fast": {"dout": "output"},
        "merge": {"in_slow": "input", "in_fast": "input"},
    }
    for name, ports in roles.items():
        lines = [
            "ip_interface:",
            f"  module_name: {name}",
            f"  ip_info_key: {name}",
            "  source_type: rtl",
            "  roles:",
            "    clock_primary: {raw_port: ap_clk, direction: input, width: 1}",
            "    reset_primary: {raw_port: ap_rst, direction: input, width: 1}",
        ] + [f"    {p}: {{raw_port: {p}, direction: {d}, width: 8}}" for p, d in ports.items()]
        (tmp_path / "interfaces" / f"{name}.interface.yaml").write_text("\n".join(lines) + "\n")
    registry = tmp_path / "modules.yml"
    registry.write_text(
        "registry_version: '1'\n"
        "modules:\n"
        + "".join(
            f"  - name: {name}\n    kind: rtl\n    top: {name}_top\n    src: [{name}.v]\n"
            f"    interface_contract: interfaces/{name}.interface.yaml\n    latency_cycles: {cycles}\n"
            for name, cycles in (("slow", 9), ("fast", 4), ("merge", 1))
        )
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
        "connections:\n"
        "  - {from: slow, to: merge, port_map: [[dout, in_slow]]}\n"
        f"  - {{from: fast, to: merge, port_map: [[dout, in_fast]], {fast_conn}}}\n"
    )
    return design, registry


def test_gen_top_emits_and_records_the_resolved_delay(tmp_path, capsys):
    from forge.core.cli.main import build_parser

    design, registry = _write_contract_design(tmp_path)
    parsed = build_parser().parse_args([
        "topgen", "gen-top", str(design), "--mode", "verilog",
        "--output", str(tmp_path / "algo_top.v"), "--contracts-from", str(registry),
    ])
    parsed.func(parsed)
    assert "delay_cycles: auto (1 resolved" in capsys.readouterr().out

    top = (tmp_path / "algo_top.v").read_text()
    assert "signal_delay" in top
    assert ".DEPTH(5)" in top.replace(" ", "")
    manifest = json.loads((tmp_path / "build_manifest.json").read_text())
    assert manifest["resolved_auto_delays"] == [
        {"kind": "connection", "index": 1, "src": "fast", "dst": "merge", "cycles": 5},
    ]


def test_gen_top_fails_when_an_auto_delay_cannot_be_resolved(tmp_path, capsys):
    from forge.core.cli.main import build_parser

    design, registry = _write_contract_design(tmp_path)
    registry.write_text(registry.read_text().replace("    latency_cycles: 9\n", ""))
    parsed = build_parser().parse_args([
        "topgen", "gen-top", str(design), "--mode", "verilog",
        "--output", str(tmp_path / "algo_top.v"), "--contracts-from", str(registry),
    ])
    with pytest.raises(SystemExit) as exc:
        parsed.func(parsed)
    assert exc.value.code == 1
    assert "'slow'" in capsys.readouterr().out
    assert not (tmp_path / "algo_top.v").exists()


def test_hls_latency_map_keeps_a_reported_zero():
    from forge.analysis.hls_reports.extractor import HLSModuleReport, latency_map_from_reports

    def report(name, worst, reported):
        return HLSModuleReport(
            module_name=name, solution="solution1", status="ok", error=None,
            latency_worst=worst, latency_reported=reported,
        )

    reports = [report("passthrough", 0, True), report("no_number", 0, False), report("pipe", 7, True)]
    assert latency_map_from_reports(reports) == {"passthrough": 0, "pipe": 7}


def _gen_top(tmp_path, design, registry, mode="verilog"):
    from forge.core.cli.main import build_parser

    output = tmp_path / ("algo_top.v" if mode == "verilog" else "block_design.tcl")
    parsed = build_parser().parse_args([
        "topgen", "gen-top", str(design), "--mode", mode,
        "--output", str(output), "--contracts-from", str(registry),
    ])
    parsed.func(parsed)
    return output.read_text()


def test_gen_top_chains_a_boundary_crossing_and_an_auto_delay(tmp_path, capsys):
    design, registry = _write_contract_design(
        tmp_path, "boundary: slr0_slr1, register_stages: 3, delay_cycles: auto",
    )
    top = _gen_top(tmp_path, design, registry).replace(" ", "")
    # fast(4) + crossing(3) reaches merge 2 cycles before slow(9).
    crossing = top.index("slr_crossing_delay#(")
    delay = top.index("signal_delay#(")
    assert ".DEPTH(3)" in top[crossing:delay]
    assert ".DEPTH(2)" in top[delay:]
    assert ".din(reg_net_fast_merge_dout)" in top[delay:]
    assert ".in_fast(delay_net_fast_merge_dout)" in top
    assert "RegisterStage" not in top

    crossings = json.loads((tmp_path / "algo_top.crossings.json").read_text())["crossings"]
    assert [(c["tag"], c["depth"]) for c in crossings] == [("slr0_slr1", 3)]
    ir = json.loads((tmp_path / "design.ir.json").read_text())
    kinds = sorted(
        (t["kind"], t["cycles"])
        for c in ir["design"]["connections"] if c["producer"]["instance_id"] == "fast"
        for t in c["transformations"]
    )
    assert kinds == [("latency_delay", 2), ("slr_crossing", 3)]


def test_register_stages_and_delay_are_chained(tmp_path, capsys):
    design, registry = _write_contract_design(tmp_path, "register_stages: 2, delay_cycles: 3")
    top = _gen_top(tmp_path, design, registry).replace(" ", "")
    assert ".data_in(net_fast_dout)" in top
    assert ".data_out(reg_net_fast_merge_dout)" in top
    assert ".din(reg_net_fast_merge_dout)" in top
    assert ".in_fast(delay_net_fast_merge_dout)" in top


def test_block_design_chains_a_boundary_crossing_and_a_delay(tmp_path, capsys):
    design, registry = _write_contract_design(
        tmp_path, "boundary: slr0_slr1, register_stages: 3, delay_cycles: auto",
    )
    tcl = _gen_top(tmp_path, design, registry, mode="bd")
    crossing = "bdry_slr0_slr1_dout_to_in_fast"
    assert f"create_bd_cell -type module -reference slr_crossing_delay {crossing}" in tcl
    assert f"set_property CONFIG.DEPTH {{3}} [get_bd_cells {crossing}]" in tcl
    assert "create_bd_cell -type module -reference signal_delay delay_0" in tcl
    assert "set_property CONFIG.DEPTH {2} [get_bd_cells delay_0]" in tcl
    assert f"connect_bd_net [get_bd_pins fast/dout] [get_bd_pins {crossing}/din]" in tcl
    assert f"connect_bd_net [get_bd_pins {crossing}/dout] [get_bd_pins delay_0/din]" in tcl
    assert "connect_bd_net [get_bd_pins delay_0/dout] [get_bd_pins merge/in_fast]" in tcl


def test_the_resolved_ir_carries_the_sized_delay_as_generation_does(tmp_path):
    from forge.ir import build_project_ir, to_json_dict

    design = _write_design(tmp_path)
    ports = [{"name": n, "direction": d, "width": 8, "type": "wire"} for n, d in
             (("dout", "OUT"), ("in_slow", "IN"), ("in_fast", "IN"))]
    ip_info = tmp_path / "ip_info.yaml"
    ip_info.write_text(json.dumps({m: {"ports": ports} for m in ("slow", "fast", "merge")}))

    doc = to_json_dict(build_project_ir(design, ip_info=ip_info))["design"]

    fast = next(c for c in doc["connections"] if c["producer"]["instance_id"] == "fast")
    assert [(t["kind"], t["cycles"]) for t in fast["transformations"]] == [("latency_delay", 5)]
