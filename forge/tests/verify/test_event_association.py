"""Words are compared event by event, and events are put together explicitly.

Packets on several links belong to one event when they overlap in frames; a
link missing its packet is an error rather than a shift of every later
event onto the wrong partner. Event names (run/lumi/event/BX) given next to
the data reach every mismatch row, a project decoder names the fields of the
words, and latency is measured from the event's own first input word.
"""

from __future__ import annotations

import json
import shutil

import pytest

from forge.integration.emp import EventAssociationError, LinkWord, associate_events, packet, read_board_data
from forge.integration.emp import write_pattern_file
from forge.verification import auto
from forge.verification import scoreboard as sb
from forge.verification.auto_tb import Bench
from forge.verification.protocols import Port, infer

needs_verilator = pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")


def _two_links(path, drop_second_on_link1=False):
    frames = [{0: packet(0x10), 1: packet(0x20)}, {}, {0: packet(0x11)}, {}]
    if not drop_second_on_link1:
        frames[2][1] = packet(0x21)
    frames.append({0: packet(0x12), 1: packet(0x22)})
    write_pattern_file(path, frames, [0, 1])
    return path


def test_packets_overlapping_in_frames_are_one_event(tmp_path):
    events = associate_events(read_board_data(_two_links(tmp_path / "p.txt")), [0, 1])

    assert [{link: [w.data for w in words] for link, (_, _, words) in e.items()} for e in events] == [
        {0: [0x10], 1: [0x20]}, {0: [0x11], 1: [0x21]}, {0: [0x12], 1: [0x22]}]


def test_a_missing_packet_is_an_association_error_not_a_shift(tmp_path):
    board = read_board_data(_two_links(tmp_path / "p.txt", drop_second_on_link1=True))

    with pytest.raises(EventAssociationError, match=r"event 1 \(frames 2-2\): link\(s\) 1"):
        associate_events(board, [0, 1])
    events = associate_events(board, [0, 1], missing="empty")
    assert [w.data for w in events[1][1][2]] == [] and [w.data for w in events[2][1][2]] == [0x22]


def test_the_concise_path_refuses_a_shifted_input(tmp_path):
    path = _two_links(tmp_path / "p.txt", drop_second_on_link1=True)
    ports = [Port("a", "in", 64), Port("a_valid", "in", 1), Port("b", "in", 64), Port("b_valid", "in", 1),
             Port("y", "out", 64), Port("y_valid", "out", 1)]
    bench = Bench("m", "clk", "rst", True, infer(ports))
    ref = auto.DataRef.from_spec({"path": "p.txt", "format": "empv2"}, tmp_path, {"a": 0, "b": 1})

    with pytest.raises(EventAssociationError):
        auto.load_inputs(ref, bench)


def test_latency_is_measured_from_the_events_own_input():
    exp = [sb.Expected(0x5, event="1:1:7"), sb.Expected(0x6, event="1:1:8")]
    cmp = sb.Compare.from_dict({"mode": "fixed_latency", "latency": 3})

    ok = sb.compare("y", exp, [(13, 0x5), (23, 0x6)], cmp, event_cycles={"1:1:7": 10, "1:1:8": 20})
    late = sb.compare("y", exp, [(13, 0x5), (25, 0x6)], cmp, event_cycles={"1:1:7": 10, "1:1:8": 20})

    assert ok.status == sb.VERIFIED
    assert late.rows == [{"kind": "latency", "index": 1, "cycle": 25, "latency": 5, "allowed": [3, 3],
                          "event": "1:1:8"}]


_EMP = """module emp_inc(input clk, input rst,
  input [63:0] l_data, input l_valid, input l_start, input l_last,
  output reg [63:0] q_data, output reg q_valid, output reg q_start, output reg q_last);
  always @(posedge clk) begin
    q_data <= l_data + 64'd1; q_valid <= l_valid & !rst; q_start <= l_start; q_last <= l_last;
  end
endmodule
"""


@needs_verilator
def test_a_mismatch_names_its_event_and_fields(tmp_path):
    write_pattern_file(tmp_path / "source.txt", [{0: packet(0x10)}, {}, {0: packet(0x20)}], [0])
    write_pattern_file(tmp_path / "sink.txt", [{1: packet(0x11)}, {1: packet(0x99)}], [1])
    (tmp_path / "sink.events.json").write_text(json.dumps([
        {"run": 355100, "lumi": 1, "event": 41, "bx": 7}, {"run": 355100, "lumi": 1, "event": 42, "bx": 9}]))
    (tmp_path / "decode.py").write_text(
        "def decode(interface, word):\n    return {'pt': word & 0xff, 'quality': (word >> 8) & 0xf}\n")
    (tmp_path / "emp_inc.v").write_text(_EMP)
    ports = [Port("l_data", "in", 64), Port("l_valid", "in", 1), Port("l_start", "in", 1), Port("l_last", "in", 1),
             Port("q_data", "out", 64), Port("q_valid", "out", 1), Port("q_start", "out", 1), Port("q_last", "out", 1)]
    bench = Bench("emp_inc", "clk", "rst", True, infer(ports, exclude=["clk", "rst"]))
    level = auto.Level("design", "emp_inc", [tmp_path / "emp_inc.v"], [], bench,
                       auto.DataRef.from_spec({"path": "source.txt"}, tmp_path, {"l": 0}),
                       auto.DataRef.from_spec({"path": "sink.txt", "events": "sink.events.json"}, tmp_path, {"q": 1}))
    level.decoder = auto.load_decoder("decode.py:decode", tmp_path)

    ev = auto.run(level, "verilator", tmp_path / "work")

    assert ev.status == sb.FAILED
    (row,) = ev.doc["interfaces"][0]["rows"]
    assert row["event"] == "355100:1:42@bx9"
    assert row["expected_fields"] == {"pt": 0x99, "quality": 0}
    assert row["observed_fields"] == {"pt": 0x21, "quality": 0}
