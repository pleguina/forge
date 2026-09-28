// APB-ish register file. Conventional peripheral naming throughout.
module regfile #(
    parameter ADDR_W = 8,
    parameter DATA_W = 32
) (
    input  wire               pclk,
    input  wire               presetn,
    input  wire [ADDR_W-1:0]  paddr,
    input  wire               pwrite,
    input  wire [DATA_W-1:0]  pwdata,
    output reg  [DATA_W-1:0]  ctrl_word,
    output reg                ctrl_word_valid
);
    always @(posedge pclk) begin
        if (!presetn) begin
            ctrl_word       <= {DATA_W{1'b0}};
            ctrl_word_valid <= 1'b0;
        end else begin
            ctrl_word_valid <= pwrite;
            if (pwrite)
                ctrl_word <= pwdata;
        end
    end
endmodule
