#!/usr/bin/env python3
"""vision_pipeline_demo stimulus generator for slice 10.7A's
full-functional flow (release-plan Phase 10, preflight.md §10/§11).

Mirrors gen_stimulus_packetizer.py's dual-clock-domain fork/join
drive+check strategy (this design has the same genuinely independent
``clk_output`` domain downstream of two real async_fifo CDC crossings,
so exact-cycle prediction is still not attempted -- see that script's
own header). Two real differences from the 10.5 packetizer flow:

  * ``drive_proc`` (``ap_clk``) drives a SINGLE shared ``norm`` instance
    (design_full_functional.yml unifies design_packetizer.yml's own
    norm_px/norm_tile split into the spec's real single-source fan-out),
    not two.
  * The dataset is a real 16x16/2x2-tile frame (256 pixels), so
    ``check_proc`` must expect 256 pixel-result records AND 4
    tile-statistics records (not 1) -- the expected tile-record list is
    built by walking the dataset in order and taking this event's own
    ``tile_record_hex`` (from PacketizerProvider, slice 10.7A) whenever
    it is a real tile-completion event (``x % TILE_WIDTH == TILE_WIDTH-1
    and y % TILE_HEIGHT == TILE_HEIGHT-1``, tile_stats_hls.h's own frozen
    completion test) -- this reproduces the real hardware's own emission
    order (tile (0,0) at pixel 119, (0,1) at 127, (1,0) at 247, (1,1) at
    255 for this dataset) without needing any new golden-model API.

Usage::

    python3 gen_stimulus_full_functional.py --flow full_functional_xsim
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
    Path(__file__).resolve().parents[1] / "schemas/data/vision_pipeline_full_functional_golden.xml"
)

_ADAPTER_ID = "vision_pipeline.pixel-stream-xml"
_PROVIDER_ID = "vision_pipeline.packetizer"

_FRAME_WIDTH = 16
_FRAME_HEIGHT = 16
_TILE_WIDTH = 8
_TILE_HEIGHT = 8
_N_PIXELS = _FRAME_WIDTH * _FRAME_HEIGHT       # 256
_N_TILES = (_FRAME_WIDTH // _TILE_WIDTH) * (_FRAME_HEIGHT // _TILE_HEIGHT)  # 4

# Both paths are real II=1 (tile_stats_hls's slice 10.5 follow-up
# correction, unaffected by slice 10.7A's concurrent-tile-column
# extension -- re-confirmed via a real csynth run, see
# tile_stats_hls.cpp's header) -- drive back-to-back, one pixel/cycle.
_TOTAL_DRIVE_ITERATIONS = _N_PIXELS

# Generous output-domain receive timeout: worst-case drive length in
# ap_clk cycles (256 * 5ns = 1280ns) plus pipeline/CDC settle margin,
# expressed in clk_output cycles (8ns) -- (1280 + 400) / 8 ~= 210,
# generously padded since CDC synchronizer latency is not statically
# bounded (same reasoning as gen_stimulus_packetizer.py's own timeout).
_RECEIVE_TIMEOUT_CYCLES = 900


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


def _is_tile_last(x: int, y: int) -> bool:
    """tile_stats_hls.h's own frozen per-tile-cell completion test."""
    return (x % _TILE_WIDTH == _TILE_WIDTH - 1) and (y % _TILE_HEIGHT == _TILE_HEIGHT - 1)


def generate_for_flow(
    flow_name: str, out_path: Path, *, dataset_path: Path = _DATASET_XML,
) -> None:
    canonical, expected = _load_dataset_and_golden(dataset_path)
    if len(canonical.events) != _N_PIXELS:
        raise ValueError(
            f"Expected exactly {_N_PIXELS} events (one {_FRAME_WIDTH}x{_FRAME_HEIGHT} frame), "
            f"got {len(canonical.events)}"
        )

    events: "list[Dict[str, Any]]" = list(canonical.events)
    pixel_record_hexes = [ev["expected"]["pixel_record_hex"] for ev in expected.events]

    # Real hardware emission order: this event's own tile_record_hex,
    # taken only on that tile's own completion event.
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

    em.comment("Expected-record arrays (release-plan Phase 10, slice 10.7A) --")
    em.comment("declared up front, before any statement (Xilinx xvlog requires")
    em.comment("task-local declarations to precede statements even in SV mode,")
    em.comment("stricter than the bare IEEE 1800 grammar). Both FIFOs are")
    em.comment("individually order-preserving, so the k-th received record of a")
    em.comment("given kind is unconditionally dataset-order index k.")
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
    em.raw("      begin : drive_proc")
    for it in range(_TOTAL_DRIVE_ITERATIONS):
        ev = events[it]["in"]
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
    em.raw("      end")

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
    em.raw(f"        if (total_received !== {_N_PIXELS + _N_TILES}) begin")
    em.raw(
        f'          $display("FAIL: full_functional_receive_timeout — expected {_N_PIXELS + _N_TILES} records, got %0d", '
        "total_received);"
    )
    em.raw('          $fatal(1, "Check failed: full_functional_receive_timeout");')
    em.raw("        end")
    em.raw(
        '        $display("FORGE_CHECK|check_id=conservation|label=conservation_invariant'
        '|signal=total_received|expected=0x%h|observed=0x%h|width=32|passed=%0d", '
        f"32'd{_N_PIXELS + _N_TILES}, total_received, (total_received === {_N_PIXELS + _N_TILES}));"
    )
    em.raw("      end")
    em.raw("    join")

    write_run_stimulus_svh(
        em, out_path,
        header_comment=f"flow={flow_name} (dual-clock-domain fork/join drive+check, see gen_stimulus_full_functional.py)",
    )
    print(f"  wrote {out_path}")

    provenance_path = out_path.parent / "golden_model_provenance.json"
    write_provider_provenance(expected, provenance_path)
    print(f"  wrote {provenance_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="vision_pipeline_demo full-functional stimulus generator")
    ap.add_argument("--flow", required=True, help="Flow name from design.verification.yml")
    args = ap.parse_args()

    verify_root = Path(__file__).resolve().parents[1]
    flow_dir    = verify_root / args.flow
    out_path    = flow_dir / "stimulus_current.svh"
    flow_dir.mkdir(parents=True, exist_ok=True)

    generate_for_flow(args.flow, out_path)


if __name__ == "__main__":
    main()
