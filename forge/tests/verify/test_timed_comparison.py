"""Objects compared in any order within an event, with the event fixed by time.

A simulated payload whose events are named by the input's packets: with
``order: event`` and a latency mode, a word counts for the event whose
latency window holds it, so the right values at the wrong time fail; with
``framing``, packet boundaries are checked; the evidence says which
properties were checked and which were not.
"""

from __future__ import annotations

import json
import shutil

import pytest
import yaml

from forge.core.cli.main import build_parser
from forge.integration.emp import packet, write_pattern_file

pytestmark = pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")

PAYLOAD = """module emp_inc(input clk, input rst,
  input [63:0] l_data, input l_valid, input l_start, input l_last,
  output reg [63:0] q_data, output reg q_valid, output reg q_start, output reg q_last);
  always @(posedge clk) begin
    q_data <= l_data + 64'd1; q_valid <= l_valid & !rst; q_start <= l_start; q_last <= LAST;
  end
endmodule
"""


def _project(tmp_path, compare, last="l_last"):
    (tmp_path / "rtl").mkdir(exist_ok=True)
    (tmp_path / "rtl" / "emp_inc.v").write_text(PAYLOAD.replace("LAST", last))
    write_pattern_file(tmp_path / "source.txt", [{0: packet(0x10)}, {}, {}, {0: packet(0x20)}, {}, {}], [0])
    write_pattern_file(tmp_path / "sink.txt", [{1: packet(0x11)}, {}, {}, {1: packet(0x21)}, {}, {}], [1])
    for side in ("source", "sink"):
        (tmp_path / f"{side}.events.json").write_text(json.dumps(["bx0", "bx1"]))
    (tmp_path / "forge.yml").write_text(yaml.safe_dump({
        "project": "emp", "top": "emp_inc", "sources": {"rtl": ["rtl/*.v"]},
        "clock": {"port": "clk"}, "reset": {"port": "rst", "active": "high"},
        "verification": {
            "target": "existing", "backends": ["verilator"], "links": {"l": 0, "q": 1}, "compare": compare,
            "input": {"path": "source.txt", "format": "empv2", "events": "source.events.json"},
            "expected": {"path": "sink.txt", "format": "empv2", "events": "sink.events.json"}}}))


def _test(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(["test", "--json"])
    try:
        args.func(args)
    except SystemExit:
        pass
    capsys.readouterr()
    evidence = sorted((tmp_path / ".forge" / "reports" / "test" / "auto").glob("*.evidence.json"))
    return json.loads(evidence[0].read_text())


def test_objects_in_their_events_latency_window_verify(tmp_path, capsys, monkeypatch):
    _project(tmp_path, {"order": "event", "mode": "fixed_latency", "latency": 2, "framing": True})
    doc = _test(tmp_path, capsys, monkeypatch)

    assert doc["status"] == "VERIFIED", doc["interfaces"]
    assert doc["checks"]["event_association"] == doc["checks"]["latency"] == doc["checks"]["framing"] == "passed"


def test_the_right_values_at_the_wrong_time_fail(tmp_path, capsys, monkeypatch):
    _project(tmp_path, {"order": "event", "mode": "latency_window", "latency": {"min": 3, "max": 4}})
    doc = _test(tmp_path, capsys, monkeypatch)

    assert doc["status"] == "FAILED"
    rows = doc["interfaces"][0]["rows"]
    assert {r["kind"] for r in rows} == {"missing", "extra"}
    assert doc["checks"]["latency"] == "failed"


def test_value_only_comparison_says_timing_was_not_checked(tmp_path, capsys, monkeypatch):
    _project(tmp_path, {"order": "event"})
    doc = _test(tmp_path, capsys, monkeypatch)

    assert doc["status"] == "VERIFIED"
    assert doc["checks"]["values"] == "passed"
    assert doc["checks"]["latency"] == doc["checks"]["event_association"] == doc["checks"]["framing"] == "not_checked"


def test_a_packet_without_its_last_flag_fails_framing(tmp_path, capsys, monkeypatch):
    _project(tmp_path, {"framing": True}, last="1'b0")
    doc = _test(tmp_path, capsys, monkeypatch)

    assert doc["checks"]["framing"] == "failed"
    assert any(r["kind"] == "framing" and r["flag"] == "last" for r in doc["interfaces"][0]["rows"])


# ── Replaying one event with its history ────────────────────────────────────

STATEFUL = """module emp_inc(input clk, input rst,
  input [63:0] l_data, input l_valid, input l_start, input l_last,
  output reg [63:0] q_data, output reg q_valid, output reg q_start, output reg q_last);
  reg [63:0] previous;
  always @(posedge clk) begin
    q_data <= l_data + previous; q_valid <= l_valid & !rst; q_start <= l_start; q_last <= l_last;
    if (rst) previous <= 0; else if (l_valid) previous <= l_data;
  end
endmodule
"""


def _stateful(tmp_path):
    (tmp_path / "rtl").mkdir(exist_ok=True)
    (tmp_path / "rtl" / "emp_inc.v").write_text(STATEFUL)
    write_pattern_file(tmp_path / "source.txt", [{0: packet(v)} if v else {} for v in (0x10, 0, 0x20, 0, 0x30, 0)], [0])
    write_pattern_file(tmp_path / "sink.txt", [{1: packet(v)} if v else {} for v in (0x10, 0, 0x30, 0, 0x50, 0)], [1])
    for side in ("source", "sink"):
        (tmp_path / f"{side}.events.json").write_text(json.dumps(["bx0", "bx1", "bx2"]))
    (tmp_path / "forge.yml").write_text(yaml.safe_dump({
        "project": "emp", "top": "emp_inc", "sources": {"rtl": ["rtl/*.v"]},
        "clock": {"port": "clk"}, "reset": {"port": "rst", "active": "high"},
        "verification": {
            "target": "existing", "backends": ["verilator"], "links": {"l": 0, "q": 1},
            "input": {"path": "source.txt", "format": "empv2", "events": "source.events.json"},
            "expected": {"path": "sink.txt", "format": "empv2", "events": "sink.events.json"}}}))


def _focused(tmp_path, capsys, monkeypatch, *argv):
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(["test", "--json", "--event-id", "bx2", *argv])
    try:
        args.func(args)
    except SystemExit:
        pass
    capsys.readouterr()
    run = sorted((tmp_path / ".forge" / "reports" / "test" / "runs").glob("*"))[-1]
    return json.loads(next(run.glob("*.evidence.json")).read_text())


def test_an_event_replayed_alone_says_it_had_no_history(tmp_path, capsys, monkeypatch):
    _stateful(tmp_path)
    doc = _focused(tmp_path, capsys, monkeypatch)

    assert doc["status"] == "FAILED"          # 0x30 + nothing, not 0x30 + 0x20
    assert doc["replay_policy"] == "independent" and "keeps state" in doc["replay_note"]


def test_a_pre_roll_replays_the_history_an_event_needs(tmp_path, capsys, monkeypatch):
    _stateful(tmp_path)
    doc = _focused(tmp_path, capsys, monkeypatch, "--pre-roll", "1")

    assert doc["status"] == "VERIFIED", doc["interfaces"]
    assert doc["events"] == ["bx2"] and doc["context_events"] == ["bx1"]
    assert doc["replay_policy"] == {"pre_roll": 1}
    assert doc["interfaces"][0]["context_rows"][0]["event"] == "bx1"
