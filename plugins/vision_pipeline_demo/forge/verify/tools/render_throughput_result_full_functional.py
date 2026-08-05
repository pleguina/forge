#!/usr/bin/env python3
"""Build a real ``forge.throughput_result.v1`` artifact for the
full-functional design from real HLS synthesis reports plus a real
Tier 2 probe CSV (``algo_top_probe.csv``, produced by ``forge verify run
--probe-log``).

Structurally identical to render_throughput_result.py (its
packetizer_xsim counterpart) -- same limitations apply unchanged (single-clock
Tier 2 probe sampling on ap_clk, generic accepted/emitted approximation
overridden by this design's own real, exactly-known conservation-invariant
counts, see that script's docstring for the full derivation). Two real
differences, both found running this design specifically:

  * Real record counts are 256 pixel-result + 4 tile-statistics (this
    design's real 16x16/2x2-tile frame), not 64 + 1.
  * The pixel-result crossing's real FIFO depth is 128, not 64 (see
    design_full_functional.yml's own header for why 64 was found to
    genuinely overflow this design's real, sustained 256-pixel burst) --
    the real measured high-water mark (64/128, zero overflow) is exactly
    what this script reports below, not assumed.

Usage::

    python3 render_throughput_result_full_functional.py
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from forge.analyze.hls_reports.extractor import collect_reports
from forge.analyze.throughput_runtime.probe import build_runtime_throughput_result
from forge.analyze.throughput_static.model import build_design_throughput_analysis
from forge.verify.throughput_result import THROUGHPUT_RESULT_SCHEMA, ThroughputResult

# Real, exactly-known record counts from this design's own functional
# verification (gen_stimulus_full_functional.py's conservation-invariant
# check) -- see module docstring for why these override the generic
# probe-derived accepted/emitted approximation.
_REAL_ACCEPTED_EMITTED = {
    "xform:pr_record_out:async_fifo": 256,
    "xform:ts_record_out:async_fifo": 4,
}

_REPO_ROOT = Path(__file__).resolve().parents[5]
_HLS_BUILD_ROOT = _REPO_ROOT / "build_hls_vision_pipeline_demo"
_PROBE_CSV = Path(__file__).resolve().parents[1] / "full_functional_xsim/xsim_work/algo_top_probe.csv"
_OUT_PATH = Path(__file__).resolve().parents[1] / "full_functional_xsim/throughput_result.json"
_PROVENANCE_JSON = _REPO_ROOT / "gen-top/design_vision_pipeline_full_functional/provenance.json"

# Primary output-field width per module (documented, not a rigorous
# per-interface derivation): normalized_pixel (8b), gradient_magnitude
# (12b), variance (24b, tile_stats_hls's widest output field).
_DATA_WIDTH_BITS = {
    "pixel_normalizer": 8,
    "sobel_hls": 12,
    "tile_stats_hls": 24,
}
# All three modules live in this design's pixel domain (ap_clk, 200MHz) --
# the real declared clock, not each module's own HLS-estimated fmax.
_CLOCK_MHZ = 200.0

# Probe CSV rows are sampled on ap_clk (200MHz) -- see
# render_throughput_result.py's own docstring for why that's the correct
# rate for the write-domain probes and an approximation for the
# read-domain ones.
_PROBE_CLOCK_HZ = 200_000_000.0


def main() -> None:
    reports = collect_reports(_HLS_BUILD_ROOT)
    static, bottleneck, predicted_rate = build_design_throughput_analysis(
        reports,
        _DATA_WIDTH_BITS,
        clock_frequency_mhz_by_module={m: _CLOCK_MHZ for m in _DATA_WIDTH_BITS},
    )

    runtime = [
        build_runtime_throughput_result(
            _PROBE_CSV, "xform:pr_record_out:async_fifo",
            full_signal="pr_full", empty_signal="pr_empty",
            occupancy_signal="pr_occupancy", overflow_signal="pr_overflow",
            clock_frequency_hz=_PROBE_CLOCK_HZ,
        ),
        build_runtime_throughput_result(
            _PROBE_CSV, "xform:ts_record_out:async_fifo",
            full_signal="ts_full", empty_signal="ts_empty",
            occupancy_signal="ts_occupancy", overflow_signal="ts_overflow",
            clock_frequency_hz=_PROBE_CLOCK_HZ,
        ),
    ]
    # Override accepted/emitted with this design's own real, exactly-known
    # counts (see module docstring) -- occupancy-derived fields untouched.
    runtime = [
        dataclasses.replace(
            r,
            accepted_transactions=_REAL_ACCEPTED_EMITTED[r.fifo_object_id],
            emitted_transactions=_REAL_ACCEPTED_EMITTED[r.fifo_object_id],
        )
        for r in runtime
    ]
    observed_rate = sum(r.measured_rate_records_per_sec for r in runtime)
    design_hash = json.loads(_PROVENANCE_JSON.read_text())["ir_content_hash"]

    result = ThroughputResult(
        schema=THROUGHPUT_RESULT_SCHEMA,
        design_hash=design_hash,
        static=static,
        runtime=runtime,
        predicted_rate=predicted_rate,
        observed_rate=observed_rate,
        bottleneck=bottleneck,
    )

    _OUT_PATH.write_text(json.dumps(result.to_dict(), indent=2) + "\n")
    print(f"  wrote {_OUT_PATH}")
    print(f"  bottleneck={bottleneck!r} predicted_rate={predicted_rate:,.0f} records/s")
    for r in runtime:
        print(
            f"  {r.fifo_object_id}: accepted={r.accepted_transactions} "
            f"emitted={r.emitted_transactions} high_water={r.high_water_mark} "
            f"full_events={r.full_events} dropped={r.dropped_transactions}"
        )


if __name__ == "__main__":
    main()
