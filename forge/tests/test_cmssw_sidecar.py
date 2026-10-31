"""Event names and frames for pattern files written by CMSSW's BoardDataWriter.

The sidecar script reproduces the writer's placement (TMUX rotation over a
channel's indices, offsets, gaps, a new file when the buffer would overrun)
from its settings and the order of the events; FORGE then takes each event's
packets from those frames, so time-multiplexed events that overlap in time
on different links keep their identity.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from forge.integration import cmssw_sidecar
from forge.integration.emp import LinkWord, write_pattern_file
from forge.verification.auto import DataRef, load_tagged
from forge.verification.auto_tb import Bench
from forge.verification.protocols import Interface, Port

WRITER = {"path": "tx", "ext": "txt", "frames_per_bx": 1, "board_tmux": 6, "max_frames_per_file": 60,
          "channels": {"tracks": {"tmux": 18, "gap": 2, "offset": 0, "indices": [0, 1, 2]}}}
LOG = "\n".join(f"Begin processing the {k + 1}th record. Run 1, Event {100 + k}, LumiSection 7 on stream 0"
                for k in range(10))


def test_the_layout_follows_the_writers_rotation_and_file_limit():
    files = cmssw_sidecar.layout(WRITER, cmssw_sidecar.events_from_log(LOG))

    # 60 frames / (1 frame per BX * TMUX 6) = 10 slots, minus (ratio 3 - 1) = 8 events per file
    assert [len(f["events"]) for f in files] == [8, 2] and files[1]["file"] == "tx_1.txt"
    first = files[0]["events"]
    assert first[0] == {"run": 1, "lumi": 7, "event": 100, "links": {"0": [0, 15]}}
    assert first[1]["links"] == {"1": [6, 21]} and first[3]["links"] == {"0": [18, 33]}


def test_the_script_runs_without_forge_and_records_the_release(tmp_path, monkeypatch):
    (tmp_path / "writer.json").write_text(json.dumps({**WRITER, "path": str(tmp_path / "tx")}))
    (tmp_path / "cmsRun.log").write_text(LOG)
    (tmp_path / "prov.json").write_text(json.dumps({"global_tag": "140X_mcRun4"}))
    script = Path(cmssw_sidecar.__file__)

    proc = subprocess.run([sys.executable, "-I", str(script), "--writer", "writer.json", "--log", "cmsRun.log",
                           "--provenance", "prov.json"], cwd=tmp_path, capture_output=True, text=True,
                          env={"CMSSW_VERSION": "CMSSW_15_1_2", "PATH": "/usr/bin:/bin"})

    assert proc.returncode == 0, proc.stderr
    prov = json.loads((tmp_path / "tx_0.txt.provenance.json").read_text())
    assert prov["cmssw_release"] == "CMSSW_15_1_2" and prov["global_tag"] == "140X_mcRun4"
    assert len(json.loads((tmp_path / "tx_0.txt.events.json").read_text())) == 8


def _write_like_the_writer(path: Path, packets):
    """Place each event's words where BoardDataWriter would."""
    frames = [dict() for _ in range(60)]
    for entry, words in packets:
        (link, (first, _)), = entry["links"].items()
        for i, w in enumerate(words):
            frames[first + i][int(link)] = LinkWord(w, valid=True, start=i == 0, last=i == len(words) - 1)
    write_pattern_file(path, frames, [0, 1, 2])


def test_forge_reads_time_multiplexed_events_by_the_writers_frames(tmp_path):
    (entries,) = [f["events"] for f in cmssw_sidecar.layout(WRITER, cmssw_sidecar.events_from_log(LOG[:300]))]
    packets = [(e, [0x10 * (k + 1) + j for j in range(3)]) for k, e in enumerate(entries)]
    _write_like_the_writer(tmp_path / "tx_0.txt", packets)
    (tmp_path / "tx_0.txt.events.json").write_text(json.dumps(entries))
    ifaces = [Interface(f"q{k}", "valid", "out", [Port(f"q{k}", "out", 64)], valid=Port(f"q{k}_valid", "out", 1))
              for k in range(3)]
    ref = DataRef(tmp_path / "tx_0.txt", "empv2", {"q0": 0, "q1": 1, "q2": 2}, tmp_path / "tx_0.txt.events.json")

    tagged = load_tagged(ref, Bench("t", "clk", "rst", True, ifaces), inputs=False)

    assert tagged["q1"] == [("1:7:101", 0x20), ("1:7:101", 0x21), ("1:7:101", 0x22)]
    assert {e for e, _ in tagged["q0"]} == {"1:7:100", "1:7:103"}


def test_a_file_that_does_not_match_the_layout_is_refused(tmp_path):
    (entries,) = [f["events"] for f in cmssw_sidecar.layout(WRITER, cmssw_sidecar.events_from_log(LOG[:300]))]
    _write_like_the_writer(tmp_path / "tx_0.txt", [(entries[0], [1, 2])])
    (tmp_path / "tx_0.txt.events.json").write_text(json.dumps(entries))
    iface = Interface("q1", "valid", "out", [Port("q1", "out", 64)], valid=Port("q1_valid", "out", 1))
    ref = DataRef(tmp_path / "tx_0.txt", "empv2", {"q1": 1}, tmp_path / "tx_0.txt.events.json")

    with pytest.raises(ValueError, match="no valid word on link 1"):
        load_tagged(ref, Bench("t", "clk", "rst", True, [iface]), inputs=False)
