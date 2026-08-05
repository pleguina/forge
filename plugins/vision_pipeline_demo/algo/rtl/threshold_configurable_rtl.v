//==============================================================================
// threshold_configurable_rtl.v
//==============================================================================
// Slice 10.7B (release-plan Phase 10, preflight.md §26/§9.1's "generic
// platform fixture" — a real, runtime-configurable variant of
// threshold_rtl.v, added alongside it (threshold_rtl.v itself stays
// "reused unmodified" per its own header, not touched by this slice).
//
// threshold_rtl.v's THRESHOLD is a compile-time Verilog parameter; this
// module instead takes it from the `control` domain's real
// cdc: {kind: mailbox_transfer} crossing (ctrl_mailbox_rtl.v's own
// {threshold[7:0], kernel_mode[1:0], frame_limit[15:0]} bundle,
// unchanged from slice 10.4/design_cdc.yml) via `mailbox_in` — the same
// plain, continuously-driven destination-side convention
// pixel_sink_rtl.v's own `mailbox_in` already established (no
// `dout_valid` companion signal needed: this module doesn't care
// exactly when the mailbox value settled, only that it has settled by
// the time it next samples it at a frame boundary — the driving
// stimulus is responsible for a generous settle margin between writing
// a new mailbox value and that value's frame, the same "no exact-cycle
// CDC timing assumed" precedent every mailbox_transfer/async_fifo
// consumer in this plugin already follows).
//
// Configuration-update timing (preflight.md §9.1, frozen): applied at
// the next frame boundary, not immediately. Realized here by latching
// `mailbox_in`'s current threshold field into `active_threshold` only
// on the frame's first accepted sample (`in_valid && x==0 && y==0`) —
// whatever the mailbox holds at that instant becomes this frame's
// threshold; a value written mid-frame is invisible until the next
// frame's own first sample, never retroactively affecting an in-flight
// frame. `kernel_mode`/`frame_limit` are carried through the mailbox
// bundle (matching the frozen forge.configuration_mailbox.v1 format)
// but not functionally consumed by this fixture — no downstream
// consumer needs them yet (Sobel is a fixed kernel; nothing in this
// plugin enforces a frame-count limit) — an honest scope boundary, not
// an oversight.
//
// Otherwise identical to threshold_rtl.v: same 2-cycle registered
// latency (preserving the exact-cycle merge property against the sobel
// branch), same `>=` comparison (preflight.md §9.1, frozen), same
// pass-through fields.
//
// Parameters:
//   WIDTH - pixel data width (default: 8)
//==============================================================================

`timescale 1ns / 1ps

module threshold_configurable_rtl #(
    parameter WIDTH = 8
)(
    input  wire              ap_clk,
    input  wire              ap_rst,

    input  wire [WIDTH-1:0]  normalized_pixel,
    input  wire [11:0]       x,
    input  wire [11:0]       y,
    input  wire [15:0]       frame_id,
    input  wire [15:0]       tile_id,
    input  wire              end_of_line,
    input  wire              end_of_frame,
    input  wire              in_valid,

    // Destination side of the control -> pixel mailbox_transfer crossing
    // (ctrl_mailbox_rtl.v's own packing: {threshold[7:0], kernel_mode[1:0],
    // frame_limit[15:0]}).
    input  wire [25:0]       mailbox_in,

    output reg  [WIDTH-1:0]  out_pixel,
    output reg                threshold_mask,
    output reg  [11:0]       out_x,
    output reg  [11:0]       out_y,
    output reg  [15:0]       out_frame_id,
    output reg  [15:0]       out_tile_id,
    output reg                out_end_of_line,
    output reg                out_end_of_frame,
    output reg                out_valid
);

    // ── Frame-boundary-gated active threshold ────────────────────────
    reg [WIDTH-1:0] active_threshold;
    wire            frame_start = in_valid && (x == 12'd0) && (y == 12'd0);

    always @(posedge ap_clk) begin
        if (ap_rst) begin
            active_threshold <= {WIDTH{1'b0}};
        end else if (frame_start) begin
            active_threshold <= mailbox_in[25:18];
        end
    end

    // ── Stage 1: register inputs (mirrors threshold_rtl.v) ───────────
    reg [WIDTH-1:0] s1_pixel;
    reg [11:0]      s1_x, s1_y;
    reg [15:0]      s1_frame_id, s1_tile_id;
    reg             s1_end_of_line, s1_end_of_frame, s1_valid;
    reg [WIDTH-1:0] s1_active_threshold;

    always @(posedge ap_clk) begin
        if (ap_rst) begin
            s1_pixel            <= {WIDTH{1'b0}};
            s1_x                <= 12'b0;
            s1_y                <= 12'b0;
            s1_frame_id         <= 16'b0;
            s1_tile_id          <= 16'b0;
            s1_end_of_line      <= 1'b0;
            s1_end_of_frame     <= 1'b0;
            s1_valid            <= 1'b0;
            s1_active_threshold <= {WIDTH{1'b0}};
        end else begin
            s1_pixel            <= normalized_pixel;
            s1_x                <= x;
            s1_y                <= y;
            s1_frame_id         <= frame_id;
            s1_tile_id          <= tile_id;
            s1_end_of_line      <= end_of_line;
            s1_end_of_frame     <= end_of_frame;
            s1_valid            <= in_valid;
            // Sampled the same cycle as the frame-start latch above, so
            // a frame's own first pixel already compares against that
            // frame's own newly-latched threshold, not the prior one.
            s1_active_threshold <= frame_start ? mailbox_in[25:18] : active_threshold;
        end
    end

    // ── Stage 2: compute + register outputs ──────────────────────────
    always @(posedge ap_clk) begin
        if (ap_rst) begin
            out_pixel        <= {WIDTH{1'b0}};
            threshold_mask   <= 1'b0;
            out_x            <= 12'b0;
            out_y            <= 12'b0;
            out_frame_id     <= 16'b0;
            out_tile_id      <= 16'b0;
            out_end_of_line  <= 1'b0;
            out_end_of_frame <= 1'b0;
            out_valid        <= 1'b0;
        end else begin
            out_pixel        <= s1_pixel;
            threshold_mask   <= (s1_pixel >= s1_active_threshold) ? 1'b1 : 1'b0;
            out_x            <= s1_x;
            out_y            <= s1_y;
            out_frame_id     <= s1_frame_id;
            out_tile_id      <= s1_tile_id;
            out_end_of_line  <= s1_end_of_line;
            out_end_of_frame <= s1_end_of_frame;
            out_valid        <= s1_valid;
        end
    end

endmodule
