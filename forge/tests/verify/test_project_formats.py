"""Data in a format only the project reads, through verification.formats.

A project names a function that reads its own files (here the L1 track
finder's memory printouts, a directory per data set, read by
examples/l1tk_memprints/memprint.py); FORGE drives and compares with what it
returns, and a change to the reader or to any file of the data makes the
result out of date. FORGE core is not touched.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from forge.core.cli.main import build_parser
from forge.project.verification_state import latest_results

READER = Path(__file__).resolve().parents[3] / "examples" / "l1tk_memprints" / "memprint.py"

ROUTER = """module router(input clk, input rst,
  input [35:0] il_l1phia, input il_l1phia_valid,
  output reg [35:0] as_l1phian1, output reg as_l1phian1_valid);
  always @(posedge clk) begin
    as_l1phian1 <= il_l1phia ^ 36'h1; as_l1phian1_valid <= il_l1phia_valid & !rst;
  end
endmodule
"""


def _memprint(path: Path, events):
    lines = []
    for k, words in enumerate(events):
        lines.append(f"BX = {k % 8:03b} Event : {k}")
        lines += [f"0x{a:02x} {w:036b} 0x{w:09x}" for a, w in enumerate(words)]
    path.write_text("\n".join(lines) + "\n")


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "router.v").write_text(ROUTER)
    for side, kind, memory, xor in (("in", "InputStubs", "IL_L1PHIA", 0), ("out", "AllStubs", "AS_L1PHIAn1", 1)):
        (tmp_path / side).mkdir()
        _memprint(tmp_path / side / f"{kind}_{memory}_04.dat",
                  [[0x60fcc2dc7 ^ xor, 0xec0522bde ^ xor], [], [0x53db02b6d ^ xor]])
    shutil.copy(READER, tmp_path / "memprint.py")
    (tmp_path / "forge.yml").write_text(yaml.safe_dump({
        "project": "l1tk", "top": "router", "sources": {"rtl": ["rtl/*.v"]},
        "clock": {"port": "clk"}, "reset": {"port": "rst", "active": "high"},
        "verification": {"target": "existing", "backends": ["verilator"],
                         "formats": {"l1tk-memprint": "memprint.py:load"},
                         "compare": {"order": "event"},
                         "input": {"path": "in", "format": "l1tk-memprint"},
                         "expected": {"path": "out", "format": "l1tk-memprint"}}}))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _run(capsys, *argv):
    args = build_parser().parse_args(list(argv))
    code = 0
    try:
        args.func(args)
    except SystemExit as exc:
        code = int(exc.code or 0)
    out = capsys.readouterr()
    return code, json.loads(out.out) if "--json" in argv else out.out, out.err


def test_the_plan_reads_a_directory_in_the_projects_format(project, capsys):
    code, plan, err = _run(capsys, "test", "--plan", "--json")

    assert code == 0, err
    (level,) = plan["levels"]
    assert level["input"].endswith("/in") and level["expected"].endswith("/out")


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
def test_a_design_is_compared_with_data_only_its_project_reads(project, capsys):
    code, doc, err = _run(capsys, "test", "--json")

    assert code == 0, err
    assert doc["metrics"]["verification"] == "VERIFIED"
    (record,) = latest_results(project / ".forge/reports")
    assert record.payload["format_files"][0]["path"].endswith("memprint.py") and not record.stale
    assert record.payload["input"]["path"].endswith("/in")

    _memprint(project / "out" / "AllStubs_AS_L1PHIAn1_04.dat", [[1], [], [2]])
    (record,) = latest_results(project / ".forge/reports")
    assert record.stale == "the expected out"

    (project / "memprint.py").write_text((project / "memprint.py").read_text() + "\n# edited\n")
    (record,) = latest_results(project / ".forge/reports")
    assert "the format reader memprint.py" in record.stale


def test_a_format_name_forge_already_reads_is_refused(project, capsys):
    config = yaml.safe_load((project / "forge.yml").read_text())
    config["verification"]["formats"] = {"empv2": "memprint.py:load"}
    (project / "forge.yml").write_text(yaml.safe_dump(config))

    code, _, err = _run(capsys, "test", "--plan")

    assert code != 0 and "FORGE already reads a format of that name" in err


def test_a_reader_that_is_not_there_is_named(project, capsys):
    config = yaml.safe_load((project / "forge.yml").read_text())
    config["verification"]["formats"] = {"l1tk-memprint": "memprint.py:read_all"}
    (project / "forge.yml").write_text(yaml.safe_dump(config))

    code, _, err = _run(capsys, "test", "--plan")

    assert code != 0 and "memprint.py has no function read_all" in err


def test_the_memprint_reader_keeps_empty_events_and_names_memories(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("memprint_under_test", READER)
    memprint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(memprint)
    _memprint(tmp_path / "VMStubs_VMSTE_L6PHIAn1_04.dat", [[5], [], [6, 7]])

    assert memprint.memory_of(Path("InputStubs_IL_L1PHIA_negPS10G_1_A_04.dat")) == "IL_L1PHIA_negPS10G_1_A"
    assert memprint.load(tmp_path) == [{"id": 0, "vmste_l6phian1": [5]}, {"id": 1, "vmste_l6phian1": []},
                                       {"id": 2, "vmste_l6phian1": [6, 7]}]
