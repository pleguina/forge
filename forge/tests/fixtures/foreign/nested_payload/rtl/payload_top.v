// Two scale stages in series under one top, each with its own SHIFT.
`include "payload_defs.vh"
module payload_top (
    input  wire                  clk,
    input  wire                  rst_n,
    input  wire [`PAYLOAD_W-1:0] sample,
    input  wire                  sample_valid,
    output wire [`PAYLOAD_W-1:0] result,
    output wire                  result_valid
);
    wire [`PAYLOAD_W-1:0] mid;
    wire                  mid_valid;

    scale #(.SHIFT(1), .W(`PAYLOAD_W)) u_coarse (
        .clk(clk), .rst_n(rst_n),
        .x(sample), .x_valid(sample_valid),
        .y(mid), .y_valid(mid_valid)
    );
    scale #(.SHIFT(2), .W(`PAYLOAD_W)) u_fine (
        .clk(clk), .rst_n(rst_n),
        .x(mid), .x_valid(mid_valid),
        .y(result), .y_valid(result_valid)
    );
endmodule
