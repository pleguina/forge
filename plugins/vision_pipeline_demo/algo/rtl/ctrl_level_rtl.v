//==============================================================================
// ctrl_level_rtl.v
//==============================================================================
// Slice 10.4 (release-plan Phase 10, preflight.md §5 Decision A, spec
// §8.1): the `control` domain's level_sync endpoint pair -- sources
// `enable_in` as a level_sync crossing to pixel, and consumes the
// already-synchronized `error_level_in` return crossing from output
// (spec §8.1: "error_level: output -> control").
//
// A dedicated module for this one crossing kind (not folded into a
// single "control_src_rtl" register bank) because
// forge.topgen.generators.structural_verilog's cdc_map is keyed by
// (src_module, dst_module) alone, not per-pin (release-plan Phase 10,
// slice 10.4 finding, forge.topgen.config's new ATG027 check) -- three
// different cdc kinds sourced from the same control-domain instance to
// the same pixel-domain instance would silently collapse into one.
// Splitting the source side into one small module per crossing kind
// (this module, ctrl_pulse_rtl, ctrl_mailbox_rtl) routes around that
// constraint with zero framework risk.
//
// control domain: literal ap_clk/ap_rst -- the same convention every
// existing module in this repo already uses (control is this design's
// primary domain).
//==============================================================================

`timescale 1ns / 1ps

module ctrl_level_rtl (
    input  wire  ap_clk,
    input  wire  ap_rst,

    input  wire  enable_in,
    input  wire  error_level_in,   // already synchronized (output -> control)

    output reg   level_out,        // cdc: {kind: level_sync} source -> pixel
    output reg   error_level_status
);

    always @(posedge ap_clk) begin
        if (ap_rst) begin
            level_out          <= 1'b0;
            error_level_status <= 1'b0;
        end else begin
            level_out          <= enable_in;
            error_level_status <= error_level_in;
        end
    end

endmodule
