#!/usr/bin/env python3
"""vision_pipeline_demo stimulus generator for the packetizer flow.

Unlike every earlier stimulus generator in this plugin (which drive one
clock domain and check at statically-derivable cycle offsets), this
design has a *second*, genuinely independent clock domain
(``clk_output``, 125MHz) downstream of two real async_fifo CDC
crossings -- exactly the kind of clock-phase-dependent timing
gen_stimulus_cdc.py's own header already establishes has no exact-cycle
golden model (see that flow's header, and
docs/development/adr/0002-cdc-primitive-semantics.md for why). So this
generator does not try to predict which output-domain cycle each record
arrives on: it emits a SystemVerilog ``fork``/``join`` with two
concurrent processes inside ``run_stimulus()`` --

  * ``drive_proc`` (``ap_clk``): drives ``norm_px`` and ``norm_tile``
    both back-to-back (64 pixels, one per cycle each, mirroring
    gen_stimulus_pixel_result.py/gen_stimulus_tile_stats.py -- real
    II=1 on both paths, see modules.yml's tile_stats_hls entry) in the
    *same* iteration loop.
  * ``check_proc`` (``clk_output``): polls ``pktz_packet_valid`` every
    output-domain cycle: whenever asserted, decodes each occupied
    128-bit slot (``pktz_packet_keep`` says which), reads its
    ``record_kind``, and checks it against the *next* expected record of
    that kind (both FIFOs are individually order-preserving, so "the
    k-th received pixel-result record" is unconditionally dataset-order
    index k -- no matching/search needed; see
    docs/development/adr/0003-vision-packet-format.md for the packet
    layout this decode relies on). Every expected record -- 64
    pixel-result + 1 tile-statistics = 65 -- must be observed exactly
    once before a generous cycle-count timeout, or the check fails.

This verifies real conservation (every accepted record is emitted
exactly once, none dropped/duplicated) and real content correctness,
without needing to reproduce CDC-crossing timing by hand.

Usage::

    python3 gen_stimulus_packetizer.py --flow packetizer_xsim
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict

from forge.verify.dataset_adapter import DatasetSource
from forge.verify.dataset_service import DatasetService
from forge.verify.golden_model import run_golden_model, write_provider_provenance
from forge.verify.stimulus_helpers import StimulusEmitter, write_run_stimulus_svh

_DATASET_XML = Path(__file__).resolve().parents[1] / "schemas/data/vision_pipeline_quickstart_golden.xml"

_ADAPTER_ID = "vision_pipeline.pixel-stream-xml"
_PROVIDER_ID = "vision_pipeline.packetizer"

_N_PIXELS = 64
_TILE_SPACING = 1  # tile_stats_hls is real II=1

# Drive loop runs for back-to-back norm_px/norm_tile iterations (both
# paths now the same cadence).
_TOTAL_DRIVE_ITERATIONS = (_N_PIXELS - 1) * _TILE_SPACING + 1  # 64

# Generous output-domain receive timeout: worst-case drive length in
# ap_clk cycles (64 * 5ns = 320ns) plus pipeline/CDC settle margin,
# expressed in clk_output cycles (8ns) -- (320 + 200) / 8 ~= 65, generously
# padded since CDC synchronizer latency is not statically bounded.
_RECEIVE_TIMEOUT_CYCLES = 400
# No fixed warmup before check_proc starts polling: packet_valid is a real
# registered output gated by rst_output (correctly low through reset/idle,
# no spurious pulses possible before real data arrives), and an earlier
# 10-cycle warmup here was found, empirically, to silently swallow record
# 0's own real beat whenever the real pixel -> output latency turned out
# shorter than that margin -- every later record then observed as the
# *next* expected value, a clean off-by-one, not a random corruption.
# Polling from cycle 0 has no such blind spot.


def _ensure_bootstrapped() -> None:
    import bootstrap as _bootstrap_mod
    _bootstrap_mod.bootstrap()


def _load_dataset_and_golden(dataset_path: Path):
    _ensure_bootstrapped()
    service = DatasetService()
    serialized = service.load(DatasetSource(raw_path=dataset_path))
    canonical = service.materialize(DatasetSource(serialized=serialized), _ADAPTER_ID, {})
    expected = run_golden_model(_PROVIDER_ID, canonical, {})
    return canonical, expected


def generate_for_flow(
    flow_name: str, out_path: Path, *, dataset_path: Path = _DATASET_XML,
) -> None:
    canonical, expected = _load_dataset_and_golden(dataset_path)
    if len(canonical.events) != _N_PIXELS:
        raise ValueError(
            f"Expected exactly {_N_PIXELS} events (one 8x8 frame), got {len(canonical.events)}"
        )

    events: "list[Dict[str, Any]]" = list(canonical.events)
    pixel_record_hexes = [ev["expected"]["pixel_record_hex"] for ev in expected.events]
    tile_record_hex = expected.events[0]["expected"]["tile_record_hex"]

    em = StimulusEmitter()

    em.comment("Expected-record arrays --")
    em.comment("declared up front, before any statement (Xilinx xvlog requires")
    em.comment("task-local declarations to precede statements even in SV mode,")
    em.comment("stricter than the bare IEEE 1800 grammar). Both FIFOs are")
    em.comment("individually order-preserving, so the k-th received record of a")
    em.comment("given kind is unconditionally dataset-order index k.")
    em.raw(f"    reg [127:0] exp_pr [0:{_N_PIXELS - 1}];")
    em.raw("    reg [127:0] exp_ts [0:0];")
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
    em.raw(f"    exp_ts[0] = 128'h{tile_record_hex:032X};")
    em.blank()

    em.raw("    fork")
    em.raw("      begin : drive_proc")
    for it in range(_TOTAL_DRIVE_ITERATIONS):
        if it < _N_PIXELS:
            ev = events[it]["in"]
            em.raw(f"        norm_px_pixel        <= 8'h{int(ev['pixel'], 0):02X};")
            em.raw(f"        norm_px_x            <= 12'd{int(ev['x'], 0)};")
            em.raw(f"        norm_px_y            <= 12'd{int(ev['y'], 0)};")
            em.raw(f"        norm_px_frame_id     <= 16'd{int(ev['frame_id'], 0)};")
            em.raw(f"        norm_px_tile_id      <= 16'd{int(ev['tile_id'], 0)};")
            em.raw(f"        norm_px_end_of_line  <= 1'b{int(ev['end_of_line'], 0) & 1};")
            em.raw(f"        norm_px_end_of_frame <= 1'b{int(ev['end_of_frame'], 0) & 1};")
            em.raw("        norm_px_pixel_valid  <= 1'b1;")
        else:
            em.raw("        norm_px_pixel_valid  <= 1'b0;")

        if it % _TILE_SPACING == 0 and (it // _TILE_SPACING) < _N_PIXELS:
            ev = events[it // _TILE_SPACING]["in"]
            em.raw(f"        norm_tile_pixel        <= 8'h{int(ev['pixel'], 0):02X};")
            em.raw(f"        norm_tile_x            <= 12'd{int(ev['x'], 0)};")
            em.raw(f"        norm_tile_y            <= 12'd{int(ev['y'], 0)};")
            em.raw(f"        norm_tile_frame_id     <= 16'd{int(ev['frame_id'], 0)};")
            em.raw(f"        norm_tile_tile_id      <= 16'd{int(ev['tile_id'], 0)};")
            em.raw(f"        norm_tile_end_of_line  <= 1'b{int(ev['end_of_line'], 0) & 1};")
            em.raw(f"        norm_tile_end_of_frame <= 1'b{int(ev['end_of_frame'], 0) & 1};")
            em.raw("        norm_tile_pixel_valid  <= 1'b1;")
        else:
            em.raw("        norm_tile_pixel_valid  <= 1'b0;")

        em.raw("        @(posedge ap_clk);")
    em.raw("        norm_px_pixel_valid   <= 1'b0;")
    em.raw("        norm_tile_pixel_valid <= 1'b0;")
    em.raw("      end")

    em.raw("      begin : check_proc")
    em.raw("        pr_idx = 0; ts_idx = 0; total_received = 0; timeout_counter = 0;")
    em.raw(f"        while (total_received < {_N_PIXELS + 1} && timeout_counter < {_RECEIVE_TIMEOUT_CYCLES}) begin")
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
        em.raw(
            '                  $fatal(1, "Check failed: pixel_result_record mismatch");'
        )
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
        em.raw(
            '                  $fatal(1, "Check failed: tile_statistics_record mismatch");'
        )
        em.raw("                end")
        em.raw("                ts_idx = ts_idx + 1;")
        em.raw("                total_received = total_received + 1;")
        em.raw("              end else begin")
        em.raw(
            f'                $display("FAIL: reserved record_kind observed in {slot_name}");'
        )
        em.raw('                $fatal(1, "Check failed: reserved record_kind");')
        em.raw("              end")
        em.raw("            end")
    em.raw("          end")
    em.raw("        end")
    em.raw(f"        if (total_received !== {_N_PIXELS + 1}) begin")
    em.raw(
        f'          $display("FAIL: packetizer_receive_timeout — expected {_N_PIXELS + 1} records, got %0d", '
        "total_received);"
    )
    em.raw('          $fatal(1, "Check failed: packetizer_receive_timeout");')
    em.raw("        end")
    em.raw(
        '        $display("FORGE_CHECK|check_id=conservation|label=conservation_invariant'
        '|signal=total_received|expected=0x%h|observed=0x%h|width=32|passed=%0d", '
        f"32'd{_N_PIXELS + 1}, total_received, (total_received === {_N_PIXELS + 1}));"
    )
    em.raw("      end")
    em.raw("    join")

    write_run_stimulus_svh(
        em, out_path,
        header_comment=f"flow={flow_name} (dual-clock-domain fork/join drive+check, see gen_stimulus_packetizer.py)",
    )
    print(f"  wrote {out_path}")

    provenance_path = out_path.parent / "golden_model_provenance.json"
    write_provider_provenance(expected, provenance_path)
    print(f"  wrote {provenance_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="vision_pipeline_demo packetizer stimulus generator")
    ap.add_argument("--flow", required=True, help="Flow name from design.verification.yml")
    args = ap.parse_args()

    verify_root = Path(__file__).resolve().parents[1]
    flow_dir    = verify_root / args.flow
    out_path    = flow_dir / "stimulus_current.svh"
    flow_dir.mkdir(parents=True, exist_ok=True)

    generate_for_flow(args.flow, out_path)


if __name__ == "__main__":
    main()
