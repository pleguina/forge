"""Whether a result still applies, from everything that decided it.

A result records its sources, headers, memory files and decoder files by
content, and its plan in four parts (scope, compile inputs, run inputs,
comparison); a change to any of them makes the result out of date and names
the part. A board capture is compared by the same function as a simulation
trace, so the two agree on the same observations.
"""

from __future__ import annotations

import uuid

import pytest

from forge.integration.emp import packet, write_pattern_file
from forge.integration.objects import Codec, codec_decoder
from forge.project.verification_state import _evidence_staleness
from forge.verification import auto
from forge.verification import scoreboard as sb
from forge.verification.auto import DataRef, Level, compare_capture, load_decoder, plan_parts
from forge.verification.auto_tb import Bench
from forge.verification.compile_key import build_key
from forge.verification.protocols import Interface, Port


def _out(name):
    return Interface(name, "valid", "out", [Port(name, "out", 64)], valid=Port(f"{name}_valid", "out", 1))


@pytest.fixture
def level(tmp_path):
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "top.v").write_text("module top; endmodule\n")
    (rtl / "rom.dat").write_text("00\n01\n")
    write_pattern_file(tmp_path / "expected.txt", [{0: packet(0x11)}], [0])
    write_pattern_file(tmp_path / "other.txt", [{0: packet(0x11)}], [0])
    return Level("t", "top", [rtl / "top.v"], [rtl], Bench("top", "clk", "rst", True, [_out("q")]),
                 input=DataRef(tmp_path / "expected.txt", "empv2", {"q": 0}),
                 expected=DataRef(tmp_path / "expected.txt", "empv2", {"q": 0}))


def _stale(level, before):
    return _evidence_staleness(before, {level.name: plan_parts(level)})


def _record(level):
    return auto._evidence(level, "board", sb.VERIFIED).doc


@pytest.mark.parametrize("change, part", [
    (lambda lv, d: setattr(lv.expected, "links", {"q": 1}), "its comparison"),
    (lambda lv, d: setattr(lv.expected, "association", {"max_skew": 3, "missing": "empty"}), "its comparison"),
    (lambda lv, d: setattr(lv, "decoder", codec_decoder({"q": Codec.from_spec(
        {"width": 8, "fields": {"pt": {"low": 4, "bits": 4}}}, "mu")})), "its comparison"),
    (lambda lv, d: lv.sources.append(d / "rtl" / "top.v"), "its compile inputs"),
    (lambda lv, d: lv.defines.update(WIDE="1"), "its compile inputs"),
    (lambda lv, d: setattr(lv, "simulation", {"elab_libs": ["unisims_ver"]}), "its compile inputs"),
    (lambda lv, d: setattr(lv.input, "links", {"q": 2}), "its run inputs"),
    (lambda lv, d: setattr(lv, "pre_roll", 2), "its run inputs"),
])
def test_each_setting_that_decides_a_result_makes_it_out_of_date(level, tmp_path, change, part):
    level.decoder = codec_decoder({"q": Codec.from_spec({"width": 8, "fields": {"pt": {"low": 0, "bits": 4}}}, "mu")})
    before = _record(level)
    assert _stale(level, before) == ""

    change(level, tmp_path)

    stale = _stale(level, before)
    assert part in stale
    assert all(p not in stale for p in auto.PLAN_PARTS.values() if not p.startswith(part))


def test_a_new_dataset_changes_what_runs_not_what_compiles(level, tmp_path):
    before = plan_parts(level)
    level.input = DataRef(tmp_path / "other.txt", "empv2", {"q": 0})
    after = plan_parts(level)

    assert after["compile"] == before["compile"] and after["run"] != before["run"]


def test_a_changed_rom_makes_a_result_out_of_date(level, tmp_path):
    before = _record(level)
    (tmp_path / "rtl" / "rom.dat").write_text("00\n02\n")

    assert "the memory file rom.dat" in _stale(level, before)


def test_a_changed_decoder_or_a_module_it_imports_makes_a_result_out_of_date(level, tmp_path):
    helper = f"helper_{uuid.uuid4().hex[:8]}"
    (tmp_path / f"{helper}.py").write_text("SHIFT = 0\n")
    (tmp_path / "decode.py").write_text(
        f"import sys\nsys.path.insert(0, {str(tmp_path)!r})\nimport {helper}\n\n"
        f"def decode(interface, word):\n    return {{'pt': (word >> {helper}.SHIFT) & 0xf}}\n")
    level.decoder = load_decoder("decode.py:decode", tmp_path)
    before = _record(level)

    (tmp_path / f"{helper}.py").write_text("SHIFT = 4\n")

    assert f"the decoder file {helper}.py" in _stale(level, before)


def test_an_extra_simulation_source_keys_the_build_and_the_result_by_content(level, tmp_path):
    extra = tmp_path / "glbl.v"
    extra.write_text("module glbl; endmodule\n")
    level.simulation = {"extra_sources": [str(extra)]}
    lst = tmp_path / "sources.f"
    auto._list_file(level, [], lst)
    first = build_key(lst, [["xvlog", str(extra)]], [])
    before = _record(level)

    extra.write_text("module glbl; wire x; endmodule\n")

    assert build_key(lst, [["xvlog", str(extra)]], []) != first
    assert "glbl.v" in _stale(level, before)


# ── Simulation and capture agree ───────────────────────────────────────────

def test_capture_and_simulation_compare_by_the_same_fields(tmp_path):
    write_pattern_file(tmp_path / "expected.txt", [{0: packet(0x11)}], [0])
    write_pattern_file(tmp_path / "capture.txt", [{0: packet(0x21)}], [0])
    level = Level("t", "top", [], [], Bench("top", "clk", "rst", True, [_out("q")]),
                  expected=DataRef(tmp_path / "expected.txt", "empv2", {"q": 0}),
                  compare=sb.Compare(fields=["pt"]))
    level.decoder = lambda name, word: {"pt": word & 0xF}

    simulated = sb.compare("q", [sb.Expected(0x11)], [(0, 0x21)], level.compare, key=auto._field_key(level, "q"))
    captured = compare_capture(level, DataRef(tmp_path / "capture.txt", "empv2"))

    assert simulated.status == captured.status == sb.VERIFIED


def test_a_stream_is_read_from_its_members_links_on_the_board(tmp_path):
    write_pattern_file(tmp_path / "expected.txt", [{0: packet(0x1)}, {0: packet(0x2)}], [0])
    write_pattern_file(tmp_path / "capture.txt", [{1: packet(0x1)}, {2: packet(0x2)}], [1, 2])
    level = Level("t", "top", [], [], Bench("top", "clk", "rst", True, [_out("q0"), _out("q1")]),
                  expected=DataRef(tmp_path / "expected.txt", "empv2", {"q": 0, "q0": 1, "q1": 2}))
    level.streams = {"q": ["q0", "q1"]}

    result = compare_capture(level, DataRef(tmp_path / "capture.txt", "empv2"))

    assert result.status == sb.VERIFIED, result.doc["interfaces"]
    assert [i["interface"] for i in result.doc["interfaces"]] == ["q"]
