//==============================================================================
// window_builder_rtl.v
//==============================================================================
// Slice 10.2 (release-plan Phase 10, docs/internal/phase10/preflight.md
// §6.3/§9.1): a real 3x3 sliding-window generator, sitting between
// pixel_normalizer and sobel_hls so sobel_hls itself stays a small,
// stateless 3x3-compute module. This is the module that actually owns
// the N-dimensional (raw_port_tpl, dims:[3,3]) physical binding the
// full spec calls for.
//
// Border policy (frozen, preflight §9.1): zero-padding. Out-of-frame
// taps read as 0 — never dropped, so every accepted input pixel still
// produces exactly one output window (1:1), matching the fixed-latency
// model every other module in this design uses.
//
// ── Why this needs a real, non-trivial pipeline latency ─────────────
// A causal 3x3 window centered on pixel (x,y) fundamentally needs pixel
// (x+1,y+1) — which, for a raster-scanned stream, does not arrive until
// FRAME_WIDTH+2 cycles after (x,y) itself (empirically verified against an
// independent numpy zero-pad reference model, not just derived by hand —
// see the standalone xsim check this module was built against). This
// module makes that delay UNIFORM for every pixel, including border
// ones, so it can be declared as a plain `latency: {kind: fixed, cycles:
// FRAME_WIDTH+2}` in modules.yml and pass forge.analyze.latency_static's
// real exact_cycle merge check like any other fixed-latency module — no
// bespoke alignment logic needed downstream in edge_mask_merge_rtl.
//
// It does this with two independent, fixed-depth pipelines that always
// advance together (`advance_en`):
//   1. A tag delay line (x/y/frame_id/tile_id/end_of_line/end_of_frame/
//      valid), FRAME_WIDTH+2 deep — output tags are just the original
//      input tags of the pixel this window is centered on, delayed.
//   2. Two line buffers (previous two rows) + 3-wide horizontal shift
//      registers per row, producing the window content itself. These
//      registers are never forced to zero at row boundaries (mid/top
//      need a row's real trailing/leading columns intact for the
//      *next* row's own vertical taps) — instead, the left/right/top
//      border zero-padding is applied combinationally at the output,
//      gated on the center pixel's own already-correctly-delayed tags
//      (out_x/out_y/out_end_of_line). Only the bottom border (last row)
//      is handled structurally, by the phantom-row drain below.
//
// End-of-frame handling: real pixel (x,y)'s window needs row y+1, which
// for the LAST row of a frame never arrives. Rather than stall waiting
// for it forever, this module drains automatically: FRAME_WIDTH+2
// cycles after accepting the frame's last pixel (end_of_frame), it walks
// a phantom all-zero row through the same datapath (states
// ST_FLUSH_COLS / ST_FLUSH_TAIL / ST_FLUSH_TAIL2 below — FRAME_WIDTH
// phantom columns plus 2 tail cycles, matching the tag delay line's own
// depth exactly, so the pipeline is fully drained by the time streaming
// resumes) to emit the last real row's windows with correctly
// zero-padded bottom taps, then clears both line buffers so the next
// frame's own first two rows see zero top taps too (no cross-frame
// contamination).
//
// Scope boundary (documented, not a bug): this module does not expose a
// ready/backpressure port — real backpressure is slice 10.5's job. The
// driving stimulus for this slice must leave at least FRAME_WIDTH+2
// idle cycles between one frame's last pixel and the next frame's
// first, exactly like a video blanking interval, so the end-of-frame
// drain above never overlaps a new frame's real data.
//
// Parameters:
//   WIDTH       - pixel data width (default: 8)
//   FRAME_WIDTH - columns per row; sizes the line buffers and sets the
//                 fixed latency (FRAME_WIDTH+2 cycles) (default: 8)
//==============================================================================

