// Drops every other input sample. First stage of the pipeline.
module decimator #(
    parameter SAMPLE_W = 16
) (
    input  wire                 clk,
    input  wire                 rst_n,
    input  wire [SAMPLE_W-1:0]  sample_in,
    input  wire                 sample_in_valid,
    output reg  [SAMPLE_W-1:0]  decimated,
    output reg                  decimated_valid
);
    reg phase;

    always @(posedge clk) begin
        if (!rst_n) begin
            phase           <= 1'b0;
            decimated       <= {SAMPLE_W{1'b0}};
            decimated_valid <= 1'b0;
        end else begin
            decimated_valid <= sample_in_valid & phase;
            if (sample_in_valid) begin
                phase     <= ~phase;
                decimated <= sample_in;
            end
        end
    end
endmodule
