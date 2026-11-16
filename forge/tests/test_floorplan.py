"""SLR assignment of a block-design top level (forge.integration.floorplan, forge dev framework floorplan)."""

from __future__ import annotations

import collections
import json
import shutil
from pathlib import Path

import pytest

from forge.core.cli.main import build_parser
from forge.integration.floorplan import assign, block_design_cells, cell_modules, link_slrs, write_xdc
from forge.integration.floorplan_report import (
    check_spec, check_tcl, compare, device_slrs, hls_resources, proposal, timing_paths, timing_summary,
)
from forge.project.adopt import plan_adoption

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "foreign" / "simple_pipeline"


def _adj(pairs):
    adj = collections.defaultdict(set)
    for a, b in pairs:
        adj[a].add(b)
        adj[b].add(a)
    return adj


def _cli(capsys, *args):
    parsed = build_parser().parse_args(list(args))
    code = 0
    try:
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    out = capsys.readouterr()
    return code, out.out, out.err


# --- assignment --------------------------------------------------------------------------------------------------

def test_a_tie_between_two_slrs_goes_to_the_lowest_whatever_the_order():
    for order in ([("delay_0", "gb"), ("delay_0", "arb")], [("delay_0", "arb"), ("delay_0", "gb")]):
        res = assign(["gb", "arb", "delay_0"], _adj(order), slr={0: ["gb"], 2: ["arb"]}, links={})
        assert res["delay_0"] == 0


def test_the_majority_of_the_neighbours_wins():
    adj = _adj([("delay_0", "gb"), ("delay_0", "arb"), ("delay_0", "mem")])
    res = assign(["gb", "arb", "mem", "delay_0"], adj, slr={0: ["gb"], 2: ["arb", "mem"]}, links={})
    assert res["delay_0"] == 2


def test_a_reserved_slr_is_only_taken_by_declaration():
    adj = _adj([("delay_0", "pdf"), ("delay_0", "best_sel"), ("delay_0", "arb")])
    res = assign(["pdf", "best_sel", "arb", "delay_0"], adj, slr={1: ["pdf", "best_sel"], 2: ["arb"]},
                 links={}, reserved=[1])
    assert res == {"pdf": 1, "best_sel": 1, "arb": 2, "delay_0": 2}


def test_declared_patterns_win_over_links_and_crossings_are_left_to_the_placer():
    adj = _adj([("bdry_x", "dt_0"), ("bdry_x", "subdet"), ("delay_0", "bdry_x")])
    res = assign(["dt_0", "subdet", "bdry_x", "delay_0"], adj, slr={2: ["subdet", "dt_*"]},
                 links={"dt_0": 0}, skip=["bdry_x"])
    assert res == {"dt_0": 2, "subdet": 2}


def test_neighbours_reach_a_few_hops_and_isolated_cells_stay_free():
    chain = [f"delay_{i}" for i in range(6)]
    adj = _adj([("gb", chain[0])] + list(zip(chain, chain[1:])))
    # listed far to near, so every pass reaches one hop further
    res = assign(["gb", *reversed(chain), "lonely"], adj, slr={0: ["gb"]}, links={})
    assert [res.get(c) for c in chain] == [0, 0, 0, 0, None, None]
    assert "lonely" not in res


# --- links -------------------------------------------------------------------------------------------------------

def _ir(ports, conns):
    return {"design": {
        "top_ports": [{"name": p, "direction": "in", "instance_id": i} for p, i in ports.items()],
        "connections": [{"producer": {"instance_id": a}, "consumer": {"instance_id": b}} for a, b in conns],
    }}


def _resolved(slr_of_port):
    return {"detector_inputs": [{"output": {"algo_port": p}, "endpoint": {"slr": s}} for p, s in slr_of_port.items()]}


