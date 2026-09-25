//==============================================================================
// signal_delay.v
//==============================================================================
// Parameterized signal delay module using shift registers
//
// This module implements an N-cycle delay for arbitrary-width signals using
// shift register primitives (SRL16/SRL32). The design is optimized for
// synthesis to use FPGA shift register LUTs efficiently.
//
// Parameters:
//   WIDTH - Bit width of the signal to delay (default: 1)
//   DEPTH - Number of clock cycles to delay (default: 1)
//
// Usage:
//   signal_delay #(.WIDTH(54), .DEPTH(50)) delay_inst (
//       .clk(ap_clk),
//       .rst(ap_rst),
//       .din(input_signal),
//       .dout(delayed_signal)
//   );
//
// Resources:
//   Approximately WIDTH * DEPTH / 32 SRL32E primitives
//   (Each SRL32E can implement up to 32 cycles of delay for 1 bit)
//
// Timing:
//   Input at cycle N appears at output at cycle N+DEPTH
//   Reset is synchronous and clears the entire shift register chain
//==============================================================================

`timescale 1ns / 1ps

module signal_delay #(
    parameter WIDTH = 1,
    parameter DEPTH = 1
)(
    input  wire             clk,
    input  wire             rst,
    input  wire [WIDTH-1:0] din,
    output wire [WIDTH-1:0] dout
);

    generate
        if (DEPTH == 0) begin : gen_no_delay
            assign dout = din;

        end else if (DEPTH == 1) begin : gen_single_reg
            reg [WIDTH-1:0] delay_reg;

            always @(posedge clk) begin
                if (rst) begin
                    delay_reg <= {WIDTH{1'b0}};
                end else begin
                    delay_reg <= din;
                end
            end

            assign dout = delay_reg;

        end else begin : gen_shift_reg
            reg [WIDTH-1:0] shift_reg [0:DEPTH-1];
            integer i;

            always @(posedge clk) begin
                if (rst) begin
                    for (i = 0; i < DEPTH; i = i + 1) begin
                        shift_reg[i] <= {WIDTH{1'b0}};
                    end
                end else begin
                    shift_reg[0] <= din;
                    for (i = 1; i < DEPTH; i = i + 1) begin
                        shift_reg[i] <= shift_reg[i-1];
                    end
                end
            end

            assign dout = shift_reg[DEPTH-1];
        end
    endgenerate

endmodule