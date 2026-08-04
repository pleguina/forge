//==============================================================================
// ctrl_pulse_rtl.v
//==============================================================================
// Slice 10.4 (release-plan Phase 10, preflight.md §5 Decision A, spec
// §8.2): the `control` domain's pulse_sync endpoint pair -- sources
// `apply_in` as a pulse_sync crossing to pixel (spec §8.2:
// "apply_configuration: control -> pixel"), and consumes the
// already-synchronized `frame_done_in` return crossing from output
// (spec §8.2: "frame_done: output -> control"). See ctrl_level_rtl.v
// for why this is its own module rather than folded into one bigger
// control-domain register bank.
//==============================================================================

`timescale 1ns / 1ps

module ctrl_pulse_rtl (
    input  wire  ap_clk,
    input  wire  ap_rst,

    input  wire  apply_in,         // pulse
    input  wire  frame_done_in,    // already synchronized pulse (output -> control)

    output reg   pulse_out,        // cdc: {kind: pulse_sync} source -> pixel
    output reg   [7:0] frame_done_count
);

    always @(posedge ap_clk) begin
        if (ap_rst) begin
            pulse_out        <= 1'b0;
            frame_done_count <= 8'b0;
        end else begin
            pulse_out         <= apply_in;
            frame_done_count  <= frame_done_in ? (frame_done_count + 8'd1) : frame_done_count;
        end
    end

endmodule
