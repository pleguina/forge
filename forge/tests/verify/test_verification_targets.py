"""Named verification targets: what is checked, what decides the result, and
replaying one instance or subsystem from what a payload run recorded.

``verification.targets`` names each thing to check with its scope (the
design, a module, an instance, a subsystem) and its data. Only the required
targets decide the project's status, so a payload comparison that passed is
not reported as PARTIAL because a module has no reference of its own. An
instance or subsystem target can replay the traffic a design target recorded
at its boundary: a regression reference that runs without the payload.
"""

from __future__ import annotations

import json
import random
import shutil
from pathlib import Path

import pytest
import yaml

from forge.core.cli.main import build_parser
from forge.project.adopt import plan_adoption
from forge.project.hdl_scan import split_verilog_modules, verilog_instances
from forge.project.status import FAIL, evaluate
from forge.project.verification_state import latest_results, render_markdown

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "foreign"
needs_verilator = pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")


def _run(capsys, *argv):
    args = build_parser().parse_args(list(argv))
    try:
        args.func(args)
        code = 0
    except SystemExit as exc:
        code = int(exc.code or 0)
    out = capsys.readouterr()
    return code, json.loads(out.out) if "--json" in argv else out.out, out.err


def _configure(root: Path, verification: dict, **top) -> None:
    config = yaml.safe_load((root / "forge.yml").read_text())
    config.update(top)
    config["verification"] = verification
    (root / "forge.yml").write_text(yaml.safe_dump(config, sort_keys=False))


# ── An existing top: the payload and one of its instances ─────────────────

@pytest.fixture
def nested(tmp_path, monkeypatch):
    root = tmp_path / "nested_payload"
    shutil.copytree(FIXTURES / "nested_payload", root)
    rng = random.Random(9)
    events = [[rng.randrange(1 << 16) for _ in range(5)] for _ in range(6)]
    (root / "in.json").write_text(json.dumps(
        {"events": [{"id": f"run1:{k}", "sample": v} for k, v in enumerate(events)]}))
    (root / "exp.json").write_text(json.dumps(
        {"events": [{"id": f"run1:{k}", "result": [(x << 3) & 0xFFFF for x in v]} for k, v in enumerate(events)]}))
    (root / "forge.yml").write_text("project: nested\ntop: payload_top\nclock: {port: clk}\n"
                                    "reset: {port: rst_n, active: low}\n")
    _configure(root, {"target": "existing", "filelist": "sim/files.f", "backends": ["verilator"], "targets": {
        "payload": {"input": "in.json", "expected": "exp.json"},
        "fine": {"scope": "instance:u_fine", "replay": "payload"}}})
    monkeypatch.chdir(root)
    return root


def test_the_instances_of_a_module_are_read_with_their_connections():
    text = (FIXTURES / "nested_payload" / "rtl" / "payload_top.v").read_text()
    top = dict(split_verilog_modules(text))["payload_top"]

    fine = {i.label: i for i in verilog_instances(top, "payload_top")}["u_fine"]

    assert fine.module == "scale" and not fine.positional
    assert fine.parameters == {"SHIFT": "2", "W": "`PAYLOAD_W"}
    assert fine.ports["x"] == "mid" and fine.ports["clk"] == "clk"


def test_an_instance_is_planned_with_its_own_parameters(nested, capsys):
    code, plan, err = _run(capsys, "test", "--plan", "--json")

    assert code == 0, err
    levels = {lv["level"]: lv for lv in plan["levels"]}
    assert levels["fine"]["scope"] == "instance:u_fine" and levels["fine"]["top"] == "scale"
    assert levels["fine"]["parameters"] == {"SHIFT": 2, "W": 16}
    assert levels["fine"]["input"] == "recorded by payload"
    assert [lv["level"] for lv in plan["levels"]] == ["payload", "fine"]


def test_an_inverting_reset_connection_flips_the_instance_reset(nested, capsys):
    top = nested / "rtl" / "payload_top.v"
    top.write_text(top.read_text().replace(".rst_n(rst_n),\n        .x(mid)", ".rst_n(~rst_n),\n        .x(mid)"))

    _, plan, _ = _run(capsys, "test", "--plan", "--json")

    fine = next(lv for lv in plan["levels"] if lv["level"] == "fine")
    assert fine["reset"] == {"port": "rst_n", "active": "high"}


