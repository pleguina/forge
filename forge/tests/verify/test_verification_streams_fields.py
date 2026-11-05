"""Outputs read the way a time-multiplexed payload writes them.

A trigger payload spreads one stream of objects over several links in turn,
fills the slots with no object with empty words, and carries bits the
reference does not define. ``verification.streams`` reads several outputs as
one, in cycle order; ``compare.fields`` compares words by the project
decoder's fields and skips the words it reads as no object; ``compare.order:
event`` allows any order within an event; a target's ``outputs`` names the
outputs its reference covers. A configuration written before the first event
(a dataset entry with a null ``id``) is kept by a focused run, and a design whose outputs
change every cycle ends ``drain`` cycles after its last input.
"""

from __future__ import annotations

import json
import shutil

import pytest
import yaml

from forge.core.cli.main import build_parser
from forge.verification import scoreboard as sb

needs_verilator = pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")

# Each cycle's x, offset by the configured word, leaves on q0 or q1 in turn as
# {data[15:0], last, first, valid}; x = 0 is a slot with no object (data 0).
# dbg is a free-running counter no reference covers.
TMUX = """module tmux2(input clk, input rst, input [15:0] x, input [7:0] cfg64, input cfg_valid,
  output reg [18:0] q0, output reg [18:0] q1, output reg [3:0] dbg);
  reg [7:0] offset;
  reg turn;
  wire [15:0] data = x == 16'd0 ? 16'd0 : x + {8'd0, offset};
  always @(posedge clk) begin
    if (rst) begin offset <= 8'd0; turn <= 1'b0; q0 <= 19'd0; q1 <= 19'd0; dbg <= 4'd0; end
    else begin
      if (cfg_valid) offset <= cfg64;
      turn <= ~turn;
      dbg <= dbg + 4'd1;
      q0 <= turn ? 19'd0 : {data, 3'b001};
      q1 <= turn ? {data, 3'b001} : 19'd0;
    end
  end
endmodule
"""

DECODE = """def decode(interface, word):
    data = word >> 3
    if data == 0:
        return None
    return {"v": data & 0xff, "hi": data >> 8}
"""

EVENTS = [("e1", [0x10, 0x11]), ("e2", [0x20, 0x21]), ("e3", [0x30, 0x31])]
OFFSET = 5


def _word(v, hi=0):
    return (((hi << 8) | v) << 3) | 1


@pytest.fixture
def payload(tmp_path, monkeypatch):
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "tmux2.v").write_text(TMUX)
    (tmp_path / "decode.py").write_text(DECODE)
    # cfg64 is held from the first cycle, cfg_valid pulses once, x waits for it
    setup = {"id": None, "cfg64": [OFFSET], "cfg_valid": [0, 1, 0], "x": [0, 0, 0, 0]}
    events = [{"id": name, "x": xs + [0, 0]} for name, xs in EVENTS]
    (tmp_path / "input.json").write_text(json.dumps({"events": [setup, *events]}))
    # the reference defines the low byte only, and lists each event's words in another order
    expected = [{"id": name, "q": [hex(_word(x + OFFSET, hi=0xA)) for x in reversed(xs)]} for name, xs in EVENTS]
    (tmp_path / "expected.json").write_text(json.dumps({
        "provenance": {"software": "CMSSW_20_0_0", "commit": "111315ea"}, "events": expected}))
    (tmp_path / "forge.yml").write_text(yaml.safe_dump({
        "project": "tmux", "top": "tmux2", "sources": {"rtl": ["rtl/*.v"]},
        "clock": {"port": "clk"}, "reset": {"port": "rst", "active": "high"},
        "verification": {
            "target": "existing", "backends": ["verilator"], "decoder": "decode.py:decode",
            "streams": {"q": "q?"},
            "targets": {"payload": {
                "input": "input.json", "expected": "expected.json", "outputs": ["q"], "drain": 8,
                "compare": {"fields": ["v"], "order": "event"}}}}}))
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


def _evidence(root):
    return json.loads((root / ".forge/reports/test/auto/payload.verilator.evidence.json").read_text())


def _expect(path, change):
    doc = json.loads(path.read_text())
    change(doc)
    path.write_text(json.dumps(doc))


