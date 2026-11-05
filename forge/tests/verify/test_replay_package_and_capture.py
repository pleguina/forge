"""The reference a payload was simulated against, packaged for a board, and a
capture from the board compared with it by the same scoreboard.

`forge report --package` writes the input and expected pattern files, their
event names, the link mapping, how outputs are compared and the identity of
the build; `forge test --capture` reads the captured links, compares them as
simulation does, attributes each mismatch to an event, interface, field and
frame, and records the build and mapping it came from.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from forge.core.cli.main import build_parser
from forge.integration.emp import packet, write_pattern_file
from forge.project.verification_state import latest_results

needs_verilator = pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")

EMP_INC = """module emp_inc(input clk, input rst,
  input [63:0] l_data, input l_valid, input l_start, input l_last,
  output reg [63:0] q_data, output reg q_valid, output reg q_start, output reg q_last);
  always @(posedge clk) begin
    q_data <= l_data + 64'd1; q_valid <= l_valid & !rst; q_start <= l_start; q_last <= l_last;
  end
endmodule
"""


@pytest.fixture
def payload(tmp_path, monkeypatch):
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "emp_inc.v").write_text(EMP_INC)
    write_pattern_file(tmp_path / "source.txt", [{0: packet(0x10)}, {}, {0: packet(0x20)}], [0])
    write_pattern_file(tmp_path / "sink.txt", [{1: packet(0x11)}, {}, {1: packet(0x21)}], [1])
    names = [{"run": 355100, "lumi": 1, "event": 41, "bx": 7}, {"run": 355100, "lumi": 1, "event": 42, "bx": 9}]
    for side in ("source", "sink"):
        (tmp_path / f"{side}.events.json").write_text(json.dumps(names))
    (tmp_path / "decode.py").write_text("def decode(interface, word):\n    return {'pt': word & 0xff}\n")
    (tmp_path / "forge.yml").write_text(yaml.safe_dump({
        "project": "emp", "top": "emp_inc", "sources": {"rtl": ["rtl/*.v"]},
        "clock": {"port": "clk"}, "reset": {"port": "rst", "active": "high"},
        "verification": {
            "target": "existing", "backends": ["verilator"], "decoder": "decode.py:decode",
            "links": {"l": 0, "q": 1},
            "targets": {"payload": {
                "input": {"path": "source.txt", "format": "empv2", "events": "source.events.json"},
                "expected": {"path": "sink.txt", "format": "empv2", "events": "sink.events.json"}}}}}))
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


def _capture(path: Path, words, first_frame=5):
    frames = [{} for _ in range(first_frame)]
    for w in words:
        frames += [{1: packet(w)}, {}]
    write_pattern_file(path, frames, [1])
    return path


def test_a_package_holds_the_reference_mapping_comparison_and_build(payload, capsys):
    code, doc, err = _run(capsys, "report", "--package", "payload", "--json")

    assert code == 0, err
    package = Path(doc["metrics"]["package"])
    manifest = json.loads((package / "manifest.json").read_text())
    assert manifest["target"] == "payload" and manifest["top"] == "emp_inc"
    assert manifest["links"] == {"l": 0, "q": 1} and manifest["compare"]["mode"] == "transaction"
    assert manifest["reference"]["expected"]["file"] == "data/expected.txt"
    assert (package / "data/expected.txt").read_bytes() == (payload / "sink.txt").read_bytes()
    assert (package / "data/input.events.json").is_file()
    assert manifest["build"]["sources"][0]["path"].endswith("emp_inc.v") and len(manifest["build"]["id"]) == 64
    assert [i["link"] for i in manifest["interfaces"]] == [0, 1]
    assert "forge test --target payload --capture" in (package / "README.md").read_text()


def test_a_capture_matching_the_reference_is_verified_with_its_build(payload, capsys):
    _, doc, _ = _run(capsys, "report", "--package", "payload", "--json")
    package = doc["metrics"]["package"]

    code, doc, err = _run(capsys, "test", "--capture", str(_capture(payload / "cap.txt", [0x11, 0x21])),
                          "--package", package, "--json")

    assert code == 0, err
    assert doc["metrics"]["levels"] == {"payload[board]": "VERIFIED"}
    evidence = json.loads((payload / ".forge/reports/test/auto/payload.board.evidence.json").read_text())
    assert evidence["build"]["package"] == package and evidence["links"] == {"q": 1}
    assert evidence["capture"]["format"] == "empv2"


def test_a_capture_mismatch_names_its_event_field_and_frame(payload, capsys):
    code, doc, _ = _run(capsys, "test", "--capture", str(_capture(payload / "cap.txt", [0x11, 0x2f])), "--json")

    assert code == 1 and doc["metrics"]["verification"] == "FAILED"
    evidence = json.loads((payload / ".forge/reports/test/auto/payload.board.evidence.json").read_text())
    (row,) = evidence["interfaces"][0]["rows"]
    assert row["kind"] == "mismatch" and row["event"] == "355100:1:42@bx9" and row["cycle"] == 7
    assert row["expected_fields"] == {"pt": 0x21} and row["observed_fields"] == {"pt": 0x2f}


def test_a_package_whose_reference_changed_is_refused(payload, capsys):
    _, doc, _ = _run(capsys, "report", "--package", "payload", "--json")
    write_pattern_file(payload / "sink.txt", [{1: packet(0x11)}, {}, {1: packet(0x22)}], [1])

    code, _, err = _run(capsys, "test", "--capture", str(_capture(payload / "cap.txt", [0x11, 0x21])),
                        "--package", doc["metrics"]["package"])

    assert code != 0 and "the expected in forge.yml differ from the package's" in err


def test_a_latency_mode_needs_the_frame_the_input_was_played_at(payload, capsys):
    config = yaml.safe_load((payload / "forge.yml").read_text())
    config["verification"]["targets"]["payload"]["compare"] = {"mode": "fixed_latency", "latency": 5}
    (payload / "forge.yml").write_text(yaml.safe_dump(config))
    capture = str(_capture(payload / "cap.txt", [0x11, 0x21], first_frame=5))

    code, _, err = _run(capsys, "test", "--capture", capture)
    assert code != 0 and "--capture-offset" in err

    code, doc, err = _run(capsys, "test", "--capture", capture, "--capture-offset", "0", "--json")
    assert code == 0, err

    code, doc, err = _run(capsys, "test", "--capture", capture, "--capture-offset", "2", "--json")
    assert code == 1
    evidence = json.loads((payload / ".forge/reports/test/auto/payload.board.evidence.json").read_text())
    assert [(r["kind"], r["latency"], r["event"]) for r in evidence["interfaces"][0]["rows"]] == [
        ("latency", 3, "355100:1:41@bx7"), ("latency", 3, "355100:1:42@bx9")]


def test_a_changed_capture_makes_its_result_out_of_date(payload, capsys):
    capture = _capture(payload / "cap.txt", [0x11, 0x21])
    _run(capsys, "test", "--capture", str(capture), "--json")

    capture.write_text(capture.read_text().replace("0000000000000011", "0000000000000012"))

    (record,) = [r for r in latest_results(payload / ".forge/reports") if r.name == "payload[board]"]
    assert record.stale == "the capture cap.txt"


@needs_verilator
def test_the_package_records_the_simulation_of_the_same_reference(payload, capsys):
    code, _, err = _run(capsys, "test", "--json")
    assert code == 0, err

    _, doc, _ = _run(capsys, "report", "--package", "payload", "--json")

    assert doc["metrics"]["simulated"]["payload[verilator]"]["status"] == "VERIFIED"
