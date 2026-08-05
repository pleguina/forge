//==============================================================================
// ctrl_pulse_rtl.v
//==============================================================================
// The `control` domain's pulse_sync endpoint pair -- sources
// `apply_in` as a pulse_sync crossing to pixel ("apply_configuration:
// control -> pixel"), and consumes the
// already-synchronized `frame_done_in` return crossing from output
// ("frame_done: output -> control"). See ctrl_level_rtl.v
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
