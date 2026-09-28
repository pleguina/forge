// Bench for the whole chain. Not part of the synthesised design — discovery
// must classify this as a testbench and keep its module out of the topology.
`timescale 1ns/1ps
module tb_pipeline;
    reg         clk = 1'b0;
    reg         rst_n = 1'b0;
    reg  [15:0] sample_in = 16'd0;
    reg         sample_in_valid = 1'b0;
    wire [15:0] decimated, shaped;
    wire        decimated_valid, shaped_valid;
    wire [31:0] packet_out;
    wire        packet_out_valid;

    always #5 clk = ~clk;

    decimator u_decimator (
        .clk(clk), .rst_n(rst_n),
        .sample_in(sample_in), .sample_in_valid(sample_in_valid),
        .decimated(decimated), .decimated_valid(decimated_valid));

    shaper u_shaper (
        .clk(clk), .rst_n(rst_n),
        .decimated(decimated), .decimated_valid(decimated_valid),
        .shaped(shaped), .shaped_valid(shaped_valid));

    packer u_packer (
        .clk(clk), .rst_n(rst_n),
        .shaped(shaped), .shaped_valid(shaped_valid),
        .packet_out(packet_out), .packet_out_valid(packet_out_valid));

    initial begin
        #20 rst_n = 1'b1;
        #500 $finish;
    end
endmodule
