"""examples/cmssw_reference: a payload compared with a real CMSSW export.

The export is packed with the codecs into pattern files, the payload matches CMSSW's GMT muons, a
payload bug is named by event and field, and a packet missing on one link is an association error.
"""

from __future__ import annotations

import json
import runpy
import shutil
from pathlib import Path

import pytest

from forge.core.cli.main import build_parser
from forge.integration.objects import Codec

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "cmssw_reference"
needs_verilator = pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")


@pytest.fixture
def example(tmp_path, monkeypatch):
    root = tmp_path / "cmssw_reference"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("data", ".forge"))
    monkeypatch.chdir(root)
    runpy.run_path(str(root / "make_patterns.py"), run_name="__main__")
    return root


def _test(capsys, *argv):
    args = build_parser().parse_args(["test", "--backend", "verilator", "--json", *argv])
    try:
        args.func(args)
        code = 0
    except SystemExit as exc:
        code = int(exc.code or 0)
    return code, json.loads(capsys.readouterr().out)


def test_the_codecs_pack_and_read_back_every_muon_of_the_export():
    export = json.loads((EXAMPLE / "export/omtf_muons.json").read_text())
    codec = Codec.load(EXAMPLE / "codecs/samuon.yml")
    muons = [m for e in export["events"] for m in e["samuons"]]

    assert len(muons) > 100 and export["provenance"]["cmssw_release"] == "CMSSW_20_0_0"
    for m in muons:
        fields = codec.unpack(codec.pack(m))
        assert fields is not None and {k: fields[k] for k in ("pt", "eta", "phi", "charge", "qual")} == \
            {k: m[k] for k in ("pt", "eta", "phi", "charge", "qual")}


@needs_verilator
def test_the_payload_matches_cmssw_and_a_bug_is_named_by_event_and_field(example, capsys):
    code, doc = _test(capsys)
    assert code == 0 and doc["metrics"]["levels"] == {"existing:candidate_to_gmt[verilator]": "VERIFIED"}

    rtl = example / "rtl" / "candidate_to_gmt.v"
    rtl.write_text(rtl.read_text().replace("c[26:14], c[39:27]", "c[26:14] + 13'd1, c[39:27]"))
    code, doc = _test(capsys)

    assert code == 1
    evidence = json.loads((example / ".forge/reports/test/auto/existing_candidate_to_gmt.verilator.evidence.json")
                          .read_text())
    row = evidence["interfaces"][0]["rows"][0]
    assert row["kind"] == "mismatch" and row["event"] == "2"
    assert row["observed_fields"]["eta"] == row["expected_fields"]["eta"] + 1
    assert evidence["expected"]["provenance"]["cmssw_commit"] == "0338fcce45b"


@needs_verilator
def test_a_packet_missing_on_one_link_is_an_association_error(example, capsys):
    path = example / "data" / "input.txt"
    lines = path.read_text().splitlines()
    first = next(i for i, line in enumerate(lines) if line.startswith("Frame 0002"))
    cells = lines[first].split()
    cells[6:8] = ["0000", "0" * 16]
    lines[first] = f"{cells[0]} {cells[1]}  " + "  ".join(f"{cells[k]} {cells[k + 1]}" for k in range(2, 14, 2))
    path.write_text("\n".join(lines) + "\n")

    code, doc = _test(capsys)

    assert code != 0
    assert "link(s) 2 have no packet starting there" in json.dumps(doc["diagnostics"])