def test_a_link_takes_the_instance_that_receives_it_and_the_ones_only_it_feeds():
    ir = _ir({"dt_0_csp_in": "dt_0", "rpc_link_0_csp_in": "rpc_link_0", "rpc_link_1_csp_in": "rpc_link_1"},
             [("rpc_link_0", "rpc_0"), ("cfg_0", "rpc_0"), ("$external", "rpc_0"),
              ("rpc_link_0", "rpc_5"), ("rpc_link_1", "rpc_1"),
              ("dt_0", "subdet"), ("rpc_0", "subdet"), ("rpc_link_0", "merge"), ("rpc_link_1", "merge")])
    links = link_slrs(ir, _resolved({"dt_0_csp_in": 0, "rpc_link_0_csp_in": 1, "rpc_link_1_csp_in": 3}))
    assert links == {"dt_0": 0, "rpc_link_0": 1, "rpc_link_1": 3, "rpc_0": 1, "rpc_5": 1, "rpc_1": 3}


# --- the block design FORGE writes -------------------------------------------------------------------------------

@pytest.fixture
def bd_build(tmp_path, capsys):
    project = tmp_path / "simple_pipeline"
    shutil.copytree(FIXTURE, project)
    plan_adoption(project).write()
    code, out, err = _cli(capsys, "build", str(project / ".forge/project/design.yml"),
                          "--contracts-from", str(project / ".forge/project/modules.yml"),
                          "--consumer-root", str(project), "--mode", "bd",
                          "--output", str(project / "out" / "block_design.tcl"))
    assert code == 0, out + err
    return project / "out"


def test_the_cells_are_the_instances_of_the_design(bd_build):
    cells, adj = block_design_cells((bd_build / "block_design.tcl").read_text())
    ir = json.loads((bd_build / "design.ir.json").read_text())["design"]
    assert set(cells) >= {i["id"] for i in ir["instances"]}
    pairs = {(c["producer"]["instance_id"], c["consumer"]["instance_id"]) for c in ir["connections"]
             if c["producer"].get("instance_id") in cells and c["consumer"].get("instance_id") in cells}
    assert pairs and all(b in adj[a] for a, b in pairs)


def test_floorplan_command_writes_the_assignment(bd_build, tmp_path, capsys):
    ir = json.loads((bd_build / "design.ir.json").read_text())["design"]
    first = next(p for p in ir["top_ports"] if p["direction"] == "in" and p.get("instance_id"))
    resolved = tmp_path / "resolved.json"
    resolved.write_text(json.dumps(_resolved({first["name"]: 3})))
    spec = tmp_path / "floorplan.yml"
    spec.write_text("floorplan:\n  root: '*u_algo_top/bd_i'\n  pblock: 'pblock_slr{slr}'\n")
    out = tmp_path / "slr.xdc"

    code, stdout, err = _cli(capsys, "dev", "framework", "floorplan",
                             "--block-design", str(bd_build / "block_design.tcl"),
                             "--ir", str(bd_build / "design.ir.json"), "--detector-io", str(resolved),
                             "--spec", str(spec), "--out", str(out))

    assert code == 0, err
    xdc = out.read_text()
    assert "set _bd [get_cells -quiet -hier -filter {NAME =~ *u_algo_top/bd_i}]" in xdc
    slr3 = xdc.split("# SLR3")[1].split("{")[1].split("}")[0].split()
    assert first["instance_id"] in slr3
    assert "add_cells_to_pblock [get_pblocks pblock_slr3] $k" in xdc
    assert "SLR3" in stdout


def test_a_spec_without_root_is_refused(bd_build, tmp_path, capsys):
    spec = tmp_path / "floorplan.yml"
    spec.write_text("floorplan:\n  slr: {0: ['*']}\n")
    resolved = tmp_path / "resolved.json"
    resolved.write_text("{}")
    code, _, err = _cli(capsys, "dev", "framework", "floorplan",
                        "--block-design", str(bd_build / "block_design.tcl"),
                        "--ir", str(bd_build / "design.ir.json"), "--detector-io", str(resolved),
                        "--spec", str(spec), "--out", str(tmp_path / "slr.xdc"))
    assert code == 1 and "floorplan.root" in err


