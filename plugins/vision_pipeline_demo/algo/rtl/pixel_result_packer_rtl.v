//==============================================================================
// pixel_result_packer_rtl.v
//==============================================================================
// Packs edge_mask_merge_rtl's pixel-result fields into
// the 128-bit pixel-result packet record (layout fixed by
// docs/development/adr/0003-vision-packet-format.md) --
//
//   record_kind{2}=2'd0, normalized_pixel{8}, gradient_magnitude{12},
//   threshold_mask{1}, x{12}, y{12}, frame_id{16}, tile_id{16},
//   end_of_line{1}, end_of_frame{1}, reserved{47}   (MSB-first)
//
// -- and appends a 1-bit toggle at bit[128] so the crossing
// cdc: {kind: async_fifo} connection to packetizer_rtl (output domain)
// carries real novelty information. FORGE's structural port_map wiring
// has no generic valid/ready concept (every existing CDC primitive's own
// header makes the same point) -- the destination side of an async_fifo
// crossing sees `dout` continuously, with no `empty` port routed to it.
// A toggle bit embedded *inside* the crossed payload sidesteps that
// entirely: cdc_async_fifo's own read-domain dout only changes value
// when its internal read pointer actually advances (i.e. exactly once
// per real dequeued entry, see cdc_async_fifo.v's `empty` guard) -- so
// the crossed toggle bit changes value if and only if a genuinely new
// record was dequeued, and holds steady (repeating the last real value,
// toggle included) on every cycle the FIFO has nothing new. This needed
// no core-framework change (no new port-wiring convention in
// forge.generation.generators.structural_verilog) -- the same "continuously
// driven, self-describing payload" model every other CDC kind in this
// repo already uses, just carrying one extra bit (this toggle-in-payload
// convention is documented project-wide in
// docs/development/adr/0002-cdc-primitive-semantics.md).
//
// Fixed 1-cycle latency: a single registered stage, gated by in_valid --
// holding both `out_record`'s value and the toggle unchanged on any
// cycle in_valid is low, so a low-valid cycle produces neither a new
// record nor a false toggle flip downstream.
//
// `out_record_valid`: a real,
// per-record write-enable pulse -- `in_valid` registered on the same
// edge as `out_record` itself, so it is high on exactly the cycle
// `out_record` carries a genuinely new value. Wired as the pixel ->
// output cdc: {kind: async_fifo, write_enable_pin: out_record_valid}
// connection's write-enable (design_packetizer.yml) -- without it,
// cdc_async_fifo writes a fresh entry every write-domain cycle
// regardless of novelty (see that primitive's own header for the real
// gap this closes, found wiring this exact design).
//==============================================================================

`timescale 1ns / 1ps

module pixel_result_packer_rtl (
    input  wire        ap_clk,
    input  wire        ap_rst,

    input  wire [7:0]  in_normalized_pixel,
    input  wire [11:0] in_gradient_magnitude,
    input  wire        in_threshold_mask,
    input  wire [11:0] in_x,
    input  wire [11:0] in_y,
    input  wire [15:0] in_frame_id,
    input  wire [15:0] in_tile_id,
    input  wire        in_end_of_line,
    input  wire        in_end_of_frame,
    input  wire        in_valid,

    // {toggle, record[127:0]} -- source of the pixel -> output
    // cdc: {kind: async_fifo} connection (design_packetizer.yml).
    output reg [128:0] out_record,
    // Real per-record write-enable -- see header.
    output reg          out_record_valid
);

    localparam [1:0] RECORD_KIND_PIXEL_RESULT = 2'd0;

    wire [127:0] packed_record = {
        RECORD_KIND_PIXEL_RESULT,   // [127:126]
        in_normalized_pixel,        // [125:118]
        in_gradient_magnitude,      // [117:106]
        in_threshold_mask,          // [105]
        in_x,                       // [104:93]
        in_y,                       // [92:81]
        in_frame_id,                // [80:65]
        in_tile_id,                 // [64:49]
        in_end_of_line,             // [48]
        in_end_of_frame,            // [47]
        47'b0                       // [46:0] reserved
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
