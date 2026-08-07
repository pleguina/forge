//==============================================================================
// threshold_rtl.v
//==============================================================================
// Quickstart-tier RTL module:
//   threshold_mask <= (normalized_pixel >= THRESHOLD) ? 1 : 0
// Every other forge.pixel_stream.v1-shaped field is passed through
// unchanged. Fixed 2-cycle registered latency (matching the full
// vision_pipeline_demo design's own threshold_rtl latency — this module
// is reused unmodified in the fuller pipeline assembly,
// not a throwaway quickstart-only variant).
//
// Parameters:
//   WIDTH     - pixel data width (default: 8)
//   THRESHOLD - comparison threshold (default: 96)
//==============================================================================

`timescale 1ns / 1ps

module threshold_rtl #(
    parameter WIDTH     = 8,
    parameter THRESHOLD = 96
)(
    input  wire              ap_clk,
    input  wire              ap_rst,

    input  wire [WIDTH-1:0]  normalized_pixel,
    input  wire [11:0]       x,
    input  wire [11:0]       y,
    input  wire [15:0]       frame_id,
    input  wire [15:0]       tile_id,
    input  wire              end_of_line,
    input  wire              end_of_frame,
    input  wire              in_valid,

    output reg  [WIDTH-1:0]  out_pixel,
    output reg               threshold_mask,
    output reg  [11:0]       out_x,
    output reg  [11:0]       out_y,
    output reg  [15:0]       out_frame_id,
    output reg  [15:0]       out_tile_id,
    output reg               out_end_of_line,
    output reg               out_end_of_frame,
    output reg               out_valid
);

    // ── Stage 1: register inputs ─────────────────────────────────────
    reg [WIDTH-1:0] s1_pixel;
    reg [11:0]      s1_x, s1_y;
    reg [15:0]      s1_frame_id, s1_tile_id;
    reg             s1_end_of_line, s1_end_of_frame, s1_valid;

    always @(posedge ap_clk) begin
        if (ap_rst) begin
            s1_pixel        <= {WIDTH{1'b0}};
            s1_x            <= 12'b0;
            s1_y            <= 12'b0;
            s1_frame_id     <= 16'b0;
            s1_tile_id      <= 16'b0;
            s1_end_of_line  <= 1'b0;
            s1_end_of_frame <= 1'b0;
            s1_valid        <= 1'b0;
        end else begin
            s1_pixel        <= normalized_pixel;
            s1_x            <= x;
            s1_y            <= y;
            s1_frame_id     <= frame_id;
            s1_tile_id      <= tile_id;
            s1_end_of_line  <= end_of_line;
            s1_end_of_frame <= end_of_frame;
            s1_valid        <= in_valid;
        end
    end

    // ── Stage 2: compute + register outputs ──────────────────────────
    always @(posedge ap_clk) begin
        if (ap_rst) begin
            out_pixel        <= {WIDTH{1'b0}};
            threshold_mask   <= 1'b0;
            out_x            <= 12'b0;
            out_y            <= 12'b0;
            out_frame_id     <= 16'b0;
            out_tile_id      <= 16'b0;
            out_end_of_line  <= 1'b0;
            out_end_of_frame <= 1'b0;
            out_valid        <= 1'b0;
        end else begin
            out_pixel        <= s1_pixel;
            threshold_mask   <= (s1_pixel >= THRESHOLD[WIDTH-1:0]) ? 1'b1 : 1'b0;
            out_x            <= s1_x;
            out_y            <= s1_y;
            out_frame_id     <= s1_frame_id;
            out_tile_id      <= s1_tile_id;
            out_end_of_line  <= s1_end_of_line;
            out_end_of_frame <= s1_end_of_frame;
            out_valid        <= s1_valid;
        end
    end

endmodule
