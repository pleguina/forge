"""forge.analyze.latency_runtime.probe
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Read simulation probe data from files written by forge verify runs.

Expected probe CSV format
--------------------------
A CSV with at minimum the columns ``cycle``, ``signal``, and ``value``::

    cycle,signal,value
    10,dec_0_raw_valid,1
    18,tout_out_valid,1

The file is written either by a plugin-specific ``gen_stimulus.py``
(as a side-effect of stimulus generation) or by a generic xsim observer
script.  The framework does not dictate the exact tool; it only defines the
column contract above.

The ``input_valid_signal`` and ``output_valid_signal`` names passed to
:func:`measure_latency` are fully plugin-defined — the framework provides
only the measurement mechanics.
"""
from __future__ import annotations

import csv
import dataclasses
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class ProbeEvent:
    signal: str
    cycle: int
    value: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_probe_csv(probe_csv: Path) -> List[ProbeEvent]:
    """Load a probe CSV → list of :class:`ProbeEvent`.

    Raises :class:`FileNotFoundError` if the file does not exist.
    """
    if not probe_csv.exists():
        raise FileNotFoundError(f"Probe CSV not found: {probe_csv}")

    events: List[ProbeEvent] = []
    with open(probe_csv, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                cycle = int(row["cycle"])
            except (KeyError, ValueError):
                continue
            events.append(ProbeEvent(
                signal=row.get("signal", ""),
                cycle=cycle,
                value=row.get("value", "").strip(),
            ))
    return events


_ASSERTED = {"1", "1'b1", "high", "true", "yes"}


def first_assert_cycle(events: List[ProbeEvent], signal: str) -> Optional[int]:
    """Return the first cycle where *signal* is in an asserted state."""
    for ev in events:
        if ev.signal == signal and ev.value.lower() in _ASSERTED:
            return ev.cycle
    return None


def measure_latency(
    events: List[ProbeEvent],
    input_valid_signal: str,
    output_valid_signal: str,
) -> Optional[int]:
    """Measure cycles from first input valid assertion to first output valid assertion.

    Returns ``None`` if either signal is never observed in the probe data.
    """
    t_in  = first_assert_cycle(events, input_valid_signal)
    t_out = first_assert_cycle(events, output_valid_signal)
    if t_in is None or t_out is None:
        return None
    return t_out - t_in
