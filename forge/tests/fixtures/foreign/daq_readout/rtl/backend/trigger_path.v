// One of two consumers of the aggregator's event word.
module trigger_path #(
    parameter HIT_W = 32
) (
    input  wire              sys_clk,
    input  wire              sys_rst,
    input  wire [HIT_W-1:0]  event_word,
    input  wire              event_valid,
    output reg  [15:0]       trigger_word,
    output reg               trigger_valid
);
    always @(posedge sys_clk) begin
        if (sys_rst) begin
            trigger_word  <= 16'd0;
            trigger_valid <= 1'b0;
        end else begin
            trigger_valid <= event_valid;
            trigger_word  <= event_word[15:0];
        end
    end
endmodule
