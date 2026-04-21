module trigger_contract_sink (
    input  wire       ap_clk,
    input  wire       ap_rst,
    input  wire       accept_lane_0,
    input  wire       accept_lane_1,
    input  wire [7:0] quality_lane_0,
    input  wire [7:0] quality_lane_1,
    input  wire       valid_lane_0,
    input  wire       valid_lane_1,
    output wire       trigger_accept,
    output wire [7:0] trigger_quality,
    output wire       trigger_valid
);

  assign trigger_accept = accept_lane_0 & accept_lane_1;
  assign trigger_quality = valid_lane_1 ? quality_lane_1 : quality_lane_0;
  assign trigger_valid = valid_lane_0 & valid_lane_1;

endmodule