@needs_verilator
def test_a_multiplexed_stream_is_compared_by_its_fields_within_each_event(payload, capsys):
    code, doc, err = _run(capsys, "test", "--json")

    assert code == 0, err
    assert doc["metrics"]["levels"] == {"payload[verilator]": "VERIFIED"}
    evidence = _evidence(payload)
    (q,) = evidence["interfaces"]
    assert q["interface"] == "q" and q["matched"] == 6 and q["observed"] == 6
    assert evidence["expected"]["provenance"] == {"software": "CMSSW_20_0_0", "commit": "111315ea"}

    _run(capsys, "report", "--json")
    report = (payload / ".forge/reports/verification_results.md").read_text()
    assert "- payload[verilator]: software CMSSW_20_0_0, commit 111315ea" in report


@needs_verilator
def test_a_wrong_object_is_named_by_its_event_and_fields(payload, capsys):
    _expect(payload / "expected.json", lambda d: d["events"][1]["q"].__setitem__(0, hex(_word(0x99))))

    code, doc, _ = _run(capsys, "test", "--json")

    assert code == 1
    (row,) = _evidence(payload)["interfaces"][0]["rows"]
    assert row["kind"] == "mismatch" and row["event"] == "e2"
    assert row["expected_fields"] == {"v": 0x99, "hi": 0} and row["observed_fields"]["v"] == 0x21 + OFFSET


@needs_verilator
def test_a_focused_run_keeps_the_configuration_written_before_the_events(payload, capsys):
    code, doc, err = _run(capsys, "test", "--event-id", "e3", "--json")

    assert code == 0, err
    evidence = json.loads(next((payload / ".forge/reports/test/runs").rglob("*.evidence.json")).read_text())
    assert evidence["events"] == ["e3"] and evidence["interfaces"][0]["matched"] == 2


@needs_verilator
def test_without_order_event_the_reordered_reference_fails(payload, capsys):
    config = yaml.safe_load((payload / "forge.yml").read_text())
    config["verification"]["targets"]["payload"]["compare"] = {"fields": ["v"]}
    (payload / "forge.yml").write_text(yaml.safe_dump(config))

    code, _, _ = _run(capsys, "test", "--json")

    assert code == 1
    assert {r["kind"] for r in _evidence(payload)["interfaces"][0]["rows"]} == {"out_of_order"}


@needs_verilator
def test_an_event_missing_a_word_on_one_input_is_an_error_not_a_shift(payload, capsys):
    doc = json.loads((payload / "input.json").read_text())
    doc["events"][2]["cfg_valid"] = [0, 0, 0]
    (payload / "input.json").write_text(json.dumps(doc))

    code, out, err = _run(capsys, "test")

    assert code != 0 and "event e2 gives cfg_valid 3 word(s) and other per-cycle inputs 4" in out + err


@needs_verilator
def test_an_input_an_entry_does_not_name_holds_its_word_and_stays_aligned(payload, capsys):
    doc = json.loads((payload / "input.json").read_text())
    doc["events"][0]["x"] = [0, 0]    # the setup is 3 cycles (cfg_valid's), x must still start e1 with it
    doc["events"][1]["cfg_valid"] = [0, 0, 0, 0]
    (payload / "input.json").write_text(json.dumps(doc))

    code, doc, err = _run(capsys, "test", "--json")

    assert code == 0, err
    driven = _evidence(payload)["inputs_driven"]
    assert driven["x"] == driven["cfg_valid"] == 3 + 3 * 4


@pytest.mark.parametrize("change, message", [
    (lambda v: v["targets"]["payload"].update(outputs=["nope"]), "outputs: 'nope' matches no output"),
    (lambda v: v.update(streams={"q": "z*"}), "verification.streams.q: no output"),
    (lambda v: v.pop("decoder"), "compare.fields names fields of the decoder's reading"),
    (lambda v: v.update(xsim={"libs": ["unisims_ver"]}), "verification.xsim: unknown key libs"),
])
def test_a_wrong_declaration_is_named(payload, capsys, change, message):
    config = yaml.safe_load((payload / "forge.yml").read_text())
    change(config["verification"])
    (payload / "forge.yml").write_text(yaml.safe_dump(config))

    code, out, err = _run(capsys, "test")

    assert code != 0 and message in out + err


