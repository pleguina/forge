//==============================================================================
// outsink_level_rtl.v
//==============================================================================
// Slice 10.4 (release-plan Phase 10, preflight.md §5 Decision A, spec
// §8.1): the `output` domain's level_sync source (`error_in` ->
// `error_out`, crossing to control, spec §8.1) and the consumer of the
// pixel -> output async_fifo crossing (`result_in`). Combining these
// two is safe (no cdc_map collision, see ctrl_level_rtl.v's header) --
// they're two *different* (src, dst) module pairs
// (pxsink->outsink_level for the FIFO, outsink_level->ctrl_level for
// the level_sync return), not two kinds on the same pair.
//
// output domain: clk_output / rst_output -- rst_output is a real
// reset_domains.rst_output.sync: reset_sync destination
// (design_cdc.yml); clk_output is genuinely 125MHz (8ns period).
//==============================================================================

`timescale 1ns / 1ps

module outsink_level_rtl (
    input  wire         clk_output,
    input  wire         rst_output,

    // Already-synchronized input from pixel_sink_rtl's async_fifo crossing.
    input  wire [25:0]  result_in,

    // Raw output-domain input (testbench-driven).
    input  wire         error_in,

    // External readback (status).
    output reg  [25:0]  result_status,

    // cdc: {kind: level_sync} source -> control.
    output reg           error_out
);

    always @(posedge clk_output) begin
        if (rst_output) begin
            result_status <= 26'b0;
            error_out     <= 1'b0;
        end else begin
            result_status <= result_in;
            error_out     <= error_in;
        end
    end

endmodule
