//==============================================================================
// pixel_sink_rtl.v
//==============================================================================
// The `pixel` domain's consumer of the three control -> pixel
// crossings (level_sync/pulse_sync/mailbox_transfer), and the *source*
// of the pixel -> output async_fifo crossing.
//
// pixel domain: clk_pixel / rst_pixel -- rst_pixel is a real
// reset_domains.rst_pixel.sync: reset_sync destination (see
// design_cdc.yml), not a raw external reset; clk_pixel is genuinely
// 200MHz (5ns period, see verify.flow.yml's extra_clocks), independent
// of and faster than control's 50MHz ap_clk.
//
// `result_out` (26 bits so the async_fifo has something non-trivial to
// carry, mirrors the mailbox bundle back out) is driven from a plain
// testbench-held-constant `result_data_in` -- cdc_async_fifo has no
// producer-side backpressure signal (algo/rtl's own real primitive,
// copied from plugins/trigger_demo -- see its header comment), so
// holding the source value constant for the whole test, rather than
// streaming a changing counter, is what keeps this check
// (does a value cross correctly at all) decoupled from FIFO fill/drain
// dynamics -- throughput/backpressure behavior is exercised separately,
// once it's actually in scope for a given design.
//==============================================================================

`timescale 1ns / 1ps

module pixel_sink_rtl (
    input  wire        clk_pixel,
    input  wire        rst_pixel,

    // Already-synchronized inputs from control_src_rtl's own three
    // source crossings.
    input  wire         level_in,
    input  wire         pulse_in,      // pulse
    input  wire [25:0]  mailbox_in,

    // Raw pixel-domain input (testbench-driven) -- the value pushed
    // into the pixel -> output async_fifo.
    input  wire [25:0]  result_data_in,

    // External readback (status).
    output reg          enable_status,
    output reg  [7:0]   apply_count,
    output reg  [25:0]  mailbox_status,

    // Source net for the design's own pixel -> output
    // cdc: {kind: async_fifo} connection.
    output reg  [25:0]  result_out
);

    always @(posedge clk_pixel) begin
        if (rst_pixel) begin
            enable_status   <= 1'b0;
            apply_count     <= 8'b0;
            mailbox_status  <= 26'b0;
            result_out      <= 26'b0;
        end else begin
            enable_status   <= level_in;
            apply_count     <= pulse_in ? (apply_count + 8'd1) : apply_count;
            mailbox_status  <= mailbox_in;
            result_out      <= result_data_in;
        end
    end

endmodule
