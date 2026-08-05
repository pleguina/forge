#!/usr/bin/env python3
"""Build a real ``forge.throughput_result.v1`` artifact for the
packetizer design from real HLS synthesis reports plus a real Tier 2
probe CSV (``algo_top_probe.csv``, produced by ``forge verify run
--probe-log``).

Known, honestly-stated limitation (found running this exact design, not
assumed): ``forge.verify.gen_sim``'s Tier 2 probe CSV sampling is tied to
a single clock (``ap_clk``) regardless of a given probe's own native
domain -- no earlier flow in this plugin measured a FIFO's *read*-domain
signals across a genuinely different, slower clock (``clk_output``, 8ns,
vs. ``ap_clk``'s 5ns), so this dual-clock design is the first to surface
the gap. The write-domain probes (``*_full``/``*_overflow``/
``*_occupancy``/``*_high_water``) are accurate -- they share ``ap_clk``,
their own real domain. The read-domain probes (``*_empty``/
``*_underflow``) are oversampled (~1.6x, the 8ns/5ns ratio) relative to
real ``clk_output`` edges, so ``emitted_transactions``/``empty_events``/
the read-domain share of ``stall_cycles``/``measured_rate_records_per_sec``
are approximate, not exact -- a real, pre-existing single-clock probe
sampling limitation, not a new bug. A per-probe sampling-clock extension
to ``forge.verify.gen_sim`` would fix this properly; that's a
FORGE-core change, out of scope for a single plugin's render script --
functional correctness does not depend on it at all (packetizer_rtl's
own toggle-in-payload novelty detection, verified exhaustively by
gen_stimulus_packetizer.py, needs none of this).

A second, related limitation (also found running this exact design):
``forge.analyze.throughput_runtime.probe.build_runtime_throughput_result``'s
``accepted_transactions``/``emitted_transactions`` approximate "a
write/read-domain cycle with full/empty deasserted is a real
accepted/emitted transaction" -- correct for a FIFO that writes
unconditionally every non-full cycle (this repo's *only* prior
async_fifo usage, design_cdc.yml's constant-held value), but this
design's own real ``cdc.write_enable_pin`` gating (see
cdc_async_fifo.v's header) means most non-full cycles are genuinely
idle, not real writes -- the generic approximation overcounts by
roughly the idle/active cycle ratio. This design's own real record
counts are already known exactly from its functional verification (see
gen_stimulus_packetizer.py's conservation-invariant check: 64
pixel-result + 1 tile-statistics records, zero dropped/duplicated) --
used directly below instead of the generic approximation for
``accepted``/``emitted``, while ``high_water_mark``/``full_events``/
``dropped_transactions`` (occupancy-derived, unaffected by this gap)
still come from the real probe CSV.

Usage::

    python3 render_throughput_result.py
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
# verification (gen_stimulus_packetizer.py's conservation-invariant
# check) -- see module docstring for why these override the generic
# probe-derived accepted/emitted approximation.
_REAL_ACCEPTED_EMITTED = {
    "xform:pr_record_out:async_fifo": 64,
    "xform:ts_record_out:async_fifo": 1,
}

_REPO_ROOT = Path(__file__).resolve().parents[5]
_HLS_BUILD_ROOT = _REPO_ROOT / "build_hls_vision_pipeline_demo"
_PROBE_CSV = Path(__file__).resolve().parents[1] / "packetizer_xsim/xsim_work/algo_top_probe.csv"
_OUT_PATH = Path(__file__).resolve().parents[1] / "packetizer_xsim/throughput_result.json"
_PROVENANCE_JSON = _REPO_ROOT / "gen-top/design_vision_pipeline_packetizer/provenance.json"

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

# Probe CSV rows are sampled on ap_clk (200MHz) -- see this module's own
# docstring for why that's the correct rate for the write-domain probes
# and an approximation for the read-domain ones.
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
