"""Tests for forge.analyze.throughput_runtime.probe (release-plan Phase
10, slice 10.0C).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forge.analyze.throughput_runtime.probe import build_runtime_throughput_result

_CSV_HEADER = "cycle,fifo_full,fifo_empty,fifo_occupancy,fifo_overflow\n"


def _write_csv(tmp_path: Path, rows: "list[str]") -> Path:
    p = tmp_path / "probe.csv"
    p.write_text(_CSV_HEADER + "\n".join(rows) + "\n")
    return p


def test_basic_accepted_emitted_and_stall_counts(tmp_path):
    # 4 cycles: full deasserted (accept) every cycle, empty deasserted
    # (emit) for 3 of 4 cycles (1 stall on the read side).
    rows = [
        "0,0,0,1,0",
        "1,0,0,2,0",
        "2,0,1,1,0",   # empty asserted -> a read-side stall
        "3,0,0,0,0",
    ]
    csv = _write_csv(tmp_path, rows)
    result = build_runtime_throughput_result(
        csv, "xform:result_stream:async_fifo",
        full_signal="fifo_full", empty_signal="fifo_empty",
        occupancy_signal="fifo_occupancy", overflow_signal="fifo_overflow",
    )
    assert result.accepted_transactions == 4
    assert result.emitted_transactions == 3
    assert result.stall_cycles == 1
    assert result.high_water_mark == 2
    assert result.dropped_transactions == 0
    assert result.duplicated_transactions == 0


def test_full_events_counts_only_rising_transitions(tmp_path):
    rows = [
        "0,0,1,0,0",   # empty: 0->1 rising edge #1
        "1,0,1,0,0",   # still high, not a new edge
        "2,0,0,0,0",
        "3,0,1,0,0",   # empty: 0->1 rising edge #2
    ]
    csv = _write_csv(tmp_path, rows)
    result = build_runtime_throughput_result(
        csv, "fifo", full_signal="fifo_full", empty_signal="fifo_empty",
    )
    assert result.empty_events == 2
    assert result.full_events == 0


def test_dropped_transactions_counts_overflow_attempts(tmp_path):
    rows = [
        "0,1,0,3,1",   # overflow attempt
        "1,1,0,3,1",   # overflow attempt
        "2,0,0,2,0",
    ]
    csv = _write_csv(tmp_path, rows)
    result = build_runtime_throughput_result(
        csv, "fifo", full_signal="fifo_full", empty_signal="fifo_empty",
        overflow_signal="fifo_overflow",
    )
    assert result.dropped_transactions == 2


def test_occupancy_and_overflow_are_optional(tmp_path):
    rows = ["0,0,0", "1,0,0"]
    p = tmp_path / "probe.csv"
    p.write_text("cycle,fifo_full,fifo_empty\n" + "\n".join(rows) + "\n")
    result = build_runtime_throughput_result(
        p, "fifo", full_signal="fifo_full", empty_signal="fifo_empty",
    )
    assert result.high_water_mark == 0
    assert result.dropped_transactions == 0


def test_hex_multi_bit_occupancy_values_parse_correctly(tmp_path):
    # sv_testbench_generator.py writes multi-bit probes as bare hex
    # (no "0x" prefix, %0h format) — "a" must parse as 10, not fail.
    rows = ["0,0,0,a,0", "1,0,0,3,0"]
    csv = _write_csv(tmp_path, rows)
    result = build_runtime_throughput_result(
        csv, "fifo", full_signal="fifo_full", empty_signal="fifo_empty",
        occupancy_signal="fifo_occupancy",
    )
    assert result.high_water_mark == 10


def test_measured_rate_uses_clock_frequency(tmp_path):
    rows = [f"{i},0,0,0,0" for i in range(100)]  # 100 cycles, all emitted
    csv = _write_csv(tmp_path, rows)
    result = build_runtime_throughput_result(
        csv, "fifo", full_signal="fifo_full", empty_signal="fifo_empty",
        clock_frequency_hz=100.0,  # 100 cycles / 100 Hz = 1 second elapsed
    )
    assert result.measured_rate_records_per_sec == 100.0


def test_measured_rate_defaults_to_zero_without_clock_frequency(tmp_path):
    rows = ["0,0,0,0,0"]
    csv = _write_csv(tmp_path, rows)
    result = build_runtime_throughput_result(
        csv, "fifo", full_signal="fifo_full", empty_signal="fifo_empty",
    )
    assert result.measured_rate_records_per_sec == 0.0


def test_raises_when_required_signal_missing(tmp_path):
    p = tmp_path / "probe.csv"
    p.write_text("cycle,unrelated_signal\n0,1\n")
    with pytest.raises(ValueError, match="fifo_full"):
        build_runtime_throughput_result(
            p, "fifo", full_signal="fifo_full", empty_signal="fifo_empty",
        )
