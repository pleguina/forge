"""EMP link words and pattern files.

An EMP pattern file holds one row per frame and one column per link; each
cell is a link word: four metadata bits (start of orbit, start of packet,
end of packet, valid) and 64 data bits. emp-fwk's testbench plays these
files into ``emp_payload`` and captures its outputs in the same format.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Sequence

LWORD_WIDTH = 64


@dataclass(frozen=True)
class LinkWord:
    data: int = 0
    valid: bool = False
    start: bool = False
    last: bool = False
    start_of_orbit: bool = False

    def cell(self) -> str:
        meta = "".join("1" if b else "0" for b in (self.start_of_orbit, self.start, self.last, self.valid))
        return f"{meta} {self.data:016x}"


IDLE = LinkWord()


def packet(data: int) -> LinkWord:
    """A one-frame packet carrying *data*."""
    return LinkWord(data, valid=True, start=True, last=True)


def write_pattern_file(
    path: Path, frames: Sequence[Mapping[int, LinkWord]], links: Sequence[int], *, board: str = "x0",
) -> None:
    """Write *frames* (link -> word, idle where absent) for *links*."""
    header = "".join(f"            {link:03d}        " for link in links)
    rows = [
        f"Frame {i:04d}  " + "".join(f"  {frame.get(link, IDLE).cell()}" for link in links)
        for i, frame in enumerate(frames)
    ]
    path.write_text(
        f"ID: {board}\n"
        "Metadata: (strobe,) start of orbit, start of packet, end of packet, valid\n\n"
        f"      Link  {header}\n" + "\n".join(rows) + "\n"
    )


def read_pattern_file(path: Path) -> Dict[int, List[LinkWord]]:
    """The words of every link in *path*, one per frame."""
    lines = path.read_text().splitlines()
    link_row = next((i for i, line in enumerate(lines) if line.split()[:1] == ["Link"]), None)
    if link_row is None:
        raise ValueError(f"{path}: no 'Link' header row")
    links = [int(tok) for tok in lines[link_row].split()[1:]]
    out: Dict[int, List[LinkWord]] = {link: [] for link in links}
    for line in lines[link_row + 1:]:
        tokens = line.split()
        if not tokens or tokens[0] != "Frame":
            continue
        cells = tokens[2:]
        if len(cells) != 2 * len(links):
            raise ValueError(f"{path}: frame {tokens[1]} has {len(cells) // 2} words, expected {len(links)}")
        for col, link in enumerate(links):
            meta, data = cells[2 * col], cells[2 * col + 1]
            sof, start, last, valid = (c == "1" for c in meta[-4:])
            out[link].append(LinkWord(int(data, 16), valid, start, last, sof))
    return out


def entity_ports(vhdl: str, entity: str = "emp_payload") -> List[str]:
    """The port declarations of *entity*, without comments, case or spacing,
    to compare a payload against the emp-fwk release it is built with."""
    text = re.sub(r"--[^\n]*", "", vhdl)
    match = re.search(rf"entity\s+{entity}\s+is\s+port\s*\((.*?)\)\s*;\s*end", text, re.S | re.I)
    if not match:
        raise ValueError(f"no entity {entity} with a port clause")
    return [
        re.sub(r"\s*([:(),])\s*", r"\1", " ".join(decl.split())).lower()
        for decl in match.group(1).split(";")
    ]
