//==============================================================================
// tile_boundary_rtl.v
//==============================================================================
// Slice 10.3 (release-plan Phase 10, docs/internal/phase10/preflight.md
// §6.2): the "tile-end summary" half of the per-tile path's tagged
// elastic join. Watches the same normalized-pixel-tagged stream
// tile_stats_hls accumulates (a fan-out sibling of it, both driven
// directly from pixel_normalizer -- not downstream of tile_stats_hls),
// and independently re-derives "this is the last sample of its tile"
// from the same frozen 8x8/row-major geometry tile_stats_hls.h's header
// documents.
//
// This exists as an INDEPENDENT re-derivation (not a tap off
// tile_stats_hls's own tile_id_out/frame_id_out) on purpose -- the same
// "verify the two branches actually agree at runtime" role
// edge_mask_merge_rtl's tag_mismatch already established for the
// pixel-result path (slice 10.2): if tile_stats_hls's accumulator ever
// drifted out of sync with the real tile boundary (e.g. a miscounted
// sample), tile_summary_join_rtl's join_mismatch is what would catch
// it -- a self-tapped tag could never detect that class of bug.
//
// On a tile-end event, {tile_id, frame_id, end_of_frame} latches into a
// HELD register for HOLD_CYCLES cycles -- held, not just pulsed for one
// cycle, because tile_summary_join_rtl's matching tile_stats_hls output
// can legitimately arrive several cycles later (tile_stats_hls's own
// latency is bounded [6,10], not fixed -- modules.yml). HOLD_CYCLES=16
// comfortably covers that bound with margin, matching the kind of
// safety margin design.verification.yml's own
// post_stimulus_drain_cycles comments already use elsewhere. No ack
// handshake back from the join: closing that loop would require a
// combinational-cycle-free but still genuinely cyclic connection
// between two module instances, which forge.topgen's design-graph
// tooling has no precedent for anywhere in this repo -- a bounded,
// self-timing hold (declared `latency: {kind: bounded, min_cycles: 1,
// max_cycles: HOLD_CYCLES+1}` in modules.yml) gets the same correctness
// property (the tag is provably still available when tile_stats_hls's
// own bounded-range output arrives) without it.
//==============================================================================

`timescale 1ns / 1ps

module tile_boundary_rtl #(
    parameter TILE_WIDTH  = 8,
    parameter TILE_HEIGHT = 8,
    parameter HOLD_CYCLES = 16
)(
    input  wire        ap_clk,
    input  wire        ap_rst,

    input  wire [11:0] x,
    input  wire [11:0] y,
    input  wire [15:0] frame_id,
    input  wire [15:0] tile_id,
    input  wire        end_of_line,
    input  wire        end_of_frame,
    input  wire        in_valid,

    // Held tag output -- stays valid for HOLD_CYCLES cycles after
    // latching (the bounded side of the join), not a single-cycle
    // pulse.
    output reg          tag_valid,
    output reg  [15:0]  tag_tile_id,
    output reg  [15:0]  tag_frame_id,
    output reg          tag_end_of_frame
);

    localparam HOLD_W = $clog2(HOLD_CYCLES + 1);

    wire tile_last = in_valid &&
                      ((y % TILE_HEIGHT) == (TILE_HEIGHT - 1)) &&
                      ((x % TILE_WIDTH)  == (TILE_WIDTH  - 1));

    reg [HOLD_W-1:0] hold_count;

    always @(posedge ap_clk) begin
        if (ap_rst) begin
            tag_valid        <= 1'b0;
            tag_tile_id      <= 16'b0;
            tag_frame_id     <= 16'b0;
            tag_end_of_frame <= 1'b0;
            hold_count       <= {HOLD_W{1'b0}};
        end else if (tile_last) begin
            // A fresh boundary always (re)starts the hold window --
            // correct at this slice's single-tile-in-flight test scale,
            // where the previous tag's hold window has always already
            // expired by the time the next one arrives.
            tag_valid        <= 1'b1;
            tag_tile_id      <= tile_id;
            tag_frame_id     <= frame_id;
            tag_end_of_frame <= end_of_frame;
            hold_count       <= HOLD_CYCLES[HOLD_W-1:0];
        end else if (tag_valid) begin
            if (hold_count == {HOLD_W{1'b0}}) begin
                tag_valid <= 1'b0;
            end else begin
                hold_count <= hold_count - 1'b1;
            end
        end
    end

endmodule
