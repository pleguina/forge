//==============================================================================
// outsink_pulse_rtl.v
//==============================================================================
// The `output` domain's pulse_sync source -- `frame_done_pulse_in`
// -> `frame_done_out`, crossing to control ("frame_done: output ->
// control"). Its own module (not folded into
// outsink_level_rtl) so the two output -> control return crossings
// (level_sync and pulse_sync) don't collide in cdc_map -- see
// ctrl_level_rtl.v's header for the full reasoning.
//
// output domain: clk_output / rst_output (see outsink_level_rtl.v).
//==============================================================================

`timescale 1ns / 1ps

module outsink_pulse_rtl (
    input  wire  clk_output,
    input  wire  rst_output,

    input  wire  frame_done_pulse_in,   // pulse, testbench-driven

    output reg   frame_done_out          // cdc: {kind: pulse_sync} source -> control
);

    always @(posedge clk_output) begin
        if (rst_output) begin
            frame_done_out <= 1'b0;
        end else begin
            frame_done_out <= frame_done_pulse_in;
        end
    end

endmodule
