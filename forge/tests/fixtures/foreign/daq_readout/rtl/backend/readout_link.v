// Output serialiser on its own clock domain.
module readout_link (
    input  wire        link_clk,
    input  wire        link_rst,
    input  wire [15:0] trigger_word,
    input  wire        trigger_valid,
    output reg  [7:0]  link_data,
    output reg         link_data_valid
);
    always @(posedge link_clk) begin
        if (link_rst) begin
            link_data       <= 8'd0;
            link_data_valid <= 1'b0;
        end else begin
            link_data_valid <= trigger_valid;
            link_data       <= trigger_word[7:0];
        end
    end
endmodule
