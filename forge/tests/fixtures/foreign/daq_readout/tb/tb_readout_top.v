`timescale 1ns/1ps
module tb_readout_top;
    reg sys_clk = 0, sys_rst = 1, link_clk = 0, link_rst = 1;
    reg [23:0] ch0_raw = 0, ch1_raw = 0, ch2_raw = 0, ch3_raw = 0;
    reg ch0_raw_valid = 0, ch1_raw_valid = 0, ch2_raw_valid = 0, ch3_raw_valid = 0;
    wire [7:0] link_data;
    wire link_data_valid;
    wire [31:0] monitor_count;

    always #4 sys_clk = ~sys_clk;
    always #2 link_clk = ~link_clk;

    readout_top dut (.*);

    initial begin
        #40 sys_rst = 0; link_rst = 0;
        #400 $finish;
    end
endmodule
