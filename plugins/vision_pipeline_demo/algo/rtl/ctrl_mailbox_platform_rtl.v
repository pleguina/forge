//==============================================================================
// ctrl_mailbox_platform_rtl.v
//==============================================================================
// Slice 10.7B (release-plan Phase 10, preflight.md §26's "generic
// platform fixture"): the SAME real logic as ctrl_mailbox_rtl.v (slice
// 10.4) -- packs {threshold, kernel_mode, frame_limit} into one
// coherent bundle for a cdc: {kind: mailbox_transfer} crossing -- as a
// necessarily separate module, not a reuse of ctrl_mailbox_rtl.v
// unmodified.
//
// Why a separate module, not a reuse: design_cdc.yml (slice 10.4) makes
// `control` its own PRIMARY domain, so ctrl_mailbox_rtl.v's own clock/
// reset ports are literally named ap_clk/ap_rst -- the same convention
// every module in every prior design in this plugin uses for ITS OWN
// primary domain. This design (design_platform_wrapper.yml) reuses the
// real pixel-domain pipeline (winbld/sobel/threshcfg/merge/prpack/
// tstats/tbnd/tjoin/tspack, 9 real modules across slices 10.2/10.3/10.5/
// 10.7A/10.7B) UNMODIFIED -- every one of those is ALSO literally
// ap_clk/ap_rst, since pixel is THEIR OWN primary domain in every
// design they already ship in. Verilog port names can't be
// parametrized, so genuinely giving this design THREE distinct clock
// domains (control/pixel/output, spec §26) while keeping the 9-module
// pixel pipeline reused unmodified requires control to run on a
// distinctly-named clock/reset pair instead -- clk_control/rst_control,
// mirroring pixel_sink_rtl.v's own precedent (slice 10.4) for exactly
// the same reason in the opposite direction (that module uses
// clk_pixel/rst_pixel precisely so its own genuinely-distinct pixel
// domain resolves to its own real top-level net, not the literal
// ap_clk every single-primary-domain design reuses -- see
// pixel_sink_rtl.interface.yaml's own header comment).
//
// ctrl_mailbox_rtl.v itself is untouched -- design_cdc.yml's own
// cdc_xsim flow still reuses it unmodified, unaffected by this slice.
//==============================================================================

`timescale 1ns / 1ps

module ctrl_mailbox_platform_rtl (
    input  wire         clk_control,
    input  wire         rst_control,

    input  wire [7:0]   threshold_in,
    input  wire [1:0]   kernel_mode_in,
    input  wire [15:0]  frame_limit_in,

    output reg  [25:0]  mailbox_out   // {threshold[7:0], kernel_mode[1:0], frame_limit[15:0]}
);

    always @(posedge clk_control) begin
        if (rst_control) begin
            mailbox_out <= 26'b0;
        end else begin
            mailbox_out <= {threshold_in, kernel_mode_in, frame_limit_in};
        end
    end

endmodule
