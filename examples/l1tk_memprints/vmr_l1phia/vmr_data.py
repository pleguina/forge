"""The VMRouter L1PHIA's inputs and expected outputs, from the emulation's memory printouts.

As the firmware's own test bench (TestBenches/VMRouterCM_test.cpp) feeds it:
the input stub memories in file-name order are inputStubs[0..3], each event
written into page bx % 4 with its entry count, and bx = event number.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from memprint import read  # noqa: E402

INPUTS = ["IL_L1PHIA_PS10G_1_A", "IL_L1PHIA_PS10G_2_A", "IL_L1PHIA_negPS10G_1_A", "IL_L1PHIA_negPS10G_2_A"]
PAGES = 4


def inputs(memprints: Path):
    stubs = [read(memprints / "InputStubs" / f"InputStubs_{name}_04.dat") for name in INPUTS]
    events = []
    for event in sorted(stubs[0]):
        call = {"bx": event % 8}
        row = {"id": event, "call": call}
        for i, memory in enumerate(stubs):
            words = memory.get(event, [])
            row[f"inputStubs_dataarray_{i}"] = words
            call[f"inputStubs_nentries_{i}_{event % PAGES}"] = len(words)
        events.append(row)
    return events


def expected(memprints: Path):
    allstubs = read(memprints / "Stubs" / "AllStubs_AS_L1PHIAn1_04.dat")
    me = read(memprints / "VMStubsME" / "VMStubs_VMSME_L1PHIAn2_04.dat")
    return [{"id": e, "memoriesAS_dataarray_s": allstubs.get(e, []), "memoryME": me.get(e, [])}
            for e in sorted(allstubs)]