def test_without_pblock_only_the_soft_assignment_is_written():
    xdc = write_xdc({"a": 0, "b": 2}, root="*/bd_i")
    assert "set_property USER_SLR_ASSIGNMENT SLR2 $k" in xdc
    assert "add_cells_to_pblock" not in xdc


# --- proposal report and comparison with the implemented design --------------------------------------------------

_CELLS = ["dec0", "dec1", "xing", "sorter", "out"]
_NETS = _adj([("dec0", "xing"), ("xing", "sorter"), ("dec1", "sorter"), ("sorter", "out")])
_RES = {"dec0": 2, "dec1": 2, "xing": 2, "sorter": 1, "out": 1}


def test_the_report_is_a_proposal_with_load_and_crossings_per_slr():
    r = proposal(_RES, _CELLS, _NETS, {"part": "xcvu13p-flga2577-1-e"}, registered=["xing"],
                 resources={"sorter": {"LUT": 100, "DSP": 2}, "out": {"LUT": 5}})
    assert r["kind"] == "placement_proposal" and r["timing_closed"] is False and r["slrs"] == 4
    assert r["per_slr"]["1"] == {"cells": 2, "resources": {"LUT": 105, "FF": 0, "BRAM": 0, "DSP": 2, "URAM": 0}}
    assert r["crossings"]["nets"] == {"1-2": 2}
    assert r["crossings"]["unregistered"] == [["dec1", "sorter"]]
    assert [i["level"] for i in r["issues"]] == ["warning"]


def test_over_budget_over_capacity_and_off_device_are_errors():
    spec = {"slrs": 3, "crossing_budget": {"1-2": 1}, "capacity": {1: {"LUT": 50}}}
    r = proposal(dict(_RES, out=3), _CELLS, _NETS, spec, registered=["xing"], resources={"sorter": {"LUT": 100}})
    errors = [i["message"] for i in r["issues"] if i["level"] == "error"]
    assert "out is assigned to SLR3, not on the device" in errors
    assert "SLR1: 100 LUT of a capacity of 50" in errors
    assert "SLR 1-2: 2 nets cross, the budget is 1" in errors


def test_spec_checks():
    issues = check_spec({"slr": {0: ["dec*"], 1: ["dec1", "nothing"], 5: ["out"]}, "reserved": [7]}, _CELLS, 4)
    assert {"level": "error", "message": "dec1 is declared in SLR0 and SLR1"} in issues
    assert {"level": "warning", "message": "slr 1: 'nothing' matches no cell"} in issues
    assert {"level": "error", "message": "slr 5: the device has SLRs 0-3"} in issues
    assert {"level": "error", "message": "reserved 7: the device has SLRs 0-3"} in issues


def test_an_unknown_part_needs_slrs():
    with pytest.raises(ValueError, match="give slrs"):
        device_slrs({"part": "xc7a35t"})
    assert device_slrs({"slrs": 2}) == 2


def test_resources_come_from_the_module_behind_each_cell():
    tcl = ("create_bd_cell -type module -reference sorter_top sorter\n"
           "create_bd_cell -type ip -vlnv cern.ch:hls:dec:1.0 dec0\n")
    assert cell_modules(tcl) == {"sorter": "sorter_top", "dec0": "dec"}
    metrics = {"modules": {"dec": {"resources": {"used": {"LUT": 7}}}}}
    assert hls_resources(metrics, cell_modules(tcl)) == {"dec0": {"LUT": 7}}


_SUMMARY = """
------------------------------------------------------------------------------------------------
| Design Timing Summary
| ---------------------
------------------------------------------------------------------------------------------------

    WNS(ns)      TNS(ns)  TNS Failing Endpoints  TNS Total Endpoints      WHS(ns)
    -------      -------  ---------------------  -------------------      -------
     -0.215      -12.480                    210               812345        0.010
"""

