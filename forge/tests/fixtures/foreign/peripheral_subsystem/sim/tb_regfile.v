`timescale 1ns/1ps
module tb_regfile;
    reg pclk = 0, presetn = 0, pwrite = 0;
    reg [7:0]  paddr  = 8'd0;
    reg [31:0] pwdata = 32'd0;
    wire [31:0] ctrl_word;
    wire ctrl_word_valid;

    always #5 pclk = ~pclk;

    regfile u_regfile (
        .pclk(pclk), .presetn(presetn), .paddr(paddr),
        .pwrite(pwrite), .pwdata(pwdata),
        .ctrl_word(ctrl_word), .ctrl_word_valid(ctrl_word_valid));

    initial begin
        #20 presetn = 1;
        #200 $finish;
    end
endmodule
