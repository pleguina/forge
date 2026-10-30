"""EMP link words and pattern files.

An EMP pattern file holds one row per frame and one column per link; each
cell is a link word: its metadata bits and 64 data bits. The metadata is
start of orbit, start of packet, end of packet and valid, optionally led by
strobe (``(strobe,)`` in the header): four bits mean strobe is 1, five bits
carry it. emp-fwk's testbench plays these files into ``emp_payload`` and
captures its outputs in the same format.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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
    strobe: bool = True

    def cell(self, with_strobe: bool = False) -> str:
        bits = [self.strobe] if with_strobe else []
        bits += [self.start_of_orbit, self.start, self.last, self.valid]
        return "".join("1" if b else "0" for b in bits) + f" {self.data:016x}"


IDLE = LinkWord()


def packet(data: int) -> LinkWord:
    """A one-frame packet carrying *data*."""
    return LinkWord(data, valid=True, start=True, last=True)


@dataclass
class BoardData:
    """The contents of a pattern file: its ID and every link's words, frame by frame."""
    name: str = "x0"
    links: Dict[int, List[LinkWord]] = field(default_factory=dict)

    def frames(self) -> List[Dict[int, LinkWord]]:
        n = max((len(w) for w in self.links.values()), default=0)
        return [{link: words[i] for link, words in self.links.items() if i < len(words)} for i in range(n)]

    def packets(self, link: int) -> List[List[LinkWord]]:
        """The valid words of *link* grouped start to last, a packet per list."""
        out: List[List[LinkWord]] = []
        current: List[LinkWord] = []
        for w in self.links.get(link, []):
            if not w.valid:
                continue
            if w.start and current:
                out.append(current)
                current = []
            current.append(w)
            if w.last:
                out.append(current)
                current = []
        if current:
            out.append(current)
        return out


def write_pattern_file(
    path: Path, frames: Sequence[Mapping[int, LinkWord]], links: Sequence[int], *, board: str = "x0",
) -> None:
    """Write *frames* (link -> word, idle where absent) for *links*; strobe is
    written only when some word has it at 0."""
    header = "".join(f"            {link:03d}        " for link in links)
    strobe = any(not w.strobe for frame in frames for w in frame.values())
    rows = [
        f"Frame {i:04d}  " + "".join(f"  {frame.get(link, IDLE).cell(strobe)}" for link in links)
        for i, frame in enumerate(frames)
    ]
    path.write_text(
        f"ID: {board}\n"
        "Metadata: (strobe,) start of orbit, start of packet, end of packet, valid\n\n"
        f"      Link  {header}\n" + "\n".join(rows) + "\n"
    )


def write_board_data(path: Path, data: BoardData) -> None:
    write_pattern_file(path, data.frames(), sorted(data.links), board=data.name)


def read_board_data(path: Path) -> BoardData:
    text = path.read_text()
    m = re.match(r"\s*ID:\s*(\S+)", text)
    return BoardData(m.group(1) if m else "x0", read_pattern_file(path))


def is_pattern_file(path: Path) -> bool:
    """Whether *path* reads like an EMP pattern file (by its header, not its suffix)."""
    try:
        head = path.read_text(errors="replace")[:400]
    except OSError:
        return False
    return bool(re.search(r"^\s*Metadata:.*valid", head, re.M) and re.search(r"^\s*Link\b", head, re.M))


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
            if len(meta) not in (4, 5) or set(meta) - {"0", "1"}:
                raise ValueError(f"{path}: frame {tokens[1]}, link {link}: metadata {meta!r} is not 4 or 5 bits")
            strobe = meta[0] == "1" if len(meta) == 5 else True
            sof, start, last, valid = (c == "1" for c in meta[-4:])
            out[link].append(LinkWord(int(data, 16), valid, start, last, sof, strobe))
    return out