_PATHS = """
Slack (VIOLATED) :        -0.215ns  (required time - arrival time)
  Source:                 pload/u_algo_top/bd_i/sorter/inst/r_reg[3]/C
  Destination:            pload/u_algo_top/bd_i/out/inst/q_reg[0]/D
Slack (MET) :             0.150ns  (required time - arrival time)
  Source:                 pload/u_algo_top/bd_i/dec0/inst/a_reg/C
  Destination:            pload/u_algo_top/bd_i/xing/inst/b_reg/D
"""


def test_vivado_reports_are_read():
    assert timing_summary(_SUMMARY) == {"wns_ns": -0.215, "tns_ns": -12.48}
    assert [p["slack_ns"] for p in timing_paths(_PATHS)] == [-0.215, 0.15]
    with pytest.raises(ValueError, match="no Design Timing Summary"):
        timing_summary("nothing")


def test_the_comparison_reports_moved_cells_and_slack_by_cell(tmp_path):
    report = proposal(_RES, _CELLS, _NETS, {"slrs": 4})
    placed = tmp_path / "placed.csv"
    placed.write_text("cell,slrs\ndec0,SLR2\ndec1,SLR2\nxing,SLR1 SLR2\nsorter,SLR1\nout,\n")
    r = compare(report, placed, summary=_SUMMARY, paths=_PATHS)
    assert r["placed_as_proposed"] == 3
    assert r["moved"] == {"xing": {"proposed": 2, "placed": [1, 2]}, "out": {"proposed": 1, "placed": []}}
    assert r["not_found"] == ["out"]
    assert r["timing"]["wns_ns"] == -0.215
    assert r["slack_histogram_ns"]["< -0.2"] == 1 and r["slack_histogram_ns"]["< 0.3"] == 1
    assert r["worst_slack_by_cell_ns"] == {"sorter": -0.215, "out": -0.215, "dec0": 0.15, "xing": 0.15}


def test_check_tcl_lists_every_assigned_cell():
    tcl = check_tcl(_RES, root="*bd_i", out_csv="placed.csv")
    assert "foreach c {dec0 dec1 out sorter xing} {" in tcl and "get_slrs -quiet -of_objects $prims" in tcl


def test_floorplan_command_writes_the_report_and_compare_reads_it(bd_build, tmp_path, capsys):
    ir = json.loads((bd_build / "design.ir.json").read_text())["design"]
    first = next(p for p in ir["top_ports"] if p["direction"] == "in" and p.get("instance_id"))
    resolved = tmp_path / "resolved.json"
    resolved.write_text(json.dumps(_resolved({first["name"]: 3})))
    spec = tmp_path / "floorplan.yml"
    spec.write_text("floorplan:\n  root: '*bd_i'\n  part: xcvu13p-flga2577-1-e\n")
    report = tmp_path / "fp.json"
    code, stdout, err = _cli(capsys, "dev", "framework", "floorplan",
                             "--block-design", str(bd_build / "block_design.tcl"),
                             "--ir", str(bd_build / "design.ir.json"), "--detector-io", str(resolved),
                             "--spec", str(spec), "--out", str(tmp_path / "slr.xdc"), "--report", str(report))
    assert code == 0, err
    data = json.loads(report.read_text())
    assert data["kind"] == "placement_proposal" and (tmp_path / "fp.check.tcl").exists()
    assert "placement proposal" in (tmp_path / "slr.xdc").read_text()

    placed = tmp_path / "placed.csv"
    placed.write_text("cell,slrs\n" + "".join(f"{c},SLR{s}\n" for c, s in data["assignment"].items()))
    code, stdout, err = _cli(capsys, "dev", "framework", "floorplan-compare", "--report", str(report),
                             "--placed", str(placed), "--out", str(tmp_path / "cmp.json"))
    assert code == 0, err
    n = len(data["assignment"])
    assert f"{n} of {n} cells placed as proposed" in stdout
