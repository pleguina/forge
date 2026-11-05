"""A repeated chain written once, its copies and their exceptions, and named
variants of a design in forge.yml.

The copies of a subsystem keep their names when the count grows; an
exception changes one copy and nothing else; a variant (a board build, a
simulation build) changes the count or adds an exception without copying a
connection table; ``forge build``, ``forge inspect`` and a verification
target work on a variant by name.
"""

from __future__ import annotations

import json
import random
import shutil
from pathlib import Path

import pytest
import yaml

from forge.contracts.design_subsystems import expand_subsystems
from forge.contracts.design_variant import load_design_document
from forge.core.cli.main import build_parser
from forge.project.config import ForgeConfig

SCALE = Path(__file__).resolve().parent / "fixtures" / "foreign" / "nested_payload" / "rtl" / "scale.v"
needs_verilator = pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")

DESIGN = """schema_version: '1.0'
part: xcvu9p-flga2104-2L-e
clock_period: 4.0
connect_clock: true
connect_reset: true
registry: modules.yml
subsystems:
  lane:
    modules:
    - {name: pre, ref: scale, parameters: {SHIFT: 1, W: 12}, external_in_ports: [x, x_valid]}
    - {name: post, ref: scale, parameters: {SHIFT: 2, W: 12}, external_out_ports: [y, y_valid]}
    connections:
    - {from: pre, to: post, port_map: [[y, x], [y_valid, x_valid]]}
modules:
- name: lane
  subsystem: lane
  instances: 3
  overrides:
    1: {post: {parameters: {SHIFT: 0, W: 12}}}
"""

REGISTRY = """registry_version: '1'
defaults: {part: xcvu9p-flga2104-2L-e, clock_period: 4.0}
modules:
- {name: scale, kind: rtl, rtl_lang: verilog, top: scale, src: [rtl/scale.v]}
"""


