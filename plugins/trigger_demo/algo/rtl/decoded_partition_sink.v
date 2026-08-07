module decoded_partition_sink (
    input wire        ap_clk,
    input wire        ap_rst,
    input wire [31:0] lower_hit_0,
    input wire [31:0] lower_hit_1,
    input wire        lower_valid_0,
    input wire        lower_valid_1,
    input wire [31:0] upper_hit_0,
    input wire [31:0] upper_hit_1,
    input wire        upper_valid_0,
    input wire        upper_valid_1
);

  reg [32:0] lower_accum;
  reg [32:0] upper_accum;

  always @(*) begin
    lower_accum = 33'd0;
    upper_accum = 33'd0;

    if (lower_valid_0) lower_accum = lower_accum + {1'b0, lower_hit_0};
    if (lower_valid_1) lower_accum = lower_accum + {1'b0, lower_hit_1};
    if (upper_valid_0) upper_accum = upper_accum + {1'b0, upper_hit_0};
    if (upper_valid_1) upper_accum = upper_accum + {1'b0, upper_hit_1};
  end

endmodule