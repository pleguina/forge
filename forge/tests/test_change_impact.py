"""What a design change affects, by stable name (`forge inspect --impact`).

A latency change on one branch of a fork names the merge whose inputs it
misaligns; a width, parameter, register stage or instance change names what
it touches and what lies downstream; the verification targets that cover a
changed instance are the ones to run again.
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import yaml

from forge.analysis.impact import affected_targets, compare
from forge.core.cli.main import build_parser
from forge.project.adopt import plan_adoption

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "foreign" / "simple_pipeline"


def _module(name, latency=None, **ports):
    return {"name": name, "kind": "rtl", "top": name, "parameters": {}, "latency": latency,
            "source_files": [f"rtl/{name}.v"], "rtl_sources": [f"/p/rtl/{name}.v"], "interfaces": [],
            "ports": [{"name": p, "direction": d, "width": w} for p, (d, w) in ports.items()]}


def _wire(a, b, port="d", width=8, stages=0):
    return {"id": f"{a}.{port}->{b}.{port}", "producer": {"instance_id": a, "port": port},
            "consumer": {"instance_id": b, "port": port}, "wiring_method": "port_map",
            "matching_evidence": {"producer_width": width, "consumer_width": width},
            "transformations": [{"id": f"x:{a}.{b}", "kind": "pipeline_register", "cycles": stages}] if stages else []}


def _fork_and_merge():
    """src feeds fast and slow, which both feed join; join feeds sink."""
    modules = [_module(m, latency={"kind": "fixed", "cycles": 1}, d=("in", 8)) for m in
               ("src", "fast", "slow", "join", "sink")]
    return {"modules": modules,
            "instances": [{"id": m["name"], "module": m["name"], "clock_domain": "ap_clk"} for m in modules],
            "connections": [_wire("src", "fast"), _wire("src", "slow"), _wire("fast", "join", "a"),
                            _wire("slow", "join", "b"), _wire("join", "sink")]}


def test_nothing_changed_is_said_plainly():
    design = _fork_and_merge()

    assert compare(design, copy.deepcopy(design)).empty


def test_a_latency_change_on_one_branch_names_the_merge_it_misaligns():
    before = _fork_and_merge()
    after = copy.deepcopy(before)
    after["modules"][2]["latency"] = {"kind": "fixed", "cycles": 3}

    impact = compare(before, after)

    assert impact.modules == {"slow": ["latency: 1 cycles -> 3 cycles"]}
    assert impact.affected == ["slow"] and impact.downstream == ["join", "sink"]
    assert impact.merges == [{"instance": "join", "changed": ["slow"], "unchanged": ["fast"]}]


def test_a_register_stage_on_a_branch_is_a_timing_change_too():
    before = _fork_and_merge()
    after = copy.deepcopy(before)
    after["connections"][3] = _wire("slow", "join", "b", stages=2)

    impact = compare(before, after)

    assert impact.connections_changed == {"slow.b->join.b": ["timing: none -> pipeline_register 2 cycle(s)"]}
    assert impact.merges == [{"instance": "join", "changed": ["slow"], "unchanged": ["fast"]}]


def test_widths_parameters_and_instances_are_named():
    before = _fork_and_merge()
    after = copy.deepcopy(before)
    after["modules"][1]["ports"][0]["width"] = 12
    after["modules"][1]["parameters"] = {"W": 12}
    after["instances"].append({"id": "spare", "module": "sink", "clock_domain": "ap_clk"})
    after["instances"][0]["clock_domain"] = "link_clk"

    impact = compare(before, after)

    assert impact.modules["fast"] == ["port d: 8 -> 12 bit", "parameter W: unset -> 12"]
    assert impact.instances_added == ["spare"]
    assert impact.instances_changed == {"src": ["clock domain: ap_clk -> link_clk"]}
    assert impact.merges == []


def test_a_source_edit_is_a_change_even_with_the_same_ports():
    design = _fork_and_merge()

    impact = compare(design, copy.deepcopy(design), changed_files=["/p/rtl/fast.v"])

    assert impact.sources_changed == {"fast": ["/p/rtl/fast.v"]} and impact.affected == ["fast"]


def test_the_targets_covering_a_change_are_the_ones_to_rerun():
    before = _fork_and_merge()
    after = copy.deepcopy(before)
    after["modules"][2]["latency"] = {"kind": "fixed", "cycles": 3}
    targets = {"payload": {"scope": "design"}, "slow": {"scope": "instance:slow", "replay": "payload"},
               "fast": {"scope": "instance:fast", "replay": "payload"},
               "front": {"scope": "subsystem", "instances": ["src", "fast"]}}

    rerun = affected_targets(compare(before, after), targets)

    assert rerun == {"payload": "the design changed", "slow": "instance slow changed",
                     "fast": "the traffic payload records will change once it runs again"}


def test_forge_inspect_impact_compares_with_the_last_build(tmp_path, monkeypatch, capsys):
    root = tmp_path / "simple_pipeline"
    shutil.copytree(FIXTURE, root)
    plan_adoption(root).write()
    monkeypatch.chdir(root)

    def run(*argv):
        args = build_parser().parse_args(list(argv))
        try:
            args.func(args)
        except SystemExit:
            pass
        return capsys.readouterr().out

    run("build", "--json")
    config = yaml.safe_load((root / "forge.yml").read_text())
    config["modules"] = {"shaper": {"latency": 2}}
    (root / "forge.yml").write_text(yaml.safe_dump(config))
    (root / "rtl" / "packer.v").write_text((root / "rtl" / "packer.v").read_text() + "// edited\n")

    doc = json.loads(run("inspect", "--impact", "--json"))

    impact = doc["metrics"]["impact"]
    assert impact["modules"] == {"shaper": ["latency: undeclared -> 2 cycles"]}
    assert impact["sources_changed"] == {"packer": ["rtl/packer.v"]}
    assert doc["metrics"]["rerun"]["design"] == "the design changed"
    assert ".forge/generated/algo_top.v" in doc["metrics"]["regenerate"]
    assert "## Merges" not in (root / ".forge/reports/impact.md").read_text()


def test_a_change_to_many_numbered_copies_reads_as_one_line():
    from forge.analysis.impact import render_markdown

    before = {"modules": [_module("link", d=("in", 8)), _module("merge", d=("in", 8))],
              "instances": [{"id": f"link_{k}", "module": "link"} for k in range(12)] + [{"id": "merge", "module": "merge"}],
              "connections": [_wire(f"link_{k}", "merge", f"in_{k}") for k in range(12)]}
    after = copy.deepcopy(before)
    for k in range(12):
        after["connections"][k] = _wire(f"link_{k}", "merge", f"in_{k}", stages=1)

    text = render_markdown(compare(before, after), {}, [], "the last build")

    assert "- **link_*.in_*->merge.in_*** (12): timing: none -> pipeline_register 1 cycle(s)" in text
    assert "- Changed: link_0..11" in text


def test_an_input_left_without_its_driver_is_named():
    before = _fork_and_merge()
    after = copy.deepcopy(before)
    del after["instances"][2], after["connections"][3], after["connections"][1]   # slow and its wires

    impact = compare(before, after)

    assert impact.instances_removed == ["slow"]
    assert impact.instances_changed == {"join": ["input b: no longer driven (tied off)"]}
