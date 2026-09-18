#!/usr/bin/env python3
"""Build a real ``forge.throughput_result.v1`` artifact for the
full-functional design from real HLS synthesis reports plus a real
Tier 2 probe CSV (``algo_top_probe.csv``, produced by ``forge verify run
--probe-log``).

Structurally identical to render_throughput_result.py (its
packetizer_xsim counterpart) -- same limitations, and the same fix for
the rate/dataset-identity bugs an independent audit found in a prior
version of that script, apply unchanged (single-clock Tier 2 probe
sampling on ap_clk, generic accepted/emitted approximation overridden by
this design's own real, exactly-known conservation-invariant counts,
measured_rate_records_per_sec recomputed from the override numerator
against the probe's real elapsed_seconds window and this flow's own real
probe clock (verify.flow.yml's clk_period_ns), dataset_hash/scenario_hash
populated from this flow's own golden_model_provenance.json instead of
left null -- see that script's docstring for the full derivation). Two
real differences, both found running this design specifically:

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

import yaml

from forge.analysis.hls_reports.extractor import collect_reports
from forge.analysis.throughput_runtime.probe import build_runtime_throughput_result
from forge.analysis.throughput_static.model import build_design_throughput_analysis
from forge.verification.throughput_result import THROUGHPUT_RESULT_SCHEMA, ThroughputResult

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
_FLOW_DIR = Path(__file__).resolve().parents[1] / "full_functional_xsim"
_PROBE_CSV = _FLOW_DIR / "xsim_work/algo_top_probe.csv"
_OUT_PATH = _FLOW_DIR / "throughput_result.json"
_FLOW_YML = _FLOW_DIR / "verify.flow.yml"
_GOLDEN_PROVENANCE_JSON = _FLOW_DIR / "golden_model_provenance.json"
_PROVENANCE_JSON = _REPO_ROOT / "gen-top/design_vision_pipeline_full_functional/provenance.json"

# Primary output-field width per module (documented, not a rigorous
# per-interface derivation): normalized_pixel (8b), gradient_magnitude
# (12b), variance (24b, tile_stats_hls's widest output field).
_DATA_WIDTH_BITS = {
    "pixel_normalizer": 8,
    "sobel_hls": 12,
    "tile_stats_hls": 24,
}
# All three modules live in this design's pixel domain (ap_clk) -- their
# HLS-estimated/synthesis-target clock, used for the *static* nominal-
# capacity numbers only (see render_throughput_result.py's own docstring
# for why this is not necessarily this flow's actual simulated ap_clk rate).
_CLOCK_MHZ = 200.0


def _probe_clock_hz() -> float:
    """The clock that actually drove this flow's Tier 2 probe sampling,
    read from this flow's own verify.flow.yml -- see
    render_throughput_result.py's own docstring/helper for why."""
    flow = yaml.safe_load(_FLOW_YML.read_text())
    clk_period_ns = flow["simulation"]["clk_period_ns"]
    return 1e9 / clk_period_ns


def main() -> None:
    reports = collect_reports(_HLS_BUILD_ROOT)
    static, bottleneck, predicted_rate = build_design_throughput_analysis(
        reports,
        _DATA_WIDTH_BITS,
        clock_frequency_mhz_by_module={m: _CLOCK_MHZ for m in _DATA_WIDTH_BITS},
    )

    probe_clock_hz = _probe_clock_hz()
    runtime = [
        build_runtime_throughput_result(
            _PROBE_CSV, "xform:pr_record_out:async_fifo",
            full_signal="pr_full", empty_signal="pr_empty",
            occupancy_signal="pr_occupancy", overflow_signal="pr_overflow",
            clock_frequency_hz=probe_clock_hz,
        ),
        build_runtime_throughput_result(
            _PROBE_CSV, "xform:ts_record_out:async_fifo",
            full_signal="ts_full", empty_signal="ts_empty",
            occupancy_signal="ts_occupancy", overflow_signal="ts_overflow",
            clock_frequency_hz=probe_clock_hz,
        ),
    ]
    # Override accepted/emitted with this design's own real, exactly-known
    # counts (see module docstring) -- occupancy-derived fields untouched.
    # See render_throughput_result.py's own override for why the rate is
    # recomputed here too, and why duplicated_transactions=0 is set
    # explicitly (backed by this design's own conservation-invariant check).
    def _override(r):
        emitted = _REAL_ACCEPTED_EMITTED[r.fifo_object_id]
        rate = (emitted / r.elapsed_seconds) if r.elapsed_seconds > 0 else 0.0
        return dataclasses.replace(
            r,
            accepted_transactions=emitted,
            emitted_transactions=emitted,
            measured_rate_records_per_sec=rate,
            duplicated_transactions=0,
        )

    runtime = [_override(r) for r in runtime]
    observed_rate = sum(r.measured_rate_records_per_sec for r in runtime)
    design_hash = json.loads(_PROVENANCE_JSON.read_text())["ir_content_hash"]
    golden_provenance = json.loads(_GOLDEN_PROVENANCE_JSON.read_text())

    result = ThroughputResult(
        schema=THROUGHPUT_RESULT_SCHEMA,
        design_hash=design_hash,
        dataset_hash=golden_provenance["input_dataset_hash"],
        scenario_hash=golden_provenance["output_hash"],
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
            f"full_events={r.full_events} dropped={r.dropped_transactions} "
            f"elapsed={r.elapsed_seconds * 1e9:.1f}ns "
            f"rate={r.measured_rate_records_per_sec:,.0f} records/s"
        )


if __name__ == "__main__":
    main()
