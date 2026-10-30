"""Verification planned from the design, with a generated testbench (forge.verification.auto).

The simulations run on Verilator, and are skipped without it.
"""

from __future__ import annotations

import json
import random
import shutil
from pathlib import Path

import pytest

from forge.core.cli.main import build_parser
from forge.integration.emp import LinkWord, packet, write_pattern_file
from forge.project.adopt import plan_adoption
from forge.verification import auto
from forge.verification import scoreboard as sb
from forge.verification.auto_tb import Bench, memory, pack
from forge.verification.protocols import Interface, Port, infer

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "foreign" / "simple_pipeline"
needs_verilator = pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")


def _model(samples):
    """simple_pipeline's output for *samples*: every second sample, a two-tap average, a sequence number."""
    out, prev = [], 0
    for seq, d in enumerate(samples[1::2]):
        out.append((seq << 16) | (((d >> 1) + (prev >> 1)) & 0xFFFF))
        prev = d
    return out


# --- data ------------------------------------------------------------------------------------------------------

def test_memory_lines_carry_valid_gaps_and_emp_flags():
    v = Interface("a", "valid", "in", [Port("a", "in", 12)], valid=Port("a_valid", "in", 1))
    assert memory(v, [0x123, 5], gap=1) == ["1123", "0000", "1005", "0000"]
    two = Interface("w", "valid", "in", [Port("lo", "in", 4), Port("hi", "in", 4)], valid=Port("go", "in", 1))
    assert pack(two, {"lo": 0x1, "hi": 0xA}) == 0xA1
    emp = infer([Port("l_data", "in", 8), Port("l_valid", "in", 1), Port("l_start", "in", 1),
                 Port("l_last", "in", 1)])[0]
    assert pack(emp, {"data": 0x5A, "valid": 1, "last": 1}) == (0b101 << 8) | 0x5A
    with pytest.raises(ValueError, match="does not fit 12 bits"):
        memory(v, [1 << 12])


# --- a level built by hand ----------------------------------------------------------------------------------------

_RV = """module rv_double(input clk, input rst,
  input [15:0] a_tdata, input a_tvalid, output a_tready,
  output reg [15:0] y_tdata, output reg y_tvalid, input y_tready);
  assign a_tready = !y_tvalid || y_tready;
  always @(posedge clk)
    if (rst) y_tvalid <= 1'b0;
    else if (a_tready) begin y_tvalid <= a_tvalid; y_tdata <= a_tdata << 1; end
endmodule
"""


def _level(tmp_path, rtl, top, ports, inputs, expected, **kw):
    src = tmp_path / f"{top}.v"
    src.write_text(rtl)
    (tmp_path / "in.json").write_text(json.dumps(inputs))
    exp = None
    if expected is not None:
        (tmp_path / "exp.json").write_text(json.dumps(expected))
        exp = auto.DataRef(tmp_path / "exp.json", kw.pop("fmt", "json"), kw.pop("links_out", {}))
    bench = Bench(top, "clk", "rst", True, infer(ports, exclude=["clk", "rst"]))
    return auto.Level("design", top, [src], [], bench, auto.DataRef(tmp_path / "in.json"), exp,
                      sb.Compare.from_dict(kw.pop("compare", None)), **kw)


_RV_PORTS = [Port("clk", "in", 1), Port("rst", "in", 1), Port("a_tdata", "in", 16), Port("a_tvalid", "in", 1),
             Port("a_tready", "out", 1), Port("y_tdata", "out", 16), Port("y_tvalid", "out", 1),
             Port("y_tready", "in", 1)]


@needs_verilator
def test_ready_valid_holds_under_stalls_and_backpressure(tmp_path):
    xs = [random.Random(4).randrange(1 << 15) for _ in range(50)]
    level = _level(tmp_path, _RV, "rv_double", _RV_PORTS, {"a": xs}, {"y": [x << 1 for x in xs]})
    ev = auto.run(level, "verilator", tmp_path / "work", stall=30, backpressure=40, seed=7)
    assert ev.status == sb.VERIFIED, (ev.doc.get("reason"), ev.doc.get("interfaces"))
    assert ev.doc["interfaces"][0]["matched"] == 50 and ev.doc["inputs_driven"] == {"a": 50}
    assert ev.doc["protocols"] == {"a": "ready_valid@1", "y": "ready_valid@1"}


