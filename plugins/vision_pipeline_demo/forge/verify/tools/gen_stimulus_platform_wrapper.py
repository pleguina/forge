#!/usr/bin/env python3
"""vision_pipeline_demo stimulus generator for the platform-wrapper flow.

Three genuinely independent clock domains this time (control/pixel/
output, not just pixel/output like packetizer_xsim/full_functional_xsim)
-- ``run_stimulus()`` forks THREE concurrent processes:

  * ``ctrl_proc`` (real time, not clock-edge-paced -- the control-domain
    mailbox source, ctrl_mailbox_platform_rtl.v, samples its input bus
    continuously every ``clk_control`` cycle with no valid/strobe
    concept, so this process only needs to hold a value, not drive a
    per-cycle loop): drives ``threshold_in=T0`` immediately, then
    switches to ``threshold_in=T1`` at the exact real-time instant
    ``drive_proc`` finishes streaming frame 0 -- giving T1 the full
    inter-frame gap to settle (via the real cdc: {kind: mailbox_transfer}
    crossing) before frame 1's own first pixel, and proving
    threshold_configurable_rtl.v's frame-boundary-gated latch really
    does apply a config change starting at the NEXT frame, never
    retroactively affecting the frame already in flight -- a mid-frame
    threshold change would otherwise produce an ambiguous, half-old-
    half-new record within the same frame.
  * ``drive_proc`` (``ap_clk``): waits a generous settle margin for T0,
    streams frame 0 (64 pixels, real II=1 back-to-back), idles for a
    generous inter-frame gap (>> window_builder_rtl's own required
    FRAME_WIDTH+2 blanking interval), streams frame 1.
  * ``check_proc`` (``clk_output``): identical decode/check strategy to
    gen_stimulus_full_functional.py's own -- polls ``pktz_packet_valid``,
    decodes each occupied 128-bit slot by ``record_kind``, checks
    against the *next* expected record of that kind (both FIFOs are
    individually order-preserving). PlatformWrapperProvider computes
    each event's own expected ``threshold_mask`` from the real
    ``frame_thresholds`` schedule below, so this content check IS the
    proof that a real mailbox-written threshold change actually reaches
    the pipeline's output -- not a separate, weaker "did it compile"
    check.

Usage::

    python3 gen_stimulus_platform_wrapper.py --flow platform_wrapper_xsim
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict

from forge.verify.dataset_adapter import DatasetSource
from forge.verify.dataset_service import DatasetService
from forge.verify.golden_model import run_golden_model, write_provider_provenance
from forge.verify.stimulus_helpers import StimulusEmitter, write_run_stimulus_svh

_DATASET_XML = (
    Path(__file__).resolve().parents[1] / "schemas/data/vision_pipeline_platform_wrapper_golden.xml"
)

_ADAPTER_ID = "vision_pipeline.pixel-stream-xml"
_PROVIDER_ID = "vision_pipeline.platform_wrapper"

_FRAME_WIDTH = 8
_FRAME_HEIGHT = 8
_N_FRAMES = 2
_N_PIXELS_PER_FRAME = _FRAME_WIDTH * _FRAME_HEIGHT   # 64
_N_PIXELS = _N_FRAMES * _N_PIXELS_PER_FRAME          # 128
_N_TILES = _N_FRAMES                                 # one 8x8 tile per frame

# Real, genuinely different per-frame thresholds -- neither equals
# DEFAULT_THRESHOLD (96), so a passing check is real proof of
# reconfiguration, not a coincidental match with the compile-time
# default threshold_rtl.v itself would have used.
_FRAME_THRESHOLDS = {0: 64, 1: 160}

# Every wait below is a plain per-clock cycle count (`repeat(N)
# @(posedge <that process's own clock>)`), the same idiom every other
# gen_stimulus_*.py script in this plugin already uses -- NOT a
# real-time `#delay` (an earlier version of this script used `#delay`
# for ctrl_proc/drive_proc's cross-domain coordination and hit a real,
# reproducible one-cycle-loss bug: pixel(0,0)'s own record never
# reached the output, every downstream tag arriving one pixel "ahead"
# -- found by a hierarchical debug $display trace (dut.threshcfg.*)
# showing frame_start's own x=0,y=0 sample was silently skipped,
# consistent with a same-time-step ordering ambiguity between a
# real-time delay's wakeup and a concurrent `@(posedge ap_clk)` in a
# SEPARATE process -- something no earlier gen_stimulus script in this
# plugin exercised, since none needed a third, real-time-only-paced
# process before). Real elapsed time between the two clocks is still
# reconciled -- just by converting the desired real-time offsets to
# each process's own clock's cycle count up front (below), not by
# mixing `#delay` and `@(posedge clk)` in the same process.
_AP_CLK_PERIOD_NS = 5.0
_CLK_CONTROL_PERIOD_NS = 20.0
_SETTLE_INITIAL_NS = 1000.0   # T0 settle time before frame 0 starts
_FRAME_DURATION_NS = _N_PIXELS_PER_FRAME * _AP_CLK_PERIOD_NS   # 320.0
_INTER_FRAME_GAP_NS = 300.0  # >> window_builder_rtl's own FRAME_WIDTH+2=10-cycle (50ns) requirement

_SETTLE_INITIAL_AP_CLK_CYCLES = int(_SETTLE_INITIAL_NS / _AP_CLK_PERIOD_NS)          # 200
_INTER_FRAME_GAP_AP_CLK_CYCLES = int(_INTER_FRAME_GAP_NS / _AP_CLK_PERIOD_NS)        # 60
# ctrl_proc switches to T1 the instant drive_proc finishes frame 0 --
# settle + one full frame's worth of ap_clk cycles, expressed in
# clk_control's own cycles (both ratios are exact by construction:
# every _NS constant above is a multiple of both clock periods).
_CTRL_SWITCH_CLK_CONTROL_CYCLES = int(
    (_SETTLE_INITIAL_NS + _FRAME_DURATION_NS) / _CLK_CONTROL_PERIOD_NS
)  # 66

_RECEIVE_TIMEOUT_CYCLES = 1500  # clk_output cycles (8ns each = 12000ns, comfortably > settle+2 frames+gap)


def _ensure_bootstrapped() -> None:
    import bootstrap as _bootstrap_mod
    _bootstrap_mod.bootstrap()


def _load_dataset_and_golden(dataset_path: Path):
    _ensure_bootstrapped()
    service = DatasetService()
    serialized = service.load(DatasetSource(raw_path=dataset_path))
    canonical = service.materialize(DatasetSource(serialized=serialized), _ADAPTER_ID, {})
    expected = run_golden_model(_PROVIDER_ID, canonical, {"frame_thresholds": _FRAME_THRESHOLDS})
    return canonical, expected


def _is_tile_last(x: int, y: int) -> bool:
    return (x % _FRAME_WIDTH == _FRAME_WIDTH - 1) and (y % _FRAME_HEIGHT == _FRAME_HEIGHT - 1)


def generate_for_flow(
    flow_name: str, out_path: Path, *, dataset_path: Path = _DATASET_XML,
) -> None:
    canonical, expected = _load_dataset_and_golden(dataset_path)
    if len(canonical.events) != _N_PIXELS:
        raise ValueError(
            f"Expected exactly {_N_PIXELS} events ({_N_FRAMES} {_FRAME_WIDTH}x{_FRAME_HEIGHT} "
            f"frames), got {len(canonical.events)}"
        )

    events: "list[Dict[str, Any]]" = list(canonical.events)
    pixel_record_hexes = [ev["expected"]["pixel_record_hex"] for ev in expected.events]

    tile_record_hexes: "list[int]" = []
    for ev, exp in zip(events, expected.events):
        x = int(ev["in"]["x"], 0)
        y = int(ev["in"]["y"], 0)
        if _is_tile_last(x, y):
            tile_record_hexes.append(exp["expected"]["tile_record_hex"])
    if len(tile_record_hexes) != _N_TILES:
        raise ValueError(
            f"Expected exactly {_N_TILES} tile-completion events, found {len(tile_record_hexes)}"
        )

    em = StimulusEmitter()

    em.comment("Expected-record arrays --")
    em.comment("declared up front, before any statement (Xilinx xvlog requires")
    em.comment("task-local declarations to precede statements even in SV mode).")
    em.raw(f"    reg [127:0] exp_pr [0:{_N_PIXELS - 1}];")
    em.raw(f"    reg [127:0] exp_ts [0:{_N_TILES - 1}];")
    em.raw("    integer pr_idx, ts_idx, total_received, timeout_counter;")
    em.blank()

    with em.event_block("reset"):
        em.drive("ap_rst", 1, width=1)
        em.tick(cycles=4)
        em.drive("ap_rst", 0, width=1)
        em.tick()

    em.blank()
    for i, rec_hex in enumerate(pixel_record_hexes):
        em.raw(f"    exp_pr[{i}] = 128'h{rec_hex:032X};")
    for i, rec_hex in enumerate(tile_record_hexes):
        em.raw(f"    exp_ts[{i}] = 128'h{rec_hex:032X};")
    em.blank()

    em.raw("    fork")

    # ── ctrl_proc: clk_control-cycle-paced ───────────────────────────────
    em.raw("      begin : ctrl_proc")
    t0 = _FRAME_THRESHOLDS[0]
    t1 = _FRAME_THRESHOLDS[1]
    em.raw(f"        ctrl_mailbox_threshold_in    <= 8'd{t0};")
    em.raw("        ctrl_mailbox_kernel_mode_in  <= 2'd0;")
    em.raw("        ctrl_mailbox_frame_limit_in  <= 16'd0;")
    em.raw(f"        repeat ({_CTRL_SWITCH_CLK_CONTROL_CYCLES}) @(posedge clk_control);")
    em.raw(f"        ctrl_mailbox_threshold_in    <= 8'd{t1};")
    em.raw("      end")

    # ── drive_proc: ap_clk-cycle-paced, two frames with a real inter-frame gap ──
    em.raw("      begin : drive_proc")
    em.raw(f"        repeat ({_SETTLE_INITIAL_AP_CLK_CYCLES}) @(posedge ap_clk);")
    for frame_idx in range(_N_FRAMES):
        base = frame_idx * _N_PIXELS_PER_FRAME
        for it in range(_N_PIXELS_PER_FRAME):
            ev = events[base + it]["in"]
            em.raw(f"        norm_pixel        <= 8'h{int(ev['pixel'], 0):02X};")
            em.raw(f"        norm_x            <= 12'd{int(ev['x'], 0)};")
            em.raw(f"        norm_y            <= 12'd{int(ev['y'], 0)};")
            em.raw(f"        norm_frame_id     <= 16'd{int(ev['frame_id'], 0)};")
            em.raw(f"        norm_tile_id      <= 16'd{int(ev['tile_id'], 0)};")
            em.raw(f"        norm_end_of_line  <= 1'b{int(ev['end_of_line'], 0) & 1};")
            em.raw(f"        norm_end_of_frame <= 1'b{int(ev['end_of_frame'], 0) & 1};")
            em.raw("        norm_pixel_valid  <= 1'b1;")
            em.raw("        @(posedge ap_clk);")
        em.raw("        norm_pixel_valid <= 1'b0;")
        if frame_idx < _N_FRAMES - 1:
            em.raw(f"        repeat ({_INTER_FRAME_GAP_AP_CLK_CYCLES}) @(posedge ap_clk);")
    em.raw("      end")

    # ── check_proc: clk_output, identical decode/check shape to ─────────
    #    gen_stimulus_full_functional.py's own.
    em.raw("      begin : check_proc")
    em.raw("        pr_idx = 0; ts_idx = 0; total_received = 0; timeout_counter = 0;")
    em.raw(f"        while (total_received < {_N_PIXELS + _N_TILES} && timeout_counter < {_RECEIVE_TIMEOUT_CYCLES}) begin")
    em.raw("          @(posedge clk_output);")
    em.raw("          timeout_counter = timeout_counter + 1;")
    em.raw("          if (pktz_packet_valid) begin")
    for slot_name, kind_bits, keep_bits, data_range in (
        ("pktz_packet_data[127:0]", "pktz_packet_data[127:126]", "pktz_packet_keep[15:0]", "low"),
        ("pktz_packet_data[255:128]", "pktz_packet_data[255:254]", "pktz_packet_keep[31:16]", "high"),
    ):
        em.raw(f"            if ({keep_bits} !== 16'h0000) begin")
        em.raw(f"              if ({kind_bits} === 2'd0) begin")
        em.raw(
            f'                $display("FORGE_CHECK|check_id=pixel_result:%0d|label=pixel_result_{data_range}'
            f'|signal={slot_name}|expected=0x%h|observed=0x%h|width=128|passed=%0d", '
            f"pr_idx, exp_pr[pr_idx], {slot_name}, ({slot_name} === exp_pr[pr_idx]));"
        )
        em.raw(f"                if ({slot_name} !== exp_pr[pr_idx]) begin")
        em.raw(
            f'                  $display("FAIL: pixel_result_record[%0d] ({data_range}) — expected %h, got %h", '
            f"pr_idx, exp_pr[pr_idx], {slot_name});"
        )
        em.raw('                  $fatal(1, "Check failed: pixel_result_record mismatch");')
        em.raw("                end")
        em.raw("                pr_idx = pr_idx + 1;")
        em.raw("                total_received = total_received + 1;")
        em.raw(f"              end else if ({kind_bits} === 2'd1) begin")
        em.raw(
            f'                $display("FORGE_CHECK|check_id=tile_statistics:%0d|label=tile_statistics_{data_range}'
            f'|signal={slot_name}|expected=0x%h|observed=0x%h|width=128|passed=%0d", '
            f"ts_idx, exp_ts[ts_idx], {slot_name}, ({slot_name} === exp_ts[ts_idx]));"
        )
        em.raw(f"                if ({slot_name} !== exp_ts[ts_idx]) begin")
        em.raw(
            f'                  $display("FAIL: tile_statistics_record[%0d] ({data_range}) — expected %h, got %h", '
            f"ts_idx, exp_ts[ts_idx], {slot_name});"
        )
        em.raw('                  $fatal(1, "Check failed: tile_statistics_record mismatch");')
        em.raw("                end")
        em.raw("                ts_idx = ts_idx + 1;")
        em.raw("                total_received = total_received + 1;")
        em.raw("              end else begin")
        em.raw(f'                $display("FAIL: reserved record_kind observed in {slot_name}");')
        em.raw('                $fatal(1, "Check failed: reserved record_kind");')
        em.raw("              end")
        em.raw("            end")
    em.raw("          end")
    em.raw("        end")
    em.raw(f"        if (total_received !== {_N_PIXELS + _N_TILES}) begin")
    em.raw(
        f'          $display("FAIL: platform_wrapper_receive_timeout — expected {_N_PIXELS + _N_TILES} records, got %0d", '
        "total_received);"
    )
    em.raw('          $fatal(1, "Check failed: platform_wrapper_receive_timeout");')
    em.raw("        end")
    em.raw(
        '        $display("FORGE_CHECK|check_id=conservation|label=conservation_invariant'
        '|signal=total_received|expected=0x%h|observed=0x%h|width=32|passed=%0d", '
        f"32'd{_N_PIXELS + _N_TILES}, total_received, (total_received === {_N_PIXELS + _N_TILES}));"
    )
    # Real status-interface exercise: merge_tag_mismatch/tjoin_join_mismatch
    # are real top-level status ports here -- confirm they read 0 throughout
    # a real, passing run, the same runtime diagnostic role tag_mismatch/
    # join_mismatch already play internally in every other design here.
    em.raw(
        '        $display("FORGE_CHECK|check_id=status_tag_mismatch|label=status_tag_mismatch'
        '|signal=merge_tag_mismatch|expected=0x0|observed=0x%h|width=1|passed=%0d", '
        "merge_tag_mismatch, (merge_tag_mismatch === 1'b0));"
    )
    em.raw("        if (merge_tag_mismatch !== 1'b0) begin")
    em.raw('          $display("FAIL: merge_tag_mismatch asserted during a real passing run");')
    em.raw('          $fatal(1, "Check failed: status_tag_mismatch");')
    em.raw("        end")
    em.raw(
        '        $display("FORGE_CHECK|check_id=status_join_mismatch|label=status_join_mismatch'
        '|signal=tjoin_join_mismatch|expected=0x0|observed=0x%h|width=1|passed=%0d", '
        "tjoin_join_mismatch, (tjoin_join_mismatch === 1'b0));"
    )
    em.raw("        if (tjoin_join_mismatch !== 1'b0) begin")
    em.raw('          $display("FAIL: tjoin_join_mismatch asserted during a real passing run");')
    em.raw('          $fatal(1, "Check failed: status_join_mismatch");')
    em.raw("        end")
    em.raw("      end")

    em.raw("    join")

    write_run_stimulus_svh(
        em, out_path,
        header_comment=f"flow={flow_name} (three-clock-domain fork/join drive+check, see gen_stimulus_platform_wrapper.py)",
    )
    print(f"  wrote {out_path}")

    provenance_path = out_path.parent / "golden_model_provenance.json"
    write_provider_provenance(expected, provenance_path)
    print(f"  wrote {provenance_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="vision_pipeline_demo platform-wrapper stimulus generator")
    ap.add_argument("--flow", required=True, help="Flow name from design.verification.yml")
    args = ap.parse_args()

    verify_root = Path(__file__).resolve().parents[1]
    flow_dir    = verify_root / args.flow
    out_path    = flow_dir / "stimulus_current.svh"
    flow_dir.mkdir(parents=True, exist_ok=True)

    generate_for_flow(args.flow, out_path)


if __name__ == "__main__":
    main()
