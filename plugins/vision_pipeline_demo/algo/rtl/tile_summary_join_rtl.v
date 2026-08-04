//==============================================================================
// tile_summary_join_rtl.v
//==============================================================================
// Slice 10.3 (release-plan Phase 10, docs/internal/phase10/preflight.md
// §6.2, spec §11's `metadata_join_rtl` role narrowed to the per-tile
// path only -- the corrected topology no longer merges this path with
// the pixel-result path before the packetizer, see preflight.md's own
// §6.2 correction): the "tagged elastic join" that combines
// tile_stats_hls's computed statistics with tile_boundary_rtl's
// independently-derived tile-end tag, producing one
// forge.tile_statistics.v1-shaped record per tile.
//
// Alignment is genuinely NOT exact-cycle here (spec §15.3: "must not be
// reported as fixed") -- tile_stats_hls's own latency is bounded, not
// fixed (modules.yml), so this module matches the two branches by
// **tile_id/frame_id**, not by cycle position: whenever tile_stats_hls
// asserts stats_valid, tile_boundary_rtl's held tag (bounded -- it stays
// valid for HOLD_CYCLES cycles, comfortably covering tile_stats_hls's
// declared max latency, see tile_boundary_rtl.v) is expected to already
// be pending with the *same* tile_id/frame_id. `join_mismatch` is the
// real diagnostic for whenever that expectation doesn't hold
// (dedicated-diagnostic-per-failure-mode, preflight.md §5's own
// pattern) -- no merged record is asserted for that cycle, matching
// edge_mask_merge_rtl's tag_mismatch precedent exactly, just against
// bounded alignment instead of exact-cycle.
//
// Purely combinational read of tile_boundary_rtl's held tag (no ack
// handshake back to it -- see tile_boundary_rtl.v for why); this
// module's own contribution is a plain 1-cycle registered
// match-and-forward step. Its own declared modules.yml latency is
// still `kind: elastic` (spec §15.3's literal wording), since the
// join's real end-to-end completion time is dominated by whichever of
// its two predecessors is slower for a given tile, not this module's
// own 1-cycle stage.
//==============================================================================

`timescale 1ns / 1ps

module tile_summary_join_rtl #(
    parameter STAT_WIDTH = 8,
    parameter MEAN_WIDTH = 16,
    parameter VAR_WIDTH  = 24
)(
    input  wire                     ap_clk,
    input  wire                     ap_rst,

    // Statistics branch (tile_stats_hls) -- bounded latency.
    input  wire [STAT_WIDTH-1:0]    stats_minimum,
    input  wire [STAT_WIDTH-1:0]    stats_maximum,
    input  wire [MEAN_WIDTH-1:0]    stats_mean,
    input  wire [VAR_WIDTH-1:0]     stats_variance,
    input  wire [15:0]              stats_tile_id,
    input  wire [15:0]              stats_frame_id,
    input  wire                     stats_valid,

    // Tag branch (tile_boundary_rtl) -- bounded (held for HOLD_CYCLES).
    input  wire                     tag_valid,
    input  wire [15:0]              tag_tile_id,
    input  wire [15:0]              tag_frame_id,
    input  wire                     tag_end_of_frame,

    // forge.tile_statistics.v1 (preflight.md §6.1)
    output reg  [15:0]              out_tile_id,
    output reg  [15:0]              out_frame_id,
    output reg  [STAT_WIDTH-1:0]    out_minimum,
    output reg  [STAT_WIDTH-1:0]    out_maximum,
    output reg  [MEAN_WIDTH-1:0]    out_mean,
    output reg  [VAR_WIDTH-1:0]     out_variance,
    output reg                      out_end_of_frame,
    output reg                      out_valid,

    output reg                      join_mismatch
);

    wire tags_match = tag_valid &&
                       (tag_tile_id  == stats_tile_id) &&
                       (tag_frame_id == stats_frame_id);
    wire match      = stats_valid && tags_match;

    always @(posedge ap_clk) begin
        if (ap_rst) begin
            out_tile_id      <= 16'b0;
            out_frame_id     <= 16'b0;
            out_minimum      <= {STAT_WIDTH{1'b0}};
            out_maximum      <= {STAT_WIDTH{1'b0}};
            out_mean         <= {MEAN_WIDTH{1'b0}};
            out_variance     <= {VAR_WIDTH{1'b0}};
            out_end_of_frame <= 1'b0;
            out_valid        <= 1'b0;
            join_mismatch    <= 1'b0;
        end else begin
            out_tile_id      <= stats_tile_id;
            out_frame_id     <= stats_frame_id;
            out_minimum      <= stats_minimum;
            out_maximum      <= stats_maximum;
            out_mean         <= stats_mean;
            out_variance     <= stats_variance;
            out_end_of_frame <= tag_end_of_frame;
            out_valid        <= match;
            join_mismatch    <= stats_valid && !tags_match;
        end
    end

endmodule
