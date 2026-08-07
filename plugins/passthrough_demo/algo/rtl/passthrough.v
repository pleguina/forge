//==============================================================================
// passthrough.v
//==============================================================================
// Minimal reference algorithm: a single registered N-bit passthrough with a
// valid strobe. Deliberately trivial — this plugin exists purely to prove
// the FORGE pipeline (topology generation, contract wiring, verification
// flow generation) end to end with a genuinely generic algorithm and no
// detector-specific vocabulary anywhere in it. See plugins/trigger_demo/
// for a realistic, richer reference implementation.
//
// Parameters:
//   WIDTH - Bit width of the data path (default: 8)
//
// Behavior:
//   dout <= din, out_valid <= in_valid, one clock cycle later.
//==============================================================================

`timescale 1ns / 1ps

module passthrough #(
    parameter WIDTH = 8
)(
    input  wire             ap_clk,
    input  wire             ap_rst,
    input  wire [WIDTH-1:0] data_in,
    input  wire             data_in_valid,
    output reg  [WIDTH-1:0] data_out,
    output reg              data_out_valid
);

    always @(posedge ap_clk) begin
        if (ap_rst) begin
            data_out       <= {WIDTH{1'b0}};
            data_out_valid <= 1'b0;
        end else begin
            data_out       <= data_in;
            data_out_valid <= data_in_valid;
        end
    end

endmodule
