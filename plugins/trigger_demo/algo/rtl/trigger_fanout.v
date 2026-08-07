module trigger_fanout (
    input  wire       ap_clk,
    input  wire       ap_rst,
    input  wire       trigger_accept,
    input  wire [7:0] trigger_quality,
    input  wire       trigger_valid,
    output wire       accept_lane_0,
    output wire       accept_lane_1,
    output wire [7:0] quality_lane_0,
    output wire [7:0] quality_lane_1,
    output wire       valid_lane_0,
    output wire       valid_lane_1
);

  assign accept_lane_0 = trigger_accept;
  assign accept_lane_1 = trigger_accept;
  assign quality_lane_0 = trigger_quality;
  assign quality_lane_1 = trigger_quality;
  assign valid_lane_0 = trigger_valid;
  assign valid_lane_1 = trigger_valid;

endmodule