// The example's payload: each link's track-finder candidates (codecs/candidate.yml) rewritten as
// GMT standalone-muon words (codecs/samuon.yml), one cycle later. Replace it with your algorithm.
module candidate_to_gmt (
  input clk, input rst,
  input [63:0] l0_data, input l0_valid, input l0_start, input l0_last,
  input [63:0] l1_data, input l1_valid, input l1_start, input l1_last,
  input [63:0] l2_data, input l2_valid, input l2_start, input l2_last,
  input [63:0] l3_data, input l3_valid, input l3_start, input l3_last,
  input [63:0] l4_data, input l4_valid, input l4_start, input l4_last,
  input [63:0] l5_data, input l5_valid, input l5_start, input l5_last,
  output reg [63:0] q0_data, output reg q0_valid, output reg q0_start, output reg q0_last,
  output reg [63:0] q1_data, output reg q1_valid, output reg q1_start, output reg q1_last,
  output reg [63:0] q2_data, output reg q2_valid, output reg q2_start, output reg q2_last,
  output reg [63:0] q3_data, output reg q3_valid, output reg q3_start, output reg q3_last,
  output reg [63:0] q4_data, output reg q4_valid, output reg q4_start, output reg q4_last,
  output reg [63:0] q5_data, output reg q5_valid, output reg q5_start, output reg q5_last
);
  function [63:0] gmt_word(input [63:0] c);
    // an empty slot stays empty; d0 is 0 for vertex-constrained muons
    gmt_word = c == 64'd0 ? 64'd0 : {8'd0, c[48:41], 7'd0, c[26:14], c[39:27], c[13:1], c[40], 1'b1};
  endfunction

  always @(posedge clk) begin
    if (rst) begin
      q0_valid <= 1'b0;
      q1_valid <= 1'b0;
      q2_valid <= 1'b0;
      q3_valid <= 1'b0;
      q4_valid <= 1'b0;
      q5_valid <= 1'b0;
    end else begin
      q0_data <= gmt_word(l0_data); q0_valid <= l0_valid; q0_start <= l0_start; q0_last <= l0_last;
      q1_data <= gmt_word(l1_data); q1_valid <= l1_valid; q1_start <= l1_start; q1_last <= l1_last;
      q2_data <= gmt_word(l2_data); q2_valid <= l2_valid; q2_start <= l2_start; q2_last <= l2_last;
      q3_data <= gmt_word(l3_data); q3_valid <= l3_valid; q3_start <= l3_start; q3_last <= l3_last;
      q4_data <= gmt_word(l4_data); q4_valid <= l4_valid; q4_start <= l4_start; q4_last <= l4_last;
      q5_data <= gmt_word(l5_data); q5_valid <= l5_valid; q5_start <= l5_start; q5_last <= l5_last;
    end
  end
endmodule
