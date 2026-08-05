#!/usr/bin/env python3
"""Negative fixture: "Connect an II=1 producer to an unbuffered,
non-backpressured II=2 consumer" and expect a diagnostic naming the
producer rate, the consumer rate, the unsustainable ratio, and suggested
remedies. This is a deliberately-failing scenario, exercising the
diagnostic path a real design would hit if a fast producer were wired
directly into a slower consumer with no buffering.

No new core-framework diagnostic code is needed for this: FORGE already
computes real per-module :class:`~forge.verify.throughput_result.StaticThroughputAnalysis`
records from real HLS synthesis reports -- this script is the same kind
of release-gate check a real design's own release-acceptance step would
run over them, comparing an unbuffered/non-backpressured producer/
consumer pair's nominal capacities.

Consumer side is deliberately synthetic, not a second real HLS module:
every real module in this plugin's own registry is II=1, so there is no
real production-code pair left to demonstrate an unsustainable rate
with (an earlier revision of this fixture used a real II=2
``tile_stats_hls`` before that module was optimized to II=1 -- see that
module's own source header). Building a dedicated slow HLS module just
to keep this fixture "real" was considered and rejected -- it would
need wiring somewhere (even if only nominally) to stay buildable,
risking exactly what it must not do: touch the real architecture or
pollute design_packetizer.yml's own real throughput/FIFO measurements.
A synthetic ``StaticThroughputAnalysis`` for the consumer side, built
directly (not via ``build_static_throughput_analysis`` from an HLS
report -- there is no HLS report to build it from) and clearly labeled
as such, checks the same real comparison logic (``check_sustainable``'s
own body is unchanged) against the real producer (``pixel_normalizer``'s
own already-synthesized report) without inventing a second real module.

Usage::

    python3 check_throughput_sustainability.py
    # exits 1 with a diagnostic if unsustainable (expected for this pair)
"""
from __future__ import annotations

import sys
from pathlib import Path

from forge.analyze.hls_reports.extractor import collect_reports
from forge.analyze.throughput_static.model import build_static_throughput_analysis
from forge.verify.throughput_result import StaticThroughputAnalysis

_REPO_ROOT = Path(__file__).resolve().parents[5]
_HLS_BUILD_ROOT = _REPO_ROOT / "build_hls_vision_pipeline_demo"
_CLOCK_MHZ = 200.0  # pixel domain's real declared clock frequency


def _synthetic_ii2_consumer(name: str) -> StaticThroughputAnalysis:
    """A hand-built, explicitly-synthetic II=2 consumer at the real
    pixel-domain clock -- see module docstring for why this isn't a real
    HLS module. Not derived from any HLS report; its own fields are
    picked to match a plausible real accumulator-style module (24-bit
    datapath, same shape ``tile_stats_hls`` used to have) purely for a
    representative, round-number diagnostic -- the exact width/II values
    are illustrative, not a claim about any real module.
    """
    ii = 2
    freq_mhz = _CLOCK_MHZ
    records_per_cycle = 1.0 / ii
    return StaticThroughputAnalysis(
        module_name=name,
        clock_frequency_mhz=freq_mhz,
        pipeline_ii=ii,
        records_per_cycle=records_per_cycle,
        data_width_bits=24,
        nominal_capacity_records_per_sec=freq_mhz * 1e6 * records_per_cycle,
    )


def check_sustainable(producer: StaticThroughputAnalysis, consumer: StaticThroughputAnalysis) -> None:
    """Raise ``SystemExit(1)`` with a full diagnostic if *producer*'s real
    nominal capacity exceeds *consumer*'s, when connected directly with
    no buffering/backpressure.
    """
    if producer.nominal_capacity_records_per_sec > consumer.nominal_capacity_records_per_sec:
        ratio = producer.nominal_capacity_records_per_sec / consumer.nominal_capacity_records_per_sec
        print(
            "FAIL: unsustainable throughput — "
            f"producer {producer.module_name!r} (II={producer.pipeline_ii}, "
            f"{producer.nominal_capacity_records_per_sec:,.0f} records/s) exceeds "
            f"consumer {consumer.module_name!r} (II={consumer.pipeline_ii}, "
            f"{consumer.nominal_capacity_records_per_sec:,.0f} records/s) — "
            f"ratio {ratio:.2f}x, with no buffering or backpressure declared "
            "between them.\n"
            "Suggested remedies: insert a real buffer (cdc.kind: async_fifo, "
            "even same-domain), decimate the producer's rate, widen the "
            "consumer's datapath, increase the consumer's clock frequency, "
            "or reduce the source rate.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print(
        f"OK: {producer.module_name!r} ({producer.nominal_capacity_records_per_sec:,.0f} records/s) "
        f"<= {consumer.module_name!r} ({consumer.nominal_capacity_records_per_sec:,.0f} records/s)"
    )


if __name__ == "__main__":
    reports = {r.module_name: r for r in collect_reports(_HLS_BUILD_ROOT)}
    # Real producer: pixel_normalizer's own already-synthesized report
    # (II=1, real 200 Mrecord/s at the real pixel-domain clock).
    real_producer = build_static_throughput_analysis(
        reports["pixel_normalizer"], data_width_bits=8, clock_frequency_mhz=_CLOCK_MHZ,
    )
    # Synthetic consumer -- see module docstring.
    synthetic_consumer = _synthetic_ii2_consumer("synthetic_ii2_consumer")
    check_sustainable(real_producer, synthetic_consumer)
