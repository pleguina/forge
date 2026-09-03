// Serialises a control word onto SPI. Consumes the register file's output.
module spi_master #(
    parameter DATA_W = 32
) (
    input  wire               pclk,
    input  wire               presetn,
    input  wire [DATA_W-1:0]  ctrl_word,
    input  wire               ctrl_word_valid,
    output reg  [DATA_W-1:0]  status_word,
    output reg                status_word_valid,
    output wire               spi_sclk,
    output wire               spi_mosi
);
    reg [4:0] bit_index;
    assign spi_sclk = pclk;
    assign spi_mosi = ctrl_word[bit_index];

    always @(posedge pclk) begin
        if (!presetn) begin
            bit_index         <= 5'd0;
            status_word       <= {DATA_W{1'b0}};
            status_word_valid <= 1'b0;
        end else begin
            status_word_valid <= ctrl_word_valid;
            if (ctrl_word_valid) begin
                bit_index   <= bit_index + 5'd1;
                status_word <= ctrl_word;
            end
        end
    end
endmodule
