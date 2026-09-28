#!/usr/bin/env python3
"""vision_pipeline_demo stimulus generator for the multi-domain CDC path.

Unlike every other gen_stimulus_*.py in this plugin, this flow has no
per-event pixel/tile dataset to drive -- design_cdc.yml exercises five
CDC-crossing kinds over a handful of discrete control-plane values, not
a streamed dataset. Forcing an XML dataset + GoldenModelProvider onto
that shape would be an artificial fit (the "expected" values here are
directly-computable constants, not per-event algorithm output), so this
script drives fixed constants directly and computes expected values
inline -- a deliberate, explained departure from the
gen_stimulus_pixel_result.py / gen_stimulus_tile_stats.py precedent,
not an oversight.

Every driven config value (enable/threshold/kernel_mode/frame_limit/
error/result_data) is held CONSTANT for the whole run: cdc_mailbox and
cdc_async_fifo (framework support RTL, forge/rtl/support/ -- see their
own header comments) have no producer-side backpressure/valid concept,
so their real completion timing is data-dependent and not statically
bounded. Holding values constant sidesteps needing to model that
timing precisely (this flow proves each crossing kind works at all, not
sustained throughput/backpressure -- see check_throughput_sustainability.py
and check_fifo_capacity.py for that), while apply_in/frame_done_pulse_in
are driven as genuine single-cycle pulses (pulse_sync's whole point) with
a comfortable settle window around them.

frame_done_pulse_in lives in the `output` domain (clk_output) -- driven
and paced with StimulusEmitter.tick(clock="clk_output"), not the default
ap_clk, to actually exercise cross-domain-paced stimulus, not just
because the period math would also tolerate ap_clk-paced driving here.
See docs/development/adr/0002-cdc-primitive-semantics.md for the
continuously-driven, self-describing-payload convention every crossing
in this plugin follows.

Usage::

    python3 gen_stimulus_cdc.py --flow cdc_xsim
"""
from __future__ import annotations

import argparse
from pathlib import Path

from forge.verification.stimulus_helpers import StimulusEmitter, write_run_stimulus_svh

# Constant config values, driven from just after reset and held for the
# whole run.
_ENABLE = 1
_THRESHOLD = 0xAB
_KERNEL_MODE = 0b10
_FRAME_LIMIT = 0xCAFE
_MAILBOX_BUS = (_THRESHOLD << 18) | (_KERNEL_MODE << 16) | _FRAME_LIMIT  # {thr[7:0], km[1:0], fl[15:0]}
_RESULT_DATA = 0x3ABCDEF  # 26 bits, well under the field's 0x3FFFFFF max
_ERROR_LEVEL = 1

# Comfortable settle windows (ap_clk cycles), generous relative to every
# real primitive's own declared/measured latency: cdc_sync2ff is a fixed
# 2 destination-domain cycles, cdc_pulse_sync a fixed 3, cdc_reset_sync's
# own deassert is 2 destination-domain cycles after ap_rst releases --
# all comfortably under one ap_clk (20ns) period's worth of the fastest
# domain (clk_pixel, 5ns) or slowest (clk_output, 8ns) cycles many times
# over. cdc_mailbox/cdc_async_fifo are data-dependent/unbounded in
# general, but with din held constant they settle as soon as the first
# real transfer completes -- 20 ap_clk cycles (400ns) is a wide margin.
_SETTLE_CYCLES = 20


def generate_for_flow(flow_name: str, out_path: Path) -> None:
    em = StimulusEmitter()

    with em.event_block("reset"):
        em.drive("ap_rst", 1, width=1)
        em.tick(cycles=4)
        em.drive("ap_rst", 0, width=1)
        em.tick()

    with em.event_block("drive_constants"):
        em.drive("ctrl_level_enable_in", _ENABLE, width=1)
        em.drive("ctrl_mailbox_threshold_in", _THRESHOLD, width=8)
        em.drive("ctrl_mailbox_kernel_mode_in", _KERNEL_MODE, width=2)
        em.drive("ctrl_mailbox_frame_limit_in", _FRAME_LIMIT, width=16)
        em.drive("pxsink_result_data_in", _RESULT_DATA, width=26)
        em.drive("outsink_level_error_in", _ERROR_LEVEL, width=1)
        em.tick(cycles=_SETTLE_CYCLES)

    with em.event_block("pulse_apply"):
        em.drive("ctrl_pulse_apply_in", 1, width=1)
        em.tick()
        em.drive("ctrl_pulse_apply_in", 0, width=1)
        em.tick(cycles=_SETTLE_CYCLES)

    with em.event_block("pulse_frame_done"):
        # Paced against the output domain's own clock via
        # StimulusEmitter.tick(clock="clk_output") -- frame_done_pulse_in
        # is sampled by outsink_pulse_rtl's always @(posedge clk_output)
        # block.
        em.drive("outsink_pulse_frame_done_pulse_in", 1, width=1)
        em.tick(clock="clk_output")
        em.drive("outsink_pulse_frame_done_pulse_in", 0, width=1)
        em.tick(cycles=_SETTLE_CYCLES)

    with em.event_block("checks"):
        em.check(
            "ctrl_level_error_level_status", _ERROR_LEVEL, width=1,
            label="error_level_status_check",
        )
        em.check(
            "ctrl_pulse_frame_done_count", 1, width=8,
            label="frame_done_count_check",
        )
        em.check(
            "pxsink_enable_status", _ENABLE, width=1,
            label="enable_status_check",
        )
        em.check(
            "pxsink_apply_count", 1, width=8,
            label="apply_count_check",
        )
        em.check(
            "pxsink_mailbox_status", _MAILBOX_BUS, width=26,
            label="mailbox_status_check",
        )
        em.check(
            "outsink_level_result_status", _RESULT_DATA, width=26,
            label="result_status_check",
        )

    write_run_stimulus_svh(em, out_path, header_comment=f"flow={flow_name} (multi-domain CDC)")
    print(f"  wrote {out_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="vision_pipeline_demo multi-domain CDC stimulus generator")
    ap.add_argument("--flow", required=True, help="Flow name from design.verification.yml")
    args = ap.parse_args()

    verify_root = Path(__file__).resolve().parents[1]
    flow_dir    = verify_root / args.flow
    out_path    = flow_dir / "stimulus_current.svh"
    flow_dir.mkdir(parents=True, exist_ok=True)

    generate_for_flow(args.flow, out_path)


if __name__ == "__main__":
    main()
