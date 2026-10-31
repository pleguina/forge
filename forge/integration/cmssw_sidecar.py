"""Event names and frame ranges for pattern files CMSSW's ``BoardDataWriter`` wrote.

``L1Trigger/DemonstratorTools``' ``BoardDataWriter`` places the events it is
given one after the other: on a channel of TMUX period ``tmux``, every
``tmux * framesPerBX`` frames, rotating over the channel's indices when its
period is a multiple of the board's, after ``offset`` frames, with a new file
(``<path>_<n>.<ext>``) once ``maxFramesPerFile`` would be overrun. Which event
went where is therefore fixed by the writer's settings and the order the
events were added. This script writes, next to each file, the ``events:``
file FORGE reads: one entry per event with its run, lumi and event number and
the frames of its packet on every channel, so FORGE checks the packets it
finds against them instead of counting packets.

It needs Python 3 only, not FORGE, so it runs in a CMSSW environment::

    python3 cmssw_sidecar.py --writer writer.json --log cmsRun.log [--provenance prov.json]

``writer.json`` repeats the writer's constructor arguments::

    {"path": "L1GTTInputFile", "ext": "txt", "frames_per_bx": 9, "board_tmux": 6,
     "max_frames_per_file": 1024,
     "channels": {"tracks": {"tmux": 18, "gap": 6, "offset": 0, "indices": [0, 1, 2]}}}

The events, in the order the writer received them, come from the job's log
(``Begin processing the Nth record. Run R, Event E, LumiSection L``; exact
for a job on one stream) or from ``--events`` (a JSON list of
``{run, lumi, event}``). The CMSSW release and commit are taken from the
environment, and anything else that decided the sample (configuration,
GlobalTag, LUT hashes) from ``--provenance``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

_BEGIN = re.compile(r"Begin processing the \d+\w* record\. Run (\d+), Event (\d+), LumiSection (\d+)")


def events_from_log(text: str) -> List[Dict[str, int]]:
    """``{run, lumi, event}`` of every record a cmsRun log says it began, in order."""
    return [{"run": int(r), "lumi": int(lumi), "event": int(e)} for r, e, lumi in _BEGIN.findall(text)]


def layout(writer: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Per output file, in order: its name and the events it holds, each with
    ``links``: channel index -> ``[first, last]`` frame of the event's slot
    (the packet starts at ``first`` and ends at or before ``last``)."""
    per_bx = int(writer["frames_per_bx"])
    board = int(writer["board_tmux"])
    max_frames = int(writer["max_frames_per_file"])
    channels = writer["channels"]
    per_file = max_frames
    for name, spec in channels.items():
        tmux = int(spec["tmux"])
        if tmux % board:
            raise ValueError(f"channel {name}: TMUX {tmux} is not a multiple of the board TMUX {board}")
        ratio = tmux // board
        if len(spec["indices"]) != ratio:
            raise ValueError(f"channel {name}: {len(spec['indices'])} indices for a link:board TMUX ratio of {ratio}")
        per_file = min(per_file, (max_frames - int(spec.get("offset", 0))) // (per_bx * board) - (ratio - 1))
    if per_file < 1:
        raise ValueError("max_frames_per_file holds no event")
    files: List[Dict[str, Any]] = []
    for start in range(0, len(events), per_file):
        chunk = events[start:start + per_file]
        entries = []
        for k, event in enumerate(chunk):
            links: Dict[str, List[int]] = {}
            for spec in channels.values():
                tmux, ratio = int(spec["tmux"]), int(spec["tmux"]) // board
                first = int(spec.get("offset", 0)) + (k % ratio) * board * per_bx + (k // ratio) * tmux * per_bx
                links[str(spec["indices"][k % ratio])] = [first, first + tmux * per_bx - int(spec.get("gap", 0)) - 1]
            entries.append({**{key: event[key] for key in ("run", "lumi", "event", "bx") if key in event},
                            "links": links})
        files.append({"file": f"{writer['path']}_{len(files)}.{writer.get('ext', 'txt')}", "events": entries})
    return files


def provenance(extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {k: os.environ[v] for k, v in (("cmssw_release", "CMSSW_VERSION"),
                                                          ("cmssw_git_hash", "CMSSW_GIT_HASH"),
                                                          ("scram_arch", "SCRAM_ARCH")) if os.environ.get(v)}
    out.update(extra or {})
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--writer", required=True, help="JSON of the BoardDataWriter's settings")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--log", help="the cmsRun log of the job that wrote the files")
    source.add_argument("--events", help="JSON list of {run, lumi, event} in the order they were written")
    parser.add_argument("--provenance", help="JSON of what else decided the sample (config, GlobalTag, LUTs)")
    parser.add_argument("--config", help="the cmsRun configuration, recorded by checksum")
    parser.add_argument("--out", default=None, help="directory for the sidecars (default: next to each file)")
    args = parser.parse_args(argv)

    writer = json.loads(Path(args.writer).read_text())
    events = events_from_log(Path(args.log).read_text()) if args.log else json.loads(Path(args.events).read_text())
    if not events:
        print("no events: the log names no processed record", file=sys.stderr)
        return 1
    extra = json.loads(Path(args.provenance).read_text()) if args.provenance else {}
    if args.config:
        extra["cmsrun_config"] = {"path": args.config,
                                  "sha256": hashlib.sha256(Path(args.config).read_bytes()).hexdigest()}
    origin = provenance(extra)
    for entry in layout(writer, events):
        target = Path(args.out) / Path(entry["file"]).name if args.out else Path(entry["file"])
        target.parent.mkdir(parents=True, exist_ok=True)
        Path(f"{target}.events.json").write_text(json.dumps(entry["events"], indent=1) + "\n")
        Path(f"{target}.provenance.json").write_text(json.dumps(
            {**origin, "writer": dict(writer), "file": entry["file"], "events": len(entry["events"])}, indent=1) + "\n")
        print(f"{target}.events.json: {len(entry['events'])} event(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