def test_xsim_takes_the_libraries_tops_and_sources_of_the_plugin_flow(payload, monkeypatch):
    from forge.project.config import ForgeConfig
    from forge.verification import auto

    monkeypatch.setenv("SIMLIB", str(payload))
    config = ForgeConfig.load(payload)
    spec = dict(config.verification_spec)
    spec["xsim"] = {"elab_libs": ["unisims_ver"], "extra_top_modules": ["glbl"],
                    "extra_sources": ["$SIMLIB/rtl/tmux2.v"]}

    class Project:
        root = payload

    Project.config = config
    (level,) = auto.plan(Project, spec=spec).levels
    xvlog, xelab = auto._compile_commands("xsim", level, payload / "tb.sv", payload)

    assert str(payload / "rtl" / "tmux2.v") in xvlog[-2:]
    assert xelab[:6] == ["xelab", auto.TB_CLOCKED, "glbl", "-L", "unisims_ver", "-s"]


@needs_verilator
def test_rom_contents_next_to_a_source_are_read_from_the_run_directory(tmp_path, monkeypatch, capsys):
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "rom.v").write_text(
        "module rom(input clk, input rst, input [1:0] a, output reg [7:0] q);\n"
        "  reg [7:0] mem [0:3];\n  initial $readmemh(\"./rom_init.dat\", mem);\n"
        "  always @(posedge clk) q <= mem[a];\nendmodule\n")
    (tmp_path / "rtl" / "rom_init.dat").write_text("11\n22\n33\n44\n")
    (tmp_path / "in.json").write_text(json.dumps({"a": [2, 1]}))
    (tmp_path / "exp.json").write_text(json.dumps({"q": [0x11, 0x11, 0x33]}))
    (tmp_path / "forge.yml").write_text(yaml.safe_dump({
        "project": "rom", "top": "rom", "sources": {"rtl": ["rtl/*.v"]},
        "clock": {"port": "clk"}, "reset": {"port": "rst", "active": "high"},
        "verification": {"target": "existing", "backends": ["verilator"], "drain": 0,
                         "input": "in.json", "expected": "exp.json"}}))
    monkeypatch.chdir(tmp_path)

    code, doc, err = _run(capsys, "test", "--json")

    assert code == 0, err


# ── Scoreboard ────────────────────────────────────────────────────────────

def _cmp(**d):
    return sb.Compare.from_dict(d)


def test_order_any_matches_over_the_whole_interface():
    exp = [sb.Expected(1, event="a"), sb.Expected(2, event="b")]
    res = sb.compare("q", exp, [(5, 2), (6, 3), (7, 1)], _cmp(order="any"))

    assert res.matched == 2 and res.rows == [{"kind": "extra", "index": 1, "cycle": 6, "observed": "0x3"}]


def test_order_event_does_not_match_a_word_of_another_event():
    exp = [sb.Expected(1, event="a"), sb.Expected(2, event="b")]
    res = sb.compare("q", exp, [(5, 2), (6, 1)], _cmp(order="event"))

    assert [(r["kind"], r.get("event"), r.get("cycle")) for r in res.rows] == [("missing", "b", None),
                                                                               ("extra", None, 5)]


def test_a_word_lost_in_one_event_does_not_displace_the_later_ones():
    exp = [sb.Expected(v, event=e) for e, v in (("a", 1), ("a", 2), ("b", 3), ("c", 4), ("d", 5), ("d", 6))]
    res = sb.compare("q", exp, [(1, 1), (3, 3), (4, 9), (5, 6), (6, 5)], _cmp(order="event"))

    assert res.matched == 4
    assert [(r["kind"], r["event"]) for r in res.rows] == [("missing", "a"), ("mismatch", "c")]


def test_a_word_with_no_object_is_not_a_transaction():
    key = lambda w: None if w == 0 else (w,)  # noqa: E731
    res = sb.compare("q", [sb.Expected(7)], [(1, 0), (2, 7), (3, 0)], _cmp(fields=["w"]), key=key)

    assert res.status == sb.VERIFIED and res.observed == 1


@pytest.mark.parametrize("d, message", [
    ({"order": "sideways"}, "compare.order 'sideways'"),
    ({"order": "event", "mode": "fixed_latency", "latency": 3}, "needs order strict"),
    ({"fields": []}, "compare.fields: want a list"),
])
def test_a_wrong_compare_is_named(d, message):
    with pytest.raises(ValueError, match=message):
        sb.Compare.from_dict(d)
