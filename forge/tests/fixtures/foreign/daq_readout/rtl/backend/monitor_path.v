// The other consumer of the same event word — the fan-out FORGE must ask
// about rather than resolve.
module monitor_path #(
    parameter HIT_W = 32
) (
    input  wire              sys_clk,
    input  wire              sys_rst,
    input  wire [HIT_W-1:0]  event_word,
    input  wire              event_valid,
    output reg  [31:0]       monitor_count
);
    always @(posedge sys_clk) begin
        if (sys_rst)
            monitor_count <= 32'd0;
        else if (event_valid)
            monitor_count <= monitor_count + 32'd1;
    end
endmodule
