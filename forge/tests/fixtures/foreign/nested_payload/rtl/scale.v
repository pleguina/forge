// Registered left shift by SHIFT bits.
module scale #(
    parameter SHIFT = 1,
    parameter W     = 16
) (
    input  wire         clk,
    input  wire         rst_n,
    input  wire [W-1:0] x,
    input  wire         x_valid,
    output reg  [W-1:0] y,
    output reg          y_valid
);
    always @(posedge clk) begin
        if (!rst_n) begin
            y       <= {W{1'b0}};
            y_valid <= 1'b0;
        end else begin
            y       <= x << SHIFT;
            y_valid <= x_valid;
        end
    end
endmodule
