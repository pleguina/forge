// Packs a shaped sample plus a sequence number into an output word.
module packer #(
    parameter SAMPLE_W = 16,
    parameter PACKET_W = 32
) (
    input  wire                 clk,
    input  wire                 rst_n,
    input  wire [SAMPLE_W-1:0]  shaped,
    input  wire                 shaped_valid,
    output reg  [PACKET_W-1:0]  packet_out,
    output reg                  packet_out_valid
);
    reg [15:0] sequence_number;

    always @(posedge clk) begin
        if (!rst_n) begin
            sequence_number  <= 16'd0;
            packet_out       <= {PACKET_W{1'b0}};
            packet_out_valid <= 1'b0;
        end else begin
            packet_out_valid <= shaped_valid;
            if (shaped_valid) begin
                sequence_number <= sequence_number + 16'd1;
                packet_out      <= {sequence_number, shaped};
            end
        end
    end
endmodule
