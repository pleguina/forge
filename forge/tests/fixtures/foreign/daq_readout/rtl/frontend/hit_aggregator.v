// Gathers four decoded channels. Per-channel array naming: FORGE must not
// invent the binding from ch0_hit..ch3_hit to the decoders' scalar `hit`.
module hit_aggregator #(
    parameter HIT_W = 32
) (
    input  wire              sys_clk,
    input  wire              sys_rst,
    input  wire [HIT_W-1:0]  ch0_hit,
    input  wire              ch0_hit_valid,
    input  wire [HIT_W-1:0]  ch1_hit,
    input  wire              ch1_hit_valid,
    input  wire [HIT_W-1:0]  ch2_hit,
    input  wire              ch2_hit_valid,
    input  wire [HIT_W-1:0]  ch3_hit,
    input  wire              ch3_hit_valid,
    output reg  [HIT_W-1:0]  event_word,
    output reg               event_valid,
    output reg  [3:0]        channel_mask
);
    always @(posedge sys_clk) begin
        if (sys_rst) begin
            event_word   <= {HIT_W{1'b0}};
            event_valid  <= 1'b0;
            channel_mask <= 4'd0;
        end else begin
            channel_mask <= {ch3_hit_valid, ch2_hit_valid, ch1_hit_valid, ch0_hit_valid};
            event_valid  <= ch0_hit_valid | ch1_hit_valid | ch2_hit_valid | ch3_hit_valid;
            event_word   <= ch0_hit ^ ch1_hit ^ ch2_hit ^ ch3_hit;
        end
    end
endmodule
