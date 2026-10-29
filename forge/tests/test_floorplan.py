"""SLR assignment of a block-design top level (forge.integration.floorplan, forge dev framework floorplan)."""

from __future__ import annotations

import collections
import json
import shutil
from pathlib import Path

import pytest

from forge.core.cli.main import build_parser
from forge.integration.floorplan import assign, block_design_cells, link_slrs, write_xdc
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
