"""APx pattern files, as CMSSW's L1Trigger/DemonstratorTools writes them.

Layout (``utilities.cc`` at cms-sw/cmssw fdcffa67b8e, ``writeAPxFile``)::

    #Sideband ON
    #LinkLabel                LINK_00                    LINK_01
    #BeginData
    0x0000    0x08 0x0000000000000001    0x00 0x0000000000000000

one row per frame: the frame index, then per link a sideband byte and the
64-bit word. The sideband's bit order has changed over time and CMSSW's own
reader and writer disagree, so it is an explicit encoding here:

- ``2025`` (``writeAPxFile``): bit 0 first frame of orbit, 1 start of frame,
  2 end of frame, 3 valid;
- ``2021`` (``readAPxFile``): bit 0 valid, 1 start of frame, 2 first frame of
  orbit, 3 end of frame.

Bits 4-7 (CRC strobe and error, throttle) have no EMP link word field: a
file with any of them set is refused rather than read without them, and EMP's
strobe 0 cannot be written.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

from forge.integration.emp import BoardData, LinkWord

# field -> sideband bit
ENCODINGS: Dict[str, Dict[str, int]] = {
    "2025": {"start_of_orbit": 0, "start": 1, "last": 2, "valid": 3},
    "2021": {"valid": 0, "start": 1, "start_of_orbit": 2, "last": 3},
}
DEFAULT_ENCODING = "2025"


def _bits(encoding: str) -> Dict[str, int]:
    if encoding not in ENCODINGS:
        raise ValueError(f"APx sideband encoding {encoding!r}: want one of {', '.join(ENCODINGS)}")
    return ENCODINGS[encoding]


def sideband(w: LinkWord, encoding: str = DEFAULT_ENCODING) -> int:
    if not w.strobe:
        raise ValueError("an APx file has no strobe: a word with strobe 0 cannot be written")
    return sum(1 << bit for name, bit in _bits(encoding).items() if getattr(w, name))


def word(sb: int, data: int, encoding: str = DEFAULT_ENCODING) -> LinkWord:
    if sb >> 4:
        raise ValueError(f"sideband 0x{sb:02x} sets CRC or throttle bits, which a link word does not carry")
    return LinkWord(data, **{name: bool(sb >> bit & 1) for name, bit in _bits(encoding).items()})


def write_apx(path: Path, data: BoardData, encoding: str = DEFAULT_ENCODING) -> None:
    links = sorted(data.links)
    lines = ["#Sideband ON", "#LinkLabel" + "".join(f"                LINK_{i:02d}    " for i in links),
             "#BeginData"]
    for i, frame in enumerate(data.frames()):
        words = [frame.get(link, LinkWord()) for link in links]
        lines.append(f"0x{i:04x}" + "".join(f"    0x{sideband(w, encoding):02x} 0x{w.data:016x}" for w in words))
    path.write_text("\n".join(lines) + "\n")


def is_apx_file(path: Path) -> bool:
    try:
        head = path.read_text(errors="replace")[:2000]
    except OSError:
        return False
    return bool(re.search(r"^#LinkLabel\b", head, re.M) and re.search(r"^#BeginData\s*$", head, re.M))


def read_apx(path: Path, encoding: str = DEFAULT_ENCODING) -> BoardData:
    lines = path.read_text().splitlines()
    i = 1 if lines and lines[0].startswith("#Sideband") else 0
    if i >= len(lines) or not lines[i].startswith("#LinkLabel"):
        raise ValueError(f"{path}: no '#LinkLabel' header")
    labels = lines[i].split()[1:]
    if not all(re.fullmatch(r"LINK_\d+", t) for t in labels):
        raise ValueError(f"{path}: link labels must be LINK_<n>, got {' '.join(labels)}")
    links = [int(t[5:]) for t in labels]
    if i + 1 >= len(lines) or lines[i + 1].strip() != "#BeginData":
        raise ValueError(f"{path}: no '#BeginData' after the link labels")
    out: Dict[int, List[LinkWord]] = {link: [] for link in links}
    for n, line in enumerate(row for row in lines[i + 2:] if row.strip()):
        tokens = line.split()
        if len(tokens) != 2 * len(links) + 1:
            raise ValueError(f"{path}: row {n} has {len(tokens)} tokens, expected {2 * len(links) + 1}")
        if int(tokens[0], 16) != n:
            raise ValueError(f"{path}: expected row {n}, found {tokens[0]}")
        for col, link in enumerate(links):
            out[link].append(word(int(tokens[1 + 2 * col], 16), int(tokens[2 + 2 * col], 16), encoding))
    return BoardData("", out)
