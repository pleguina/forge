//==============================================================================
// edge_mask_merge_rtl.v
//==============================================================================
// Merges the Sobel branch (window_builder_rtl -> sobel_hls) and
// the threshold branch (threshold_rtl -> a Connection.delay_cycles
// alignment delay, see plugins/vision_pipeline_demo/forge/designs/
// design.yml) into one forge.edge_mask_stream.v1-shaped pixel-result
// record.
//
// This is the module that needs exact-cycle
// alignment between its two upstream branches -- that alignment is a
// static property
// the two branches' declared fixed latencies already guarantee (checked
// by forge.analyze.latency_static's real exact_cycle merge-point
// classification at `forge topgen validate` time, not by anything in
// this module). What THIS module does at runtime is verify that
// guarantee actually held: both branches' x/y/frame_id/tile_id tags are
// compared every cycle, and `tag_mismatch` is a real diagnostic signal,
// not decoration -- if the two branches ever drift apart (a genuine
// design bug, or a latency declaration that doesn't match the RTL/HLS
// that was actually built), this module reports it and refuses to
// assert a valid merged record for that cycle, rather than silently
// pairing mismatched pixels.
//
// Fixed latency 1 cycle, II=1.
//==============================================================================

`timescale 1ns / 1ps

module edge_mask_merge_rtl #(
    parameter PIXEL_WIDTH     = 8,
    parameter MAGNITUDE_WIDTH = 12
)(
    input  wire                        ap_clk,
    input  wire                        ap_rst,

    // Threshold branch (threshold_rtl, delayed to realign with Sobel).
    input  wire [PIXEL_WIDTH-1:0]      thresh_pixel,
    input  wire                        thresh_mask,
    input  wire [11:0]                 thresh_x,
    input  wire [11:0]                 thresh_y,
    input  wire [15:0]                 thresh_frame_id,
    input  wire [15:0]                 thresh_tile_id,
    input  wire                        thresh_end_of_line,
    input  wire                        thresh_end_of_frame,
    input  wire                        thresh_valid,

    // Sobel branch (window_builder_rtl -> sobel_hls).
    input  wire [MAGNITUDE_WIDTH-1:0]  sobel_magnitude,
    input  wire [11:0]                 sobel_x,
    input  wire [11:0]                 sobel_y,
    input  wire [15:0]                 sobel_frame_id,
    input  wire [15:0]                 sobel_tile_id,
    input  wire                        sobel_end_of_line,
    input  wire                        sobel_end_of_frame,
    input  wire                        sobel_valid,

    // forge.edge_mask_stream.v1
    output reg  [PIXEL_WIDTH-1:0]      normalized_pixel,
    output reg  [MAGNITUDE_WIDTH-1:0]  gradient_magnitude,
    output reg                         threshold_mask,
    output reg  [11:0]                 out_x,
    output reg  [11:0]                 out_y,
    output reg  [15:0]                 out_frame_id,
    output reg  [15:0]                 out_tile_id,
    output reg                         out_end_of_line,
    output reg                         out_end_of_frame,
    output reg                         out_valid,

    output reg                         tag_mismatch
);

    wire both_valid = thresh_valid && sobel_valid;
    wire tags_match = (thresh_x == sobel_x) && (thresh_y == sobel_y) &&
                       (thresh_frame_id == sobel_frame_id) &&
                       (thresh_tile_id  == sobel_tile_id) &&
                       (thresh_end_of_line == sobel_end_of_line) &&
                       (thresh_end_of_frame == sobel_end_of_frame);

    always @(posedge ap_clk) begin
        if (ap_rst) begin
            normalized_pixel   <= {PIXEL_WIDTH{1'b0}};
            gradient_magnitude <= {MAGNITUDE_WIDTH{1'b0}};
            threshold_mask     <= 1'b0;
            out_x              <= 12'b0;
            out_y              <= 12'b0;
            out_frame_id       <= 16'b0;
            out_tile_id        <= 16'b0;
            out_end_of_line    <= 1'b0;
            out_end_of_frame   <= 1'b0;
            out_valid          <= 1'b0;
            tag_mismatch       <= 1'b0;
        end else begin
            normalized_pixel   <= thresh_pixel;
            gradient_magnitude <= sobel_magnitude;
            threshold_mask     <= thresh_mask;
            out_x              <= sobel_x;
            out_y              <= sobel_y;
            out_frame_id       <= sobel_frame_id;
            out_tile_id        <= sobel_tile_id;
            out_end_of_line    <= sobel_end_of_line;
            out_end_of_frame   <= sobel_end_of_frame;
            out_valid          <= both_valid && tags_match;
            tag_mismatch       <= both_valid && !tags_match;
        end
    end

endmodule