def test_an_unknown_instance_is_named_by_check_and_test(nested, capsys):
    config = yaml.safe_load((nested / "forge.yml").read_text())
    config["verification"]["targets"]["fine"]["scope"] = "instance:u_finer"
    (nested / "forge.yml").write_text(yaml.safe_dump(config))

    section = next(s for s in evaluate(nested).sections if s.name == "Verification")
    code, _, err = _run(capsys, "test", "--plan")

    assert section.state == FAIL and "ATG044" in section.codes and "u_finer" in section.detail
    assert code != 0 and "has no instance 'u_finer' (has u_coarse, u_fine)" in err


@needs_verilator
def test_an_instance_replays_what_the_payload_run_recorded(nested, capsys):
    code, doc, err = _run(capsys, "test", "--json")
    assert code == 0, err
    assert doc["metrics"]["targets"]["fine"] == {
        "scope": "instance:u_fine", "required": True, "reference": "derived", "status": "VERIFIED"}
    assert doc["metrics"]["targets"]["payload"]["reference"] == "functional"

    scale = nested / "rtl" / "scale.v"
    scale.write_text(scale.read_text().replace("y       <= x << SHIFT;", "y       <= (x << SHIFT) + 1;"))
    code, doc, _ = _run(capsys, "test", "--target", "fine", "--json")

    assert code == 1 and doc["metrics"]["levels"] == {"fine[verilator]": "FAILED"}
    records = {r.name: r for r in latest_results(nested / ".forge" / "reports")}
    assert records["payload[verilator]"].status == "VERIFIED" and "scale.v" in records["payload[verilator]"].stale
    assert "| fine[verilator] | FAILED | yes | recorded traffic |" in render_markdown(list(records.values()))


@needs_verilator
def test_some_events_are_replayed_without_replacing_the_latest_results(nested, capsys):
    _run(capsys, "test", "--target", "payload", "--json")
    latest = (nested / ".forge/reports/test/auto/payload.verilator.evidence.json").read_text()

    code, doc, err = _run(capsys, "test", "--target", "payload", "--event-list", "2,run1:4", "--json")

    assert code == 0, err
    assert doc["metrics"]["events"] == ["2", "run1:4"]
    assert (nested / ".forge/reports/test/auto/payload.verilator.evidence.json").read_text() == latest
    run = Path(next(a for a in doc["artifacts"] if a.endswith("Z")))
    evidence = json.loads((run / "payload.verilator.evidence.json").read_text())
    assert evidence["events"] == ["run1:2", "run1:4"] and evidence["interfaces"][0]["expected"] == 10
    traces = nested / ".forge/verify/payload/verilator/module_traces/u_fine"
    assert json.loads((traces / "derived.json").read_text())["events"] == ["run1:2", "run1:4"]
    assert len(json.loads((traces / "input.json").read_text())["x"]) == 10


def test_events_are_selected_only_from_data_that_names_them(nested, capsys):
    (nested / "plain.json").write_text(json.dumps({"sample": [1, 2, 3]}))
    (nested / "plain_exp.json").write_text(json.dumps({"result": [8, 16, 24]}))
    _configure(nested, {"target": "existing", "filelist": "sim/files.f", "backends": ["verilator"],
                        "input": "plain.json", "expected": "plain_exp.json"})
    if shutil.which("verilator") is None:
        pytest.skip("verilator not on PATH")

    code, _, err = _run(capsys, "test", "--event-id", "1")

    assert code != 0 and "names no events to select from" in err


# ── A generated design: modules, instances and a subsystem ────────────────

def _expected(samples):
    out, prev = [], 0
    for seq, d in enumerate(samples[1::2]):
        out.append((seq << 16) | (((d >> 1) + (prev >> 1)) & 0xFFFF))
        prev = d
    return out


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    root = tmp_path / "simple_pipeline"
    shutil.copytree(FIXTURES / "simple_pipeline", root)
    plan_adoption(root).write()
    xs = [random.Random(3).randrange(1 << 16) for _ in range(40)]
    (root / "input.json").write_text(json.dumps({"decimator_sample_in": xs}))
    (root / "expected.json").write_text(json.dumps({"packer_packet_out": _expected(xs)}))
    monkeypatch.chdir(root)
    return root


@needs_verilator
def test_a_payload_pass_is_not_partial_for_modules_without_a_reference(pipeline, capsys):
    _configure(pipeline, {"backends": ["verilator"], "input": "input.json", "expected": "expected.json"})

    code, out, err = _run(capsys, "test")

    assert code == 0, err
    assert "Required: design VERIFIED (reference comparison); not required: module:decimator " \
           "NOT_VERIFIED_NO_ORACLE (no reference supplied)" in out


