#!/usr/bin/env python3
"""trigger_demo's golden events in and out of EMP pattern files.

    emp_tools.py source --out source.txt [--gap N]
        One event per frame on links 0-3 (each hit a one-frame packet),
        N idle frames after each.

    emp_tools.py check --sink sink.txt
        Compare the valid words captured on output link 0 with the golden
        trigger words.

    emp_tools.py check-entity --emp-fwk DIR
        Fail if emp_payload's ports differ from those in DIR's
        components/payload, which change between emp-fwk releases.
"""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from forge.integration.emp import IDLE, entity_ports, packet, read_pattern_file, write_pattern_file

_HERE = Path(__file__).resolve().parent
_VERIFY_TOOLS = _HERE.parents[1] / "forge" / "verify" / "tools"
_GOLDEN_XML = _HERE.parents[1] / "forge" / "verify" / "schemas" / "data" / "trigger_demo_golden.xml"
_PAYLOAD_VHD = _HERE.parent / "firmware" / "hdl" / "emp_payload.vhd"

sys.path.insert(0, str(_VERIFY_TOOLS))
from gen_stimulus import Event, parse_golden_xml  # noqa: E402

HIT_LINKS = [0, 1, 2, 3]
TRIGGER_LINK = 0


def write_source(events: List[Event], out: Path, gap: int) -> int:
    frames = []
    for event in events:
        by_channel = {ch.ch_id: ch for ch in event.channels}
        frames.append({
            link: packet(by_channel[link].raw) if by_channel[link].valid else IDLE
            for link in HIT_LINKS
        })
        frames += [{}] * gap
    write_pattern_file(out, frames, HIT_LINKS)
    return len(frames)


def check_sink(events: List[Event], sink: Path) -> List[str]:
    expected = [(e.event_id, e.golden.word) for e in events if e.golden.n_hits > 0]
    words = read_pattern_file(sink)[TRIGGER_LINK]
    got = [(frame, w.data) for frame, w in enumerate(words) if w.valid]
    errors = []
    if len(got) != len(expected):
        errors.append(f"{len(got)} valid output frames, expected {len(expected)}")
    for (event_id, word), (frame, data) in zip(expected, got):
        if data != word:
            errors.append(f"event {event_id}: frame {frame} word 0x{data:08x}, expected 0x{word:08x}")
    return errors


def check_entity(emp_fwk: Path) -> List[str]:
    reference = emp_fwk / "components" / "payload" / "firmware" / "hdl" / "emp_payload.vhd"
    ours, theirs = entity_ports(_PAYLOAD_VHD.read_text()), entity_ports(reference.read_text())
    return list(difflib.unified_diff(theirs, ours, str(reference), _PAYLOAD_VHD.name, lineterm=""))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("source")
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--gap", type=int, default=7)
    c = sub.add_parser("check")
    c.add_argument("--sink", type=Path, required=True)
    for each in (s, c):
        each.add_argument("--xml", type=Path, default=_GOLDEN_XML)
    e = sub.add_parser("check-entity")
    e.add_argument("--emp-fwk", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.cmd == "check-entity":
        diff = check_entity(args.emp_fwk)
        print("\n".join(diff) if diff else f"PASS emp_payload ports match {args.emp_fwk}")
        return 1 if diff else 0
    events = parse_golden_xml(args.xml)
    if args.cmd == "source":
        print(write_source(events, args.out, args.gap))
        return 0
    errors = check_sink(events, args.sink)
    for error in errors:
        print(f"FAIL {error}")
    if not errors:
        n = sum(1 for e in events if e.golden.n_hits > 0)
        print(f"PASS {n} trigger words on link {TRIGGER_LINK} match {args.xml.name}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
