"""forge.analyze.throughput_runtime.probe — build
:class:`~forge.verify.throughput_result.RuntimeThroughputResult` from a
real simulation's Tier 2 probe CSV (release-plan Phase 10, slice 10.0C —
preflight.md §5 Decision B).

Reuses :func:`forge.analyze.latency_runtime.probe.load_wide_probe_csv`
directly — no new CSV parsing. A FIFO's ``full``/``empty``/``occupancy``/
``overflow_attempt`` signals must already be declared as ``tier2_probes:``
entries (``port_map.yaml``) bound to the generated ``cdc_async_fifo``
instance's own ports; this module only consumes the resulting CSV, it
does not declare probes itself.

Field derivations (documented, not hidden): FORGE's structural port_map
wiring has no generic valid/ready concept, so "accepted"/"emitted" are
approximated from the FIFO's own internal guards — a write-domain cycle
with ``full`` deasserted is a real accepted write (the FIFO's own
``!full`` guard is what actually gates the write), and symmetrically for
reads/``empty``. ``stall_cycles`` sums both sides' stalled cycles
(``full`` asserted on the write side, ``empty`` asserted on the read
side) — a producer-side and a consumer-side stall are both real stalls,
counted together since nothing here disambiguates whose stall a given
cycle "belongs" to. ``duplicated_transactions`` is always 0 — this
FIFO design has no mechanism that could duplicate a transaction.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from forge.analyze.latency_runtime.probe import ProbeEvent, load_wide_probe_csv
from forge.verify.throughput_result import RuntimeThroughputResult

_ASSERTED = {"1", "1'b1", "high", "true", "yes"}


def _is_asserted(value: str) -> bool:
    return value.strip().lower() in _ASSERTED


def _values_for_signal(events: "List[ProbeEvent]", signal: str) -> "List[Tuple[int, str]]":
    return sorted(((e.cycle, e.value) for e in events if e.signal == signal), key=lambda t: t[0])


def _count_rising_edges(values: "List[Tuple[int, str]]") -> int:
    count = 0
    prev_high = False
    for _cycle, value in values:
        high = _is_asserted(value)
        if high and not prev_high:
            count += 1
        prev_high = high
    return count


def build_runtime_throughput_result(
    probe_csv: Path,
    fifo_object_id: str,
    *,
    full_signal: str,
    empty_signal: str,
    occupancy_signal: "Optional[str]" = None,
    overflow_signal: "Optional[str]" = None,
    clock_frequency_hz: "Optional[float]" = None,
) -> RuntimeThroughputResult:
    """Build one FIFO instance's :class:`RuntimeThroughputResult` from a
    real wide-format Tier 2 probe CSV.

    ``occupancy_signal``/``overflow_signal`` are optional — when omitted,
    ``high_water_mark``/``dropped_transactions`` stay ``0`` (an honest
    "not measured", not a fabricated value) rather than requiring every
    caller to have wired every probe.

    Raises:
        ValueError: *probe_csv* has no rows for *full_signal* or
            *empty_signal* — both are required to derive anything at all.
    """
    events = load_wide_probe_csv(probe_csv)
    full_vals = _values_for_signal(events, full_signal)
    empty_vals = _values_for_signal(events, empty_signal)

    if not full_vals or not empty_vals:
        missing = full_signal if not full_vals else empty_signal
        raise ValueError(f"probe CSV {probe_csv} has no rows for signal {missing!r}")

    accepted_transactions = sum(1 for _, v in full_vals if not _is_asserted(v))
    emitted_transactions = sum(1 for _, v in empty_vals if not _is_asserted(v))
    stall_cycles = (
        sum(1 for _, v in full_vals if _is_asserted(v))
        + sum(1 for _, v in empty_vals if _is_asserted(v))
    )
    full_events = _count_rising_edges(full_vals)
    empty_events = _count_rising_edges(empty_vals)

    high_water_mark = 0
    if occupancy_signal:
        occ_vals = _values_for_signal(events, occupancy_signal)
        # Multi-bit probes are written as bare hex (no "0x" prefix, see
        # sv_testbench_generator.py's %0h format for width>1 signals);
        # base-16 parsing is also valid for the width==1 (%0b, "0"/"1")
        # degenerate case.
        if occ_vals:
            high_water_mark = max(int(v, 16) for _, v in occ_vals if v)

    dropped_transactions = 0
    if overflow_signal:
        ovf_vals = _values_for_signal(events, overflow_signal)
        dropped_transactions = sum(1 for _, v in ovf_vals if _is_asserted(v))

    cycles = [c for c, _ in full_vals]
    total_cycles = (max(cycles) - min(cycles) + 1) if cycles else 0
    measured_rate = 0.0
    if clock_frequency_hz and total_cycles > 0:
        elapsed_s = total_cycles / clock_frequency_hz
        if elapsed_s > 0:
            measured_rate = emitted_transactions / elapsed_s

    return RuntimeThroughputResult(
        fifo_object_id=fifo_object_id,
        accepted_transactions=accepted_transactions,
        emitted_transactions=emitted_transactions,
        stall_cycles=stall_cycles,
        high_water_mark=high_water_mark,
        full_events=full_events,
        empty_events=empty_events,
        measured_rate_records_per_sec=measured_rate,
        dropped_transactions=dropped_transactions,
        duplicated_transactions=0,
    )