@pytest.fixture
def lanes(tmp_path, monkeypatch):
    (tmp_path / "rtl").mkdir()
    shutil.copy(SCALE, tmp_path / "rtl")
    (tmp_path / "design.yml").write_text(DESIGN)
    (tmp_path / "modules.yml").write_text(REGISTRY)
    (tmp_path / "forge.yml").write_text("project: lanes\ndesign: design.yml\nclock: {port: clk}\n"
                                        "reset: {port: rst_n, active: low}\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _run(capsys, *argv):
    args = build_parser().parse_args(list(argv))
    try:
        args.func(args)
        code = 0
    except SystemExit as exc:
        code = int(exc.code or 0)
    out = capsys.readouterr()
    return code, json.loads(out.out) if "--json" in argv else out.out, out.err


def _expanded(**changes):
    doc = yaml.safe_load(DESIGN)
    doc["modules"][0].update(changes)
    return expand_subsystems(doc, Path("design.yml"))


# ── Expansion ─────────────────────────────────────────────────────────────

def test_each_copy_is_its_blocks_and_wiring_under_a_stable_name():
    doc = _expanded()

    assert [m["name"] for m in doc["modules"]] == [
        "lane_0__pre", "lane_0__post", "lane_1__pre", "lane_1__post", "lane_2__pre", "lane_2__post"]
    assert [(c["from"], c["to"]) for c in doc["connections"]] == [
        ("lane_0__pre", "lane_0__post"), ("lane_1__pre", "lane_1__post"), ("lane_2__pre", "lane_2__post")]
    assert doc["modules"][3]["parameters"] == {"SHIFT": 0, "W": 12}
    assert doc["modules"][1]["parameters"] == {"SHIFT": 2, "W": 12}


def test_more_copies_leave_the_existing_names_alone():
    names = [m["name"] for m in _expanded(instances=5)["modules"]]

    assert names[:6] == [m["name"] for m in _expanded()["modules"]]
    assert names[6:] == ["lane_3__pre", "lane_3__post", "lane_4__pre", "lane_4__post"]


def test_an_exception_can_take_a_block_out_of_one_copy():
    doc = _expanded(overrides={2: {"post": None}})

    assert "lane_2__post" not in [m["name"] for m in doc["modules"]]
    assert ("lane_2__pre", "lane_2__post") not in [(c["from"], c["to"]) for c in doc["connections"]]


def test_an_outside_connection_is_made_once_per_copy_with_its_index():
    doc = yaml.safe_load(DESIGN)
    doc["modules"].append({"name": "merger", "ref": "merger"})
    doc["connections"] = [{"from": "lane.post", "to": "merger", "port_map": [["y", "in_{i}"]]},
                          {"from": "lane[1].post", "to": "merger", "port_map": [["y_valid", "sel"]]}]

    out = expand_subsystems(doc, Path("design.yml"))["connections"][3:]

    assert [(c["from"], c["port_map"]) for c in out] == [
        ("lane_0__post", [["y", "in_0"]]), ("lane_1__post", [["y", "in_1"]]), ("lane_2__post", [["y", "in_2"]]),
        ("lane_1__post", [["y_valid", "sel"]])]


@pytest.mark.parametrize("changes, message", [
    ({"overrides": {3: {"post": None}}}, "copy 3 does not exist"),
    ({"overrides": {0: {"mid": {}}}}, "no block mid in subsystem lane"),
    ({"subsystem": "lanes"}, "no subsystem 'lanes'"),
    ({"ref": "scale"}, "a subsystem use takes"),
])
def test_a_wrong_use_is_named(changes, message):
    with pytest.raises(ValueError, match=message):
        _expanded(**changes)


def test_a_variant_changes_the_count_and_one_copy(lanes):
    (lanes / "design.board.yml").write_text(
        "variant_of: design.yml\nmodules:\n  lane:\n    instances: 4\n"
        "    overrides: {2: {post: {parameters: {SHIFT: 3, W: 12}}}}\n")

    doc = load_design_document(lanes / "design.board.yml")

    params = {m["name"]: m["parameters"]["SHIFT"] for m in doc["modules"]}
    assert params == {"lane_0__pre": 1, "lane_0__post": 2, "lane_1__pre": 1, "lane_1__post": 2,
                      "lane_2__pre": 1, "lane_2__post": 3, "lane_3__pre": 1, "lane_3__post": 2}


# ── forge.yml variants ────────────────────────────────────────────────────

def test_the_copies_are_generated_with_their_own_parameters(lanes, capsys):
    code, _, err = _run(capsys, "build", "--json")

    assert code == 0, err
    top = (lanes / ".forge/generated/algo_top.v").read_text()
    assert "scale #(.SHIFT(0), .W(12)) lane_1__post (" in top
    assert "input [11:0] lane_2__pre_x," in top


def test_a_named_variant_is_built_and_compared_with_the_project_build(lanes, capsys):
    config = yaml.safe_load((lanes / "forge.yml").read_text())
    config["variants"] = {"sim": {"modules": {"lane": {"instances": 2}}}}
    (lanes / "forge.yml").write_text(yaml.safe_dump(config))
    _run(capsys, "build", "--json")

    code, doc, err = _run(capsys, "build", "--variant", "sim", "--json")
    assert code == 0, err
    top = (lanes / ".forge/variants/sim/generated/algo_top.v").read_text()
    assert "lane_1__post" in top and "lane_2__pre" not in top
    assert "lane_2__pre" in (lanes / ".forge/generated/algo_top.v").read_text()

    code, doc, _ = _run(capsys, "inspect", "--variant", "sim", "--impact", "--json")
    assert doc["metrics"]["impact"]["instances"]["removed"] == ["lane_2__post", "lane_2__pre"]
    assert doc["metrics"]["impact"]["instances"]["changed"] == {}


def test_variants_survive_rewriting_forge_yml(lanes):
    config = ForgeConfig.load(lanes)
    config.variants = {"board": "design.board.yml", "sim": {"remove": {"modules": ["lane"]}}}
    config.save()

    assert ForgeConfig.load(lanes).variants == config.variants


def test_an_unknown_variant_is_named(lanes, capsys):
    code, out, err = _run(capsys, "build", "--variant", "nope")

    assert code != 0 and "no variant 'nope' under variants: in forge.yml (have none)" in out + err


@needs_verilator
def test_a_target_simulates_a_variant_and_an_instance_replays_it(lanes, capsys):
    xs = [random.Random(1).randrange(1 << 12) for _ in range(10)]
    (lanes / "in.json").write_text(json.dumps({f"lane_{k}__pre_x": xs for k in range(2)}))
    (lanes / "exp.json").write_text(json.dumps(
        {f"lane_{k}__post_y": [(x << s) & 0xFFF for x in xs] for k, s in ((0, 3), (1, 1))}))
    config = yaml.safe_load((lanes / "forge.yml").read_text())
    config["variants"] = {"sim": {"modules": {"lane": {"instances": 2}}}}
    config["verification"] = {"backends": ["verilator"], "targets": {
        "sim_payload": {"variant": "sim", "input": "in.json", "expected": "exp.json"},
        "post1": {"scope": "instance:lane_1__post", "replay": "sim_payload"}}}
    (lanes / "forge.yml").write_text(yaml.safe_dump(config))

    code, doc, err = _run(capsys, "test", "--json")

    assert code == 0, err
    assert doc["metrics"]["levels"] == {"sim_payload[verilator]": "VERIFIED", "post1[verilator]": "VERIFIED"}