@needs_verilator
def test_new_data_reuses_the_build_and_new_rtl_does_not(tmp_path):
    level = _level(tmp_path, _RV, "rv_double", _RV_PORTS, {"a": [1, 2]}, {"y": [2, 4]})
    first = auto.run(level, "verilator", tmp_path / "work")
    (tmp_path / "in.json").write_text(json.dumps({"a": [3]}))
    (tmp_path / "exp.json").write_text(json.dumps({"y": [6]}))
    second = auto.run(level, "verilator", tmp_path / "work")
    assert (first.doc["compile_reused"], second.doc["compile_reused"]) == (False, True)
    assert second.status == sb.VERIFIED and second.doc["input"]["sha256"] != first.doc["input"]["sha256"]
    (tmp_path / "rv_double.v").write_text(_RV.replace("a_tdata << 1", "a_tdata << 2"))
    third = auto.run(level, "verilator", tmp_path / "work")
    assert third.doc["compile_reused"] is False and third.doc["compile_key"] != first.doc["compile_key"]
    assert third.status == sb.FAILED


_EMP = """module emp_inc(input clk, input rst,
  input [63:0] l_data, input l_valid, input l_start, input l_last,
  output reg [63:0] q_data, output reg q_valid, output reg q_start, output reg q_last);
  always @(posedge clk) begin
    q_data <= l_data + 64'd1; q_valid <= l_valid & !rst; q_start <= l_start; q_last <= l_last;
  end
endmodule
"""


@needs_verilator
def test_emp_pattern_files_drive_and_check_link_words(tmp_path):
    ports = [Port("clk", "in", 1), Port("rst", "in", 1), Port("l_data", "in", 64), Port("l_valid", "in", 1),
             Port("l_start", "in", 1), Port("l_last", "in", 1), Port("q_data", "out", 64),
             Port("q_valid", "out", 1), Port("q_start", "out", 1), Port("q_last", "out", 1)]
    src_frames = [{0: LinkWord(0x10, valid=True, start=True)}, {0: LinkWord(0x11, valid=True, last=True)}, {},
                  {0: packet(0x20)}]
    write_pattern_file(tmp_path / "source.txt", src_frames, [0])
    sink = [{1: LinkWord(0x11, valid=True, start=True)}, {1: LinkWord(0x12, valid=True, last=True)},
            {1: packet(0x21)}]
    write_pattern_file(tmp_path / "sink.txt", sink, [1])
    src = tmp_path / "emp_inc.v"
    src.write_text(_EMP)
    bench = Bench("emp_inc", "clk", "rst", True, infer(ports, exclude=["clk", "rst"]))
    level = auto.Level("design", "emp_inc", [src], [], bench,
                       auto.DataRef(tmp_path / "source.txt", "empv2", {"l": 0}),
                       auto.DataRef(tmp_path / "sink.txt", "empv2", {"q": 1}))
    ev = auto.run(level, "verilator", tmp_path / "work")
    assert ev.status == sb.VERIFIED, (ev.doc.get("reason"), ev.doc.get("interfaces"))
    assert ev.doc["interfaces"][0]["matched"] == 3


def test_a_missing_simulator_blocks_rather_than_passes(tmp_path, monkeypatch):
    level = _level(tmp_path, _RV, "rv_double", _RV_PORTS, {"a": [1]}, {"y": [2]})
    monkeypatch.setattr(auto.shutil, "which", lambda name: None)
    ev = auto.run(level, "verilator", tmp_path / "work")
    assert ev.status == sb.BLOCKED_MISSING_TOOL and "verilator not on PATH" in ev.doc["reason"]


# --- forge test on an adopted project -----------------------------------------------------------------------------

@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "simple_pipeline"
    shutil.copytree(FIXTURE, root)
    plan_adoption(root).write()
    monkeypatch.chdir(root)
    xs = [random.Random(3).randrange(1 << 16) for _ in range(40)]
    (root / "input.json").write_text(json.dumps({"decimator_sample_in": xs}))
    (root / "expected.json").write_text(json.dumps({"packer_packet_out": _model(xs)}))
    return root


def _test(capsys, *argv):
    args = build_parser().parse_args(["test", *argv])
    try:
        args.func(args)
        code = 0
    except SystemExit as exc:
        code = int(exc.code or 0)
    return code, capsys.readouterr()


