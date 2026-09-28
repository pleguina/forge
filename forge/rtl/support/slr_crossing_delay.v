//==============================================================================
// slr_crossing_delay.v
//==============================================================================
// Protected pipeline delay for SLR-crossing boundaries.
//
// This module is NOT intended to be optimised into SRLs.
// It creates real, named, protected FF stages that can be constrained by XDC
// into an SLR boundary pblock or Laguna SLL registers.
//
// Use only for short physical crossing delays, typically DEPTH=1 or DEPTH=2.
//
// Parameters:
//   WIDTH - Bit width of the signal to delay (default: 1)
//   DEPTH - Number of protected FF stages (default: 2)
//
// Timing:
//   Input at cycle N appears at output at cycle N+DEPTH
//
// Important:
//   - DONT_TOUCH prevents absorption/retiming of the FFs.
//   - SHREG_EXTRACT="NO" prevents conversion to SRL16/SRL32.
//   - Stage names stage0_reg, stage1_reg, stage2_reg are stable and referenced
//     from algo_top.crossings.json and the generated XDC constraints.
//
// Interface is intentionally identical to signal_delay so the generator can
// substitute one for the other by name alone.
//==============================================================================

`timescale 1ns / 1ps

module slr_crossing_delay #(
    parameter WIDTH = 1,
    parameter DEPTH = 2
)(
    input  wire             clk,
    input  wire             rst,
    input  wire [WIDTH-1:0] din,
    output wire [WIDTH-1:0] dout
);

    generate

        if (DEPTH == 0) begin : gen_no_delay

            // DEPTH == 0 is allowed as a wire for non-boundary use, but the
            // generator should reject boundary tags with DEPTH == 0.
            assign dout = din;

        end else if (DEPTH == 1) begin : gen_depth1

            // Single protected FF stage.
            // Board XDC targets this cell pattern:
            //   <hier>/gen_depth1/stage0_reg*
            (* DONT_TOUCH = "TRUE", SHREG_EXTRACT = "NO" *)
            reg [WIDTH-1:0] stage0_reg;

            always @(posedge clk) begin
                if (rst) begin
                    stage0_reg <= {WIDTH{1'b0}};
                end else begin
                    stage0_reg <= din;
                end
            end

            assign dout = stage0_reg;

        end else if (DEPTH == 2) begin : gen_depth2

            // Two protected FF stages.
            //   stage0_reg: source-side / local launch register.
            //   stage1_reg: boundary / destination-side register — best candidate
            //               for USER_SLL_REG or a boundary pblock placement.
            //
            // Board XDC targets:
            //   <hier>/gen_depth2/stage0_reg*
            //   <hier>/gen_depth2/stage1_reg*
            (* DONT_TOUCH = "TRUE", SHREG_EXTRACT = "NO" *)
            reg [WIDTH-1:0] stage0_reg;

            (* DONT_TOUCH = "TRUE", SHREG_EXTRACT = "NO" *)
            reg [WIDTH-1:0] stage1_reg;

            always @(posedge clk) begin
                if (rst) begin
                    stage0_reg <= {WIDTH{1'b0}};
                    stage1_reg <= {WIDTH{1'b0}};
                end else begin
                    stage0_reg <= din;
                    stage1_reg <= stage0_reg;
                end
            end

            assign dout = stage1_reg;

        end else begin : gen_depth_general

            // General protected chain for DEPTH > 2.
            // Stages are named stage0_reg through stage{DEPTH-1}_reg using a
            // packed array.  XDC patterns must match stage*_reg[*] for the whole
            // chain if placement as a unit is required.
            (* DONT_TOUCH = "TRUE", SHREG_EXTRACT = "NO" *)
            reg [WIDTH-1:0] stage_reg [0:DEPTH-1];
            integer i;

            always @(posedge clk) begin
                if (rst) begin
                    for (i = 0; i < DEPTH; i = i + 1) begin
                        stage_reg[i] <= {WIDTH{1'b0}};
                    end
                end else begin
                    stage_reg[0] <= din;
                    for (i = 1; i < DEPTH; i = i + 1) begin
                        stage_reg[i] <= stage_reg[i-1];
                    end
                end
            end

            assign dout = stage_reg[DEPTH-1];

        end

    endgenerate

endmodule
