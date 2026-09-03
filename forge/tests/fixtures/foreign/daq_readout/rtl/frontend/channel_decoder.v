// One input channel. Four of these are instantiated by readout_top.
module channel_decoder #(
    parameter RAW_W = 24,
    parameter HIT_W = 32
) (
    input  wire              sys_clk,
    input  wire              sys_rst,
    input  wire [RAW_W-1:0]  raw_word,
    input  wire              raw_valid,
    output reg  [HIT_W-1:0]  hit,
    output reg               hit_valid
);
    always @(posedge sys_clk) begin
        if (sys_rst) begin
            hit       <= {HIT_W{1'b0}};
            hit_valid <= 1'b0;
        end else begin
            hit_valid <= raw_valid;
            hit       <= {{(HIT_W-RAW_W){1'b0}}, raw_word};
        end
    end
endmodule