def test_the_plan_says_what_it_inferred(project, capsys):
    code, out = _test(capsys, "--plan", "--json")
    assert code == 0, out.err
    doc = json.loads(out.out)
    design = doc["levels"][0]
    assert design["level"] == "design" and design["function"].endswith("NOT_VERIFIED_NO_ORACLE")
    assert [(i["name"], i["protocol"]) for i in design["interfaces"]] == [
        ("decimator_sample_in", "valid@1"), ("packer_packet_out", "valid@1")]
    assert {lv["level"] for lv in doc["levels"]} == {"design", "module:decimator", "module:packer", "module:shaper"}


@needs_verilator
def test_forge_test_verifies_with_input_and_expected_alone(project, capsys):
    code, out = _test(capsys, "--input", "input.json", "--expected", "expected.json",
                      "--backend", "verilator", "--json")
    assert code == 0, out.err
    doc = json.loads(out.out)
    levels = doc["metrics"]["levels"]
    assert levels["design[verilator]"] == "VERIFIED"
    assert levels["module:packer[verilator]"] == "NOT_VERIFIED_NO_ORACLE"
    assert doc["metrics"]["verification"] == "PARTIAL"
    evidence = json.loads((project / ".forge/reports/test/auto/design.verilator.evidence.json").read_text())
    assert evidence["schema"] == "forge.verification_evidence/1"
    assert evidence["expected"]["sha256"] and evidence["compile_key"] and evidence["evidence_id"]
    assert {s["path"].rsplit("/", 1)[-1] for s in evidence["sources"]} >= {"decimator.v", "shaper.v", "packer.v"}


@needs_verilator
def test_a_wrong_expected_word_fails(project, capsys):
    exp = json.loads((project / "expected.json").read_text())
    exp["packer_packet_out"][3] ^= 1
    (project / "expected.json").write_text(json.dumps(exp))
    code, out = _test(capsys, "--input", "input.json", "--expected", "expected.json",
                      "--backend", "verilator", "--level", "design", "--json")
    assert code != 0
    evidence = json.loads((project / ".forge/reports/test/auto/design.verilator.evidence.json").read_text())
    assert [r["kind"] for r in evidence["interfaces"][0]["rows"]] == ["mismatch"]


# --- an HLS block with ap_ctrl_hs ---------------------------------------------------------------------------------

HLS_MAC = Path(__file__).resolve().parents[1] / "fixtures" / "hls_ap_ctrl_hs"


def test_an_ap_ctrl_hs_block_is_a_call_and_a_result():
    from forge.core.utils.hdl_parser import _scan_verilog_ports
    ports = [Port(n, d, w) for n, (d, w) in _scan_verilog_ports(HLS_MAC / "mac.v").items()]
    ifaces = infer(ports, exclude=["ap_clk", "ap_rst"])
    assert [(i.name, i.protocol, [p.name for p in i.ports()]) for i in ifaces] == [
        ("call", "ready_valid", ["a", "b", "c", "ap_start", "ap_ready"]),
        ("result", "valid", ["ap_return", "ap_done"]),
    ]


@pytest.mark.parametrize("backend", [
    pytest.param("verilator", marks=needs_verilator),
    pytest.param("xsim", marks=pytest.mark.skipif(shutil.which("xelab") is None, reason="xsim not on PATH")),
])
def test_hls_calls_return_their_results_at_the_reported_latency(tmp_path, backend):
    from forge.core.utils.hdl_parser import _scan_verilog_ports
    ports = [Port(n, d, w) for n, (d, w) in _scan_verilog_ports(HLS_MAC / "mac.v").items()]
    rng = random.Random(9)
    calls = [{"a": rng.randrange(1 << 12), "b": rng.randrange(1 << 12), "c": rng.randrange(256)} for _ in range(60)]
    (tmp_path / "in.json").write_text(json.dumps({"call": calls}))
    (tmp_path / "exp.json").write_text(json.dumps({"result": [(x["a"] * x["b"] + x["c"]) & 0xFFFFFF for x in calls]}))
    bench = Bench("mac", "ap_clk", "ap_rst", True, infer(ports, exclude=["ap_clk", "ap_rst"]))
    level = auto.Level("design", "mac", sorted(HLS_MAC.glob("*.v")), [], bench,
                       auto.DataRef(tmp_path / "in.json"), auto.DataRef(tmp_path / "exp.json"),
                       sb.Compare.from_dict({"mode": "fixed_latency", "latency": 3}))
    ev = auto.run(level, backend, tmp_path / "work", stall=25, seed=2)
    assert ev.status == sb.VERIFIED, (ev.doc.get("reason"), ev.doc.get("interfaces"))
    assert ev.doc["interfaces"][0]["matched"] == 60
