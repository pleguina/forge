//==============================================================================
// packetizer_rtl.v
//==============================================================================
// The `output` domain
// multiplexing point for both record kinds onto
// forge.packet_stream.v1: data{256}, keep{32}, last{1} +
// ready/valid. Record and beat layout are fixed by
// docs/development/adr/0003-vision-packet-format.md.
//
// Two independent upstream paths cross into this module's own `output`
// domain via two separate cdc: {kind: async_fifo, depth: 64} connections
// (design_packetizer.yml) -- pixel_result_packer_rtl and
// tile_stats_packer_rtl each source their own crossing, rather than one
// shared arbiter feeding one shared FIFO before the packetizer -- this
// module is the only place the two record kinds
// meet (see ADR 0003's "multiplexing point" decision). Each input port
// is a raw, continuously-driven FIFO `dout`
// (129 bits: {toggle, record[127:0]}, see pixel_result_packer_rtl.v's
// header for the toggle-in-payload novelty scheme) -- a real new record
// on a given path is detected by comparing that path's toggle bit
// against the last-seen value, not by any `empty`/valid port (FORGE's
// structural wiring exposes neither to this module).
//
// Packing: bits[127:0] carry whichever record is ready
// first that beat, bits[255:128] the second, from either path -- slot
// position carries no kind meaning, record_kind alone disambiguates.
// A beat carrying only one ready record packs it into bits[127:0] alone
// with keep[15:0]=16'hFFFF, keep[31:16]=16'h0000. At this design's
// quickstart scale, both paths cross via *independent* async FIFOs, so
// whether any given beat ever carries two records depends on real,
// clock-phase-dependent CDC crossing timing between the two crossings
// -- observed from the real xsim run, not engineered/assumed; the
// single-record-per-beat case is what the packing rule's own
// second sentence already describes, and is what this design's own
// stimulus checker verifies exhaustively (every record decoded and
// checked, from whichever slot it lands in).
//
// `last` (the generic forge.packet_stream.v1 doesn't specify its
// meaning beyond the standard AXI4-Stream-style "end of transfer"
// convention): this design
// chooses `last` = the pixel-result record's own `end_of_frame` field,
// when a pixel-result record occupies this beat (tile-statistics
// records carry no end_of_frame field of their own).
//
// Fixed 3-cycle latency (a real 3-stage pipeline below, not a rough
// estimate), II=1 (accepts a new toggle transition on either path every
// cycle).
//==============================================================================

`timescale 1ns / 1ps

module packetizer_rtl (
    input  wire         clk_output,
    input  wire         rst_output,

    // {toggle, record[127:0]} -- destination of the pixel -> output
    // cdc: {kind: async_fifo} connections (design_packetizer.yml).
    input  wire [128:0] pr_record_in,
    input  wire [128:0] ts_record_in,

    // forge.packet_stream.v1 (docs/development/adr/0003-vision-packet-format.md)
    output reg  [255:0] packet_data,
    output reg  [31:0]  packet_keep,
    output reg          packet_last,
    output reg          packet_valid
);

    // ── Stage 0: toggle-compare novelty detection ──────────────────────
    reg pr_toggle_seen, ts_toggle_seen;
    wire pr_new = (pr_record_in[128] != pr_toggle_seen);
    wire ts_new = (ts_record_in[128] != ts_toggle_seen);

    // ── Stage 0 -> 1 ────────────────────────────────────────────────────
    reg         s1_pr_valid, s1_ts_valid;
    reg [127:0] s1_pr_rec,   s1_ts_rec;

    // ── Stage 1 -> 2 ────────────────────────────────────────────────────
    reg         s2_pr_valid, s2_ts_valid;
    reg [127:0] s2_pr_rec,   s2_ts_rec;

    always @(posedge clk_output) begin
        if (rst_output) begin
            pr_toggle_seen <= 1'b0;
            ts_toggle_seen <= 1'b0;
            s1_pr_valid <= 1'b0;
            s1_ts_valid <= 1'b0;
            s1_pr_rec   <= 128'b0;
            s1_ts_rec   <= 128'b0;

            s2_pr_valid <= 1'b0;
            s2_ts_valid <= 1'b0;
            s2_pr_rec   <= 128'b0;
            s2_ts_rec   <= 128'b0;

            packet_data  <= 256'b0;
            packet_keep  <= 32'b0;
            packet_last  <= 1'b0;
            packet_valid <= 1'b0;
        end else begin
            // Stage 0: latch novelty + own the toggle state.
            pr_toggle_seen <= pr_record_in[128];
            ts_toggle_seen <= ts_record_in[128];
            s1_pr_valid <= pr_new;
            s1_ts_valid <= ts_new;
            s1_pr_rec   <= pr_record_in[127:0];
            s1_ts_rec   <= ts_record_in[127:0];

            // Stage 1 -> 2: pass through.
            s2_pr_valid <= s1_pr_valid;
            s2_ts_valid <= s1_ts_valid;
            s2_pr_rec   <= s1_pr_rec;
            s2_ts_rec   <= s1_ts_rec;

            // Stage 2 -> output: pack the beat.
            packet_valid <= s2_pr_valid || s2_ts_valid;
            if (s2_pr_valid && s2_ts_valid) begin
                packet_data <= {s2_ts_rec, s2_pr_rec};
                packet_keep <= 32'hFFFFFFFF;
                packet_last <= s2_pr_rec[47];  // pixel-result end_of_frame
            end else if (s2_pr_valid) begin
                packet_data <= {128'b0, s2_pr_rec};
                packet_keep <= 32'h0000FFFF;
                packet_last <= s2_pr_rec[47];
            end else if (s2_ts_valid) begin
                packet_data <= {128'b0, s2_ts_rec};
                packet_keep <= 32'h0000FFFF;
                packet_last <= 1'b0;
            end else begin
                packet_data <= 256'b0;
                packet_keep <= 32'b0;
                packet_last <= 1'b0;
            end
        end
    end

endmodule
