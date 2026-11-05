"""Read the L1 track finder's memory printouts as FORGE events.

The CMSSW L1 track emulation (L1Trigger/TrackFindingTracklet) writes the
contents of every memory between processing steps, one file per memory,
``<Kind>_<memory>_<sector>.dat``::

    BX = 000 Event : 0
    0x00 0110000|011111100110|00010110111000|111 0x60fcc2dc7
    0x01 1110110|000000101001|00010101111011|110 0xec0522bde
    BX = 001 Event : 1
    BX = 010 Event : 2
    0x1b 0x00 0000100|0101|110|111 0x01177

Each ``BX`` line opens an event (the BX is the 3-bit time slot, the event
number counts on), each other line is one entry: its address, for a binned
memory its bin, the fields in binary and the word in hex. An event with no
entries is kept as an empty one.

``load(directory)`` returns one FORGE event per event number, naming each
memory by its interface name (``NAMES``, else the memory name in lower case)
with its words in the order the emulation wrote them. Used as
``verification.formats: {l1tk-memprint: memprint.py:load}``.
"""

import re
from pathlib import Path
from typing import Dict, List

#: memory name in the file name -> interface name of the design under test
NAMES: Dict[str, str] = {}

_HEADER = re.compile(r"BX\s*=\s*([01]+)\s+Event\s*:\s*(\d+)")


def memory_of(path: Path) -> str:
    """``InputStubs_IL_L1PHIA_negPS10G_1_A_04.dat`` -> ``IL_L1PHIA_negPS10G_1_A``."""
    parts = path.stem.split("_")
    if len(parts) < 3:
        raise ValueError(f"{path.name}: want <Kind>_<memory>_<sector>.dat")
    return "_".join(parts[1:-1])


def read(path: Path) -> Dict[int, List[int]]:
    """event number -> words of one memory printout."""
    events: Dict[int, List[int]] = {}
    current = None
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        header = _HEADER.match(line.strip())
        if header:
            current = int(header.group(2))
            events.setdefault(current, [])
            continue
        tokens = line.split()
        if current is None or len(tokens) < 3 or not tokens[-1].startswith("0x"):
            raise ValueError(f"{path}:{number}: not a memory entry: {line!r}")
        events[current].append(int(tokens[-1], 16))
    return events


def load(directory: Path) -> List[dict]:
    files = sorted(Path(directory).glob("*.dat"))
    if not files:
        raise ValueError(f"{directory}: no memory printouts (*.dat)")
    per_event: Dict[int, dict] = {}
    for f in files:
        memory = memory_of(f)
        name = NAMES.get(memory, memory.lower())
        for event, words in read(f).items():
            per_event.setdefault(event, {})[name] = words
    return [{"id": event, **memories} for event, memories in sorted(per_event.items())]
