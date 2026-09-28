// Two-tap moving average over the decimated stream.
module shaper #(
    parameter SAMPLE_W = 16
) (
    input  wire                 clk,
    input  wire                 rst_n,
    input  wire [SAMPLE_W-1:0]  decimated,
    input  wire                 decimated_valid,
    output reg  [SAMPLE_W-1:0]  shaped,
    output reg                  shaped_valid
);
    reg [SAMPLE_W-1:0] previous;

    always @(posedge clk) begin
        if (!rst_n) begin
            previous     <= {SAMPLE_W{1'b0}};
            shaped       <= {SAMPLE_W{1'b0}};
            shaped_valid <= 1'b0;
        end else begin
            shaped_valid <= decimated_valid;
            if (decimated_valid) begin
                previous <= decimated;
                shaped   <= (decimated >> 1) + (previous >> 1);
            end
        end
    end
endmodule
