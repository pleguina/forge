//==============================================================================
// ctrl_mailbox_rtl.v
//==============================================================================
// Slice 10.4 (release-plan Phase 10, preflight.md §5 Decision A, spec
// §8.3): the `control` domain's mailbox_transfer source -- packs
// {threshold, kernel_mode, frame_limit} into one coherent bundle for
// the control -> pixel cdc: {kind: mailbox_transfer} crossing (spec
// §8.3: "The bundle must update atomically. Independent bit
// synchronizers are forbidden" -- exactly why this is one
// mailbox_transfer connection carrying one packed bus, not three
// separate level_sync-style crossings). See ctrl_level_rtl.v for why
// this is its own module.
//==============================================================================

`timescale 1ns / 1ps

module ctrl_mailbox_rtl (
    input  wire         ap_clk,
    input  wire         ap_rst,

    input  wire [7:0]   threshold_in,
    input  wire [1:0]   kernel_mode_in,
    input  wire [15:0]  frame_limit_in,

    output reg  [25:0]  mailbox_out   // {threshold[7:0], kernel_mode[1:0], frame_limit[15:0]}
);

    always @(posedge ap_clk) begin
        if (ap_rst) begin
            mailbox_out <= 26'b0;
        end else begin
            mailbox_out <= {threshold_in, kernel_mode_in, frame_limit_in};
        end
    end

endmodule
