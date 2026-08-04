//==============================================================================
// tile_stats_packer_rtl.v
//==============================================================================
// Slice 10.5 (release-plan Phase 10, docs/internal/phase10/preflight.md
// §6.1/§6.2/§9.1): packs tile_summary_join_rtl's tile-statistics fields
// into the frozen 128-bit tile-statistics packet record --
//
//   record_kind{2}=2'd1, tile_id{16}, minimum{8}, maximum{8}, mean{16},
//   variance{24}, frame_id{16}, reserved{38}   (MSB-first, §6.1)
//
// -- with the same toggle-in-payload novelty scheme as
// pixel_result_packer_rtl.v (see that file's header for the full
// rationale) -- {toggle, record[127:0]} = 129 bits total, source of the
// pixel -> output cdc: {kind: async_fifo} connection to packetizer_rtl.
//
// Fixed 1-cycle latency, gated by in_valid (same convention as
// pixel_result_packer_rtl.v).
//
// `out_record_valid` (release-plan Phase 10, slice 10.5): real
// per-record write-enable, same convention as
// pixel_result_packer_rtl.v's own -- see that file's header for the
// full rationale.
//==============================================================================

`timescale 1ns / 1ps

module tile_stats_packer_rtl (
    input  wire        ap_clk,
    input  wire        ap_rst,

    input  wire [15:0] in_tile_id,
    input  wire [7:0]  in_minimum,
    input  wire [7:0]  in_maximum,
    input  wire [15:0] in_mean,
    input  wire [23:0] in_variance,
    input  wire [15:0] in_frame_id,
    input  wire        in_valid,

    // {toggle, record[127:0]} -- source of the pixel -> output
    // cdc: {kind: async_fifo} connection (design_packetizer.yml).
    output reg [128:0] out_record,
    // Real per-record write-enable -- see header.
    output reg          out_record_valid
);

    localparam [1:0] RECORD_KIND_TILE_STATISTICS = 2'd1;

    wire [127:0] packed_record = {
        RECORD_KIND_TILE_STATISTICS,  // [127:126]
        in_tile_id,                   // [125:110]
        in_minimum,                   // [109:102]
        in_maximum,                   // [101:94]
        in_mean,                      // [93:78]
        in_variance,                  // [77:54]
        in_frame_id,                  // [53:38]
        38'b0                         // [37:0] reserved
    };

    always @(posedge ap_clk) begin
        if (ap_rst) begin
            out_record <= 129'b0;
            out_record_valid <= 1'b0;
        end else begin
            out_record_valid <= in_valid;
            if (in_valid) begin
                out_record <= {~out_record[128], packed_record};
            end
        end
    end

endmodule