`timescale 1ns / 1ps

module window_builder_rtl #(
    parameter WIDTH       = 8,
    parameter FRAME_WIDTH = 8
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

    // 3x3 window, row-major: win_r{0..2}c{0..2}. r0=row y-1, r2=row y+1;
    // c0=col x-1, c2=col x+1; win_r1c1 is the center pixel itself.
    output wire [WIDTH-1:0]  win_r0c0, win_r0c1, win_r0c2,
    output wire [WIDTH-1:0]  win_r1c0, win_r1c1, win_r1c2,
    output wire [WIDTH-1:0]  win_r2c0, win_r2c1, win_r2c2,

    output wire [11:0]       out_x,
    output wire [11:0]       out_y,
    output wire [15:0]       out_frame_id,
    output wire [15:0]       out_tile_id,
    output wire              out_end_of_line,
    output wire              out_end_of_frame,
    output wire              out_valid
);

    localparam DELAY   = FRAME_WIDTH + 2;
    localparam COL_W   = (FRAME_WIDTH <= 1) ? 1 : $clog2(FRAME_WIDTH);

    localparam ST_STREAM      = 2'd0;
    localparam ST_FLUSH_COLS  = 2'd1;
    localparam ST_FLUSH_TAIL  = 2'd2;
    localparam ST_FLUSH_TAIL2 = 2'd3;

    integer i;

    // ── End-of-frame drain state machine ─────────────────────────────
    reg [1:0]        state;
    reg [COL_W-1:0]  phantom_col;

    wire advance_en = in_valid || (state != ST_STREAM);
    wire lb_touch   = (state == ST_STREAM) ? in_valid : (state == ST_FLUSH_COLS);
    wire [COL_W-1:0] lb_addr = (state == ST_FLUSH_COLS) ? phantom_col
                                                          : x[COL_W-1:0];
    wire [WIDTH-1:0] new_pixel = (state == ST_STREAM) ? normalized_pixel
                                                        : {WIDTH{1'b0}};

    always @(posedge ap_clk) begin
        if (ap_rst) begin
            state       <= ST_STREAM;
            phantom_col <= {COL_W{1'b0}};
        end else begin
            case (state)
                ST_STREAM: begin
                    if (in_valid && end_of_frame) begin
                        state       <= ST_FLUSH_COLS;
                        phantom_col <= {COL_W{1'b0}};
                    end
                end
                ST_FLUSH_COLS: begin
                    if (phantom_col == FRAME_WIDTH[COL_W-1:0] - 1'b1) begin
                        state <= ST_FLUSH_TAIL;
                    end else begin
                        phantom_col <= phantom_col + 1'b1;
                    end
                end
                ST_FLUSH_TAIL: begin
                    state <= ST_FLUSH_TAIL2;
                end
                ST_FLUSH_TAIL2: begin
                    state <= ST_STREAM;
                end
                default: state <= ST_STREAM;
            endcase
        end
    end

    // ── Line buffers (previous two rows) + 3x3 shift registers ──────
    reg [WIDTH-1:0] lb1 [0:FRAME_WIDTH-1];  // row y-1
    reg [WIDTH-1:0] lb2 [0:FRAME_WIDTH-1];  // row y-2

    reg [WIDTH-1:0] top0, top1, top2;  // row y-2, newest..oldest column
    reg [WIDTH-1:0] mid0, mid1, mid2;  // row y-1
    reg [WIDTH-1:0] bot0, bot1, bot2;  // row y (current)

    always @(posedge ap_clk) begin
        if (ap_rst) begin
            for (i = 0; i < FRAME_WIDTH; i = i + 1) begin
                lb1[i] <= {WIDTH{1'b0}};
                lb2[i] <= {WIDTH{1'b0}};
            end
            top0 <= {WIDTH{1'b0}}; top1 <= {WIDTH{1'b0}}; top2 <= {WIDTH{1'b0}};
            mid0 <= {WIDTH{1'b0}}; mid1 <= {WIDTH{1'b0}}; mid2 <= {WIDTH{1'b0}};
            bot0 <= {WIDTH{1'b0}}; bot1 <= {WIDTH{1'b0}}; bot2 <= {WIDTH{1'b0}};
        end else if (advance_en) begin
            if (state == ST_FLUSH_TAIL || state == ST_FLUSH_TAIL2) begin
                // Frame boundary: clear line-buffer history so the next
                // frame's own first two rows see zero top/left taps
                // too, instead of this frame's leftover data.
                for (i = 0; i < FRAME_WIDTH; i = i + 1) begin
                    lb1[i] <= {WIDTH{1'b0}};
                    lb2[i] <= {WIDTH{1'b0}};
                end
                top2 <= top1; top1 <= top0; top0 <= {WIDTH{1'b0}};
                mid2 <= mid1; mid1 <= mid0; mid0 <= {WIDTH{1'b0}};
                bot2 <= bot1; bot1 <= bot0; bot0 <= {WIDTH{1'b0}};
            end else if (lb_touch) begin
                top2 <= top1; top1 <= top0; top0 <= lb2[lb_addr];
                mid2 <= mid1; mid1 <= mid0; mid0 <= lb1[lb_addr];
                bot2 <= bot1; bot1 <= bot0; bot0 <= new_pixel;

                lb2[lb_addr] <= lb1[lb_addr];
                lb1[lb_addr] <= new_pixel;
            end
        end
    end

    // ── Tag delay line: output tags are just the input tags of the
    // pixel this window is centered on, delayed DELAY cycles. Decoupled
    // from the line-buffer datapath above on purpose: only genuinely
    // accepted pixels push a valid tag entry; the flush states above
    // advance this chain (so it keeps draining) without pushing a new
    // one, so the frame's real trailing tags surface at the correct
    // cycle and nothing phantom is ever reported as valid.
    // ──────────────────────────────────────────────────────────────
    reg [11:0] dly_x        [0:DELAY-1];
    reg [11:0] dly_y        [0:DELAY-1];
    reg [15:0] dly_frame_id [0:DELAY-1];
    reg [15:0] dly_tile_id  [0:DELAY-1];
    reg        dly_eol      [0:DELAY-1];
    reg        dly_eof      [0:DELAY-1];
    reg        dly_valid    [0:DELAY-1];

    wire tag_push_valid = in_valid && (state == ST_STREAM);

    always @(posedge ap_clk) begin
        if (ap_rst) begin
            for (i = 0; i < DELAY; i = i + 1) begin
                dly_x[i] <= 12'b0; dly_y[i] <= 12'b0;
                dly_frame_id[i] <= 16'b0; dly_tile_id[i] <= 16'b0;
                dly_eol[i] <= 1'b0; dly_eof[i] <= 1'b0; dly_valid[i] <= 1'b0;
            end
        end else if (advance_en) begin
            for (i = DELAY - 1; i > 0; i = i - 1) begin
                dly_x[i]        <= dly_x[i-1];
                dly_y[i]        <= dly_y[i-1];
                dly_frame_id[i] <= dly_frame_id[i-1];
                dly_tile_id[i]  <= dly_tile_id[i-1];
                dly_eol[i]      <= dly_eol[i-1];
                dly_eof[i]      <= dly_eof[i-1];
                dly_valid[i]    <= dly_valid[i-1];
            end
            dly_x[0]        <= x;
            dly_y[0]        <= y;
            dly_frame_id[0] <= frame_id;
            dly_tile_id[0]  <= tile_id;
            dly_eol[0]      <= end_of_line;
            dly_eof[0]      <= end_of_frame;
            dly_valid[0]    <= tag_push_valid;
        end
    end

    assign out_x            = dly_x[DELAY-1];
    assign out_y             = dly_y[DELAY-1];
    assign out_frame_id      = dly_frame_id[DELAY-1];
    assign out_tile_id       = dly_tile_id[DELAY-1];
    assign out_end_of_line   = dly_eol[DELAY-1];
    assign out_end_of_frame  = dly_eof[DELAY-1];
    assign out_valid         = dly_valid[DELAY-1];

    // ── Horizontal/top border masking ────────────────────────────────
    // top0/mid0/bot0/top2/mid2/bot2 above are plain, unforced
    // shift-register taps: they hold whatever real data streamed past,
    // which for a border window is a real neighboring row's
    // trailing/leading columns, not the zero this window's center is
    // entitled to (a row's own horizontal history can't be reset
    // between rows -- mid/top need it intact for future rows' vertical
    // taps). So the zero-padding for the left/right/top borders is
    // applied here instead, combinationally, gated on the CENTER
    // pixel's own already-correctly-delayed tags (out_x/out_y/
    // out_end_of_line) -- never on the raw tap registers themselves.
    // The bottom border (last row) is handled structurally instead, by
    // the phantom all-zero row ST_FLUSH_COLS walks through the same
    // registers above.
    wire mask_left  = (out_x == 12'd0);
    wire mask_right = out_end_of_line;
    wire mask_top   = (out_y == 12'd0);

    assign win_r0c0 = (mask_top || mask_left)  ? {WIDTH{1'b0}} : top2;
    assign win_r0c1 = mask_top                 ? {WIDTH{1'b0}} : top1;
    assign win_r0c2 = (mask_top || mask_right) ? {WIDTH{1'b0}} : top0;
    assign win_r1c0 = mask_left                ? {WIDTH{1'b0}} : mid2;
    assign win_r1c1 = mid1;
    assign win_r1c2 = mask_right               ? {WIDTH{1'b0}} : mid0;
    assign win_r2c0 = mask_left                ? {WIDTH{1'b0}} : bot2;
    assign win_r2c1 = bot1;
    assign win_r2c2 = mask_right               ? {WIDTH{1'b0}} : bot0;

endmodule