@needs_verilator
def test_a_target_that_is_not_required_does_not_decide_the_status(pipeline, capsys):
    (pipeline / "wrong.json").write_text(json.dumps({"shaped": [1, 2, 3]}))
    (pipeline / "shaper_in.json").write_text(json.dumps({"decimated": [2, 4, 6]}))
    _configure(pipeline, {"backends": ["verilator"], "targets": {
        "payload": {"input": "input.json", "expected": "expected.json"},
        "shaper": {"scope": "module:shaper", "input": "shaper_in.json", "expected": "wrong.json",
                   "required": False}}})

    code, doc, _ = _run(capsys, "test", "--require-verified", "--json")

    assert code == 0 and doc["metrics"]["verification"] == "VERIFIED"
    assert doc["metrics"]["targets"]["shaper"]["status"] == "FAILED"
    assert [d["severity"] for d in doc["diagnostics"]] == ["warning"]


@needs_verilator
def test_a_subsystem_is_built_from_the_design_and_replayed(pipeline, capsys):
    _configure(pipeline, {"backends": ["verilator"], "targets": {
        "payload": {"input": "input.json", "expected": "expected.json"},
        "front": {"scope": "subsystem", "instances": ["decimator", "shaper"], "replay": "payload"}}})

    code, doc, err = _run(capsys, "test", "--json")

    assert code == 0, err
    assert doc["metrics"]["targets"]["front"]["status"] == "VERIFIED"
    design = yaml.safe_load((pipeline / ".forge/verify/front/design.yml").read_text())
    assert [m["name"] for m in design["modules"]] == ["decimator", "shaper"]
    shaper = design["modules"][1]
    assert shaper["external_out_ports"] == ["shaped", "shaped_valid"]
    assert [(c["from"], c["to"]) for c in design["connections"]] == [("decimator", "shaper")]
    top = (pipeline / ".forge/verify/front/generated/front_top.v").read_text()
    assert "output [15:0] shaper_shaped," in top and "packer" not in top
    assert "packer" in (pipeline / ".forge/generated/algo_top.v").read_text()


def test_a_replay_names_a_design_target(pipeline, capsys):
    _configure(pipeline, {"targets": {"one": {"scope": "instance:shaper", "replay": "nothing"}}})

    code, _, err = _run(capsys, "test", "--plan")

    assert code != 0 and "'nothing' is not a design target" in err


# ── An authored design: one RTL module in two blocks with their parameters ─

AUTHORED_DESIGN = """schema_version: '1.0'
part: xcvu9p-flga2104-2L-e
clock_period: 4.0
connect_clock: true
connect_reset: true
registry: modules.yml
modules:
- {name: coarse, ref: scale, parameters: {SHIFT: 1, W: 12}, external_in_ports: [x, x_valid]}
- {name: fine, ref: scale, parameters: {SHIFT: 2, W: 12}, external_out_ports: [y, y_valid]}
connections:
- {from: coarse, to: fine, port_map: [[y, x], [y_valid, x_valid]]}
"""

AUTHORED_REGISTRY = """registry_version: '1'
defaults: {part: xcvu9p-flga2104-2L-e, clock_period: 4.0}
modules:
- {name: scale, kind: rtl, rtl_lang: verilog, top: scale, src: [rtl/scale.v]}
"""


def test_a_module_level_simulates_the_block_with_its_parameters(tmp_path, monkeypatch, capsys):
    (tmp_path / "rtl").mkdir()
    shutil.copy(FIXTURES / "nested_payload" / "rtl" / "scale.v", tmp_path / "rtl")
    (tmp_path / "design.yml").write_text(AUTHORED_DESIGN)
    (tmp_path / "modules.yml").write_text(AUTHORED_REGISTRY)
    (tmp_path / "forge.yml").write_text("project: authored\ndesign: design.yml\nclock: {port: clk}\n"
                                        "reset: {port: rst_n, active: low}\n")
    monkeypatch.chdir(tmp_path)

    code, plan, err = _run(capsys, "test", "--plan", "--json")

    assert code == 0, err
    fine = next(lv for lv in plan["levels"] if lv["level"] == "module:fine")
    assert fine["parameters"] == {"SHIFT": 2, "W": 12} and fine["interfaces"][0]["width"] == 12
