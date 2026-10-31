"""HLS ap_memory ports driven and observed by the generated testbench.

A block with ap_ctrl_hs reads one call's words from an input memory (read
latency 2, a page per call) and writes results to an output memory; the
testbench holds both memories, writes each call's page before the call,
runs calls one after another and records every write, which is compared
per call (``order: event`` with ``latency: {min: 0, max: next}``).
"""

from __future__ import annotations

import json
import shutil

import pytest
import yaml

from forge.core.cli.main import build_parser
from forge.verification.protocols import Port, infer

needs_verilator = pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")

BLOCK = """module block(input ap_clk, input ap_rst, input ap_start, output reg ap_done, output reg ap_idle,
  output reg ap_ready, input [3:0] n, input [0:0] bx,
  output reg [3:0] in_mem_address0, output reg in_mem_ce0, input [15:0] in_mem_q0,
  output reg [3:0] out_mem_address0, output reg out_mem_ce0, output reg out_mem_we0, output reg [15:0] out_mem_d0);
  reg busy; reg [3:0] i, k, cnt; reg pg; reg [2:0] vpipe; reg [3:0] ip0, ip1, ip2;
  always @(posedge ap_clk) begin
    ap_done <= 0; ap_ready <= 0; out_mem_we0 <= 0; out_mem_ce0 <= 0; in_mem_ce0 <= 0;
    if (ap_rst) begin busy <= 0; ap_idle <= 1; vpipe <= 0; end
    else if (!busy) begin
      if (ap_start) begin cnt <= n; pg <= bx; i <= 0; k <= 0; busy <= 1; ap_idle <= 0; ap_ready <= 1; end
    end else begin
      if (i < cnt) begin in_mem_address0 <= {pg, i[2:0]}; in_mem_ce0 <= 1; i <= i + 1; end
      vpipe <= {vpipe[1:0], i < cnt}; ip0 <= i; ip1 <= ip0; ip2 <= ip1;
      if (vpipe[2]) begin
        out_mem_address0 <= {pg, ip2[2:0]}; out_mem_ce0 <= 1; out_mem_we0 <= 1; out_mem_d0 <= in_mem_q0 + 1;
        k <= k + 1;
      end
      if (i >= cnt && k == cnt && vpipe == 0) begin busy <= 0; ap_done <= 1; ap_idle <= 1; end
    end
  end
endmodule
"""


def test_ap_memory_ports_are_one_memory_each():
    ports = [Port(n, d, w) for n, d, w in (
        ("ap_start", "in", 1), ("ap_ready", "out", 1), ("ap_done", "out", 1), ("n", "in", 4),
        ("in_mem_address0", "out", 4), ("in_mem_ce0", "out", 1), ("in_mem_q0", "in", 16),
        ("out_mem_address0", "out", 4), ("out_mem_ce0", "out", 1), ("out_mem_we0", "out", 1),
        ("out_mem_d0", "out", 16))]

    ifaces = {i.name: i for i in infer(ports, declared={"in_*": {"protocol": "memory", "latency": 2, "pages": 2}})}

    assert ifaces["in_mem"].protocol == "memory" and ifaces["in_mem"].direction == "in"
    assert ifaces["in_mem"].options == {"latency": 2, "pages": 2, "depth": 16}
    assert ifaces["out_mem"].direction == "out" and ifaces["out_mem"].width == 16
    assert ifaces["call"].fields["done"].name == "ap_done"


def _project(root, expected_last=8):
    (root / "rtl").mkdir()
    (root / "rtl" / "block.v").write_text(BLOCK)
    events = [{"id": "e0", "in_mem": [1, 2, 3], "call": {"n": 3, "bx": 0}},
              {"id": "e1", "in_mem": [], "call": {"n": 0, "bx": 1}},
              {"id": "e2", "in_mem": [7], "call": {"n": 1, "bx": 0}},
              {"id": "e3", "in_mem": [5, 6], "call": {"n": 2, "bx": 1}}]
    expected = [{"id": "e0", "out_mem": [2, 3, 4]}, {"id": "e1", "out_mem": []},
                {"id": "e2", "out_mem": [expected_last]}, {"id": "e3", "out_mem": [7, 6]}]
    (root / "input.json").write_text(json.dumps({"events": events}))
    (root / "expected.json").write_text(json.dumps({"events": expected}))
    (root / "forge.yml").write_text(yaml.safe_dump({
        "project": "mem", "top": "block", "sources": {"rtl": ["rtl/*.v"]},
        "clock": {"port": "ap_clk"}, "reset": {"port": "ap_rst", "active": "high"},
        "verification": {"target": "existing", "backends": ["verilator"],
                         "protocols": {"in_mem": {"protocol": "memory", "latency": 2, "pages": 2}},
                         "compare": {"order": "event", "mode": "latency_window", "latency": {"min": 0, "max": "next"}},
                         "input": {"path": "input.json", "format": "json"},
                         "expected": {"path": "expected.json", "format": "json"}}}))


def _test(root, capsys, monkeypatch):
    monkeypatch.chdir(root)
    args = build_parser().parse_args(["test", "--json"])
    try:
        args.func(args)
    except SystemExit:
        pass
    capsys.readouterr()
    return json.loads(next((root / ".forge/reports/test/auto").glob("*.evidence.json")).read_text())


@needs_verilator
def test_a_block_reading_and_writing_memories_is_checked_call_by_call(tmp_path, capsys, monkeypatch):
    _project(tmp_path)
    doc = _test(tmp_path, capsys, monkeypatch)

    assert doc["status"] == "VERIFIED", doc.get("reason") or doc["interfaces"]
    (out,) = [i for i in doc["interfaces"] if i["interface"] == "out_mem"]
    assert out["matched"] == 6 and doc["checks"]["event_association"] == "passed"


@needs_verilator
def test_a_wrong_word_is_reported_in_its_call(tmp_path, capsys, monkeypatch):
    _project(tmp_path, expected_last=9)
    doc = _test(tmp_path, capsys, monkeypatch)

    (out,) = [i for i in doc["interfaces"] if i["interface"] == "out_mem"]
    assert [(r["kind"], r["event"]) for r in out["rows"]] == [("mismatch", "e2")]
