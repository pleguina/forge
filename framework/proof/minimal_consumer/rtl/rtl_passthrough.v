module rtl_passthrough (
    input  wire       ap_clk,
    input  wire       ap_rst,
    input  wire [7:0] in_data,
    output reg  [7:0] out_data
);

always @(posedge ap_clk) begin
    if (ap_rst) begin
        out_data <= 8'd0;
    end else begin
        out_data <= in_data;
    end
end

endmodule