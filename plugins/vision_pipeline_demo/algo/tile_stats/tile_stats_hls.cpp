#include "tile_stats_hls.h"

void tile_stats_hls(
    pixel_t     normalized_pixel,
    coord_t     x,
    coord_t     y,
    frame_id_t  frame_id,
    tile_id_t   tile_id,
    flag_t      end_of_line,
    flag_t      end_of_frame,
    flag_t      in_valid,
    stat_t      &minimum,
    stat_t      &maximum,
    mean_t      &mean,
    variance_t  &variance,
    tile_id_t   &tile_id_out,
    frame_id_t  &frame_id_out,
    flag_t      &out_valid
) {
#pragma HLS PIPELINE II=1
// Real II=1 (release-plan Phase 10, slice 10.5 follow-up -- was II=2;
// see git history for the original single-accumulator, branching
// version and its own real-not-assumed derivation). Two real,
// empirically-confirmed obstacles had to be removed together, not one:
//
// (1) `run_sumsq`'s multiply-accumulate (normalized_pixel^2, summed
//     into itself every sample) originally bound to a DSP48 MACC with a
//     real 3-cycle internal latency -- a *single* loop-carried
//     accumulator can't close that loop faster than II=2. Fixed by
//     splitting into 4 named-scalar partial accumulators (>= the
//     DSP48's own 3-cycle latency, matching the frozen 64-sample tile
//     evenly), cycled round-robin by sample index, each getting 4
//     cycles of slack between its own updates -- plus forcing the
//     accumulate-add itself off the DSP48 (`#pragma HLS BIND_OP ...
//     impl=fabric`, below), since Vitis fuses a multiply-then-add into
//     one DSP MACC by default, which re-ties the add to the same
//     multi-cycle pipeline stage the split was meant to escape. An
//     ARRAY_PARTITION-complete array with a variable index was tried
//     first for the 4 partials -- still II=2, because Vitis's
//     dependence analysis for a variable-indexed *static* array inside
//     a PIPELINE-only function (no real `for` loop with a
//     compile-time-provable index pattern) stays conservative and
//     serializes all 4 elements as one group regardless of partitioning.
//     Four independent named registers, each with its own simple
//     self-mux update expression, gave the scheduler nothing to
//     disambiguate.
// (2) Even with (1) fixed, II stayed at 2 -- confirmed by inspecting the
//     real schedule report and finding every operation involved was
//     already 0-latency combinational logic, just spread across 5 FSM
//     states purely by the function's own control flow (`if (in_valid)
//     { ... if (tile_last) {...} else {...} }` creates separate basic
//     blocks). The real second obstacle was the branching itself, not
//     any arithmetic -- confirmed by rewriting the whole body as
//     branch-free straight-line dataflow (every conditional expressed
//     as a select, never as an `if` altering control flow) and getting
//     a real, clean II=1 back. `minimum`/`maximum`/`mean`/`variance`/
//     `tile_id_out`/`frame_id_out`/`out_valid` are therefore now always
//     *computed*, and only *gated* to their real value vs. 0 by a final
//     select on `(in_valid && tile_last)` -- computing the mean/
//     variance closed form on every sample (not just tile-last ones) is
//     extra combinational work, but it's cheap (one more mul/shift/sub)
//     next to the real win of removing the branch-driven state split.
//
// Slice 10.7A extends this to MAX_TILE_COLS concurrent tile-column
// register banks (tile_stats_hls.h), selected by a plain mux on `x`'s
// TILE_COL_BIT -- not a branch, so it does not reintroduce obstacle (2)
// above. Real II/latency re-confirmed against this extended body's own
// csynth report, not assumed identical to the single-column version's.
#pragma HLS LATENCY min=1 max=1
#pragma HLS INTERFACE ap_ctrl_none port=return
#pragma HLS INTERFACE ap_none port=normalized_pixel
#pragma HLS INTERFACE ap_none port=x
#pragma HLS INTERFACE ap_none port=y
#pragma HLS INTERFACE ap_none port=frame_id
#pragma HLS INTERFACE ap_none port=tile_id
#pragma HLS INTERFACE ap_none port=end_of_line
#pragma HLS INTERFACE ap_none port=end_of_frame
#pragma HLS INTERFACE ap_none port=in_valid
#pragma HLS INTERFACE ap_none port=minimum
#pragma HLS INTERFACE ap_none port=maximum
#pragma HLS INTERFACE ap_none port=mean
#pragma HLS INTERFACE ap_none port=variance
#pragma HLS INTERFACE ap_none port=tile_id_out
#pragma HLS INTERFACE ap_none port=frame_id_out
#pragma HLS INTERFACE ap_none port=out_valid

    // Persistent accumulator state across invocations (the standard
    // Vitis HLS idiom for a streaming statistics core under PIPELINE --
    // these map to real registers in the synthesized RTL, not C-sim-only
    // storage). Widths sized against the frozen 8x8 tile (64 samples of
    // an 8-bit pixel): sum <= 64*255 = 16320 (15 bits), each of the 4
    // sum_sq partials <= 16*255^2 = 1,040,400 (21 bits, comfortably
    // inside the declared 24-bit width).
    //
    // Slice 10.7A: two independent NAMED copies of the whole register
    // set (col0_*/col1_*), one per concurrent tile column
    // (tile_stats_hls.h's MAX_TILE_COLS/TILE_COL_BIT) -- never a real
    // `[MAX_TILE_COLS]` array. The sum-of-squares split above already
    // found (slice 10.5 follow-up) that a variable-indexed *static*
    // array inside a PIPELINE-only function gives Vitis's dependence
    // analysis nothing to disambiguate and it serializes the whole
    // group regardless of ARRAY_PARTITION; two named copies, muxed by a
    // plain select, sidestep that the same way the 4-way partial split
    // already did.
    static stat_t      col0_min       = 255, col1_min       = 255;
    static stat_t      col0_max       = 0,   col1_max       = 0;
    static ap_uint<16> col0_sum       = 0,   col1_sum       = 0;
    static ap_uint<24> col0_sumsq_p0  = 0,   col1_sumsq_p0  = 0;
    static ap_uint<24> col0_sumsq_p1  = 0,   col1_sumsq_p1  = 0;
    static ap_uint<24> col0_sumsq_p2  = 0,   col1_sumsq_p2  = 0;
    static ap_uint<24> col0_sumsq_p3  = 0,   col1_sumsq_p3  = 0;
    static ap_uint<7>  col0_count     = 0,   col1_count     = 0;
#pragma HLS RESET variable=col0_min
#pragma HLS RESET variable=col1_min
#pragma HLS RESET variable=col0_max
#pragma HLS RESET variable=col1_max
#pragma HLS RESET variable=col0_sum
#pragma HLS RESET variable=col1_sum
#pragma HLS RESET variable=col0_sumsq_p0
#pragma HLS RESET variable=col1_sumsq_p0
#pragma HLS RESET variable=col0_sumsq_p1
#pragma HLS RESET variable=col1_sumsq_p1
#pragma HLS RESET variable=col0_sumsq_p2
#pragma HLS RESET variable=col1_sumsq_p2
#pragma HLS RESET variable=col0_sumsq_p3
#pragma HLS RESET variable=col1_sumsq_p3
#pragma HLS RESET variable=col0_count
#pragma HLS RESET variable=col1_count

    // Tile-cell-local completion test (tile_stats_hls.h, frozen 8x8
    // geometry, row-major traversal) -- true regardless of how many
    // tiles a frame has, since it's a purely local per-tile-cell test.
    bool is_last_row = ((y % TILE_HEIGHT) == (TILE_HEIGHT - 1));
    bool is_last_col = ((x % TILE_WIDTH)  == (TILE_WIDTH  - 1));
    bool tile_last   = in_valid && is_last_row && is_last_col;

    // Which tile column this sample belongs to -- a plain select, read
    // once, used to mux every accumulator read/write below (never a
    // branch; see item (2) in the 10.5 follow-up header note on why
    // control flow, not arithmetic, was the real II obstacle there).
    bool col1_sel = (x[TILE_COL_BIT] != 0);

    stat_t      sel_min   = col1_sel ? col1_min       : col0_min;
    stat_t      sel_max   = col1_sel ? col1_max       : col0_max;
    ap_uint<16> sel_sum   = col1_sel ? col1_sum       : col0_sum;
    ap_uint<7>  sel_count = col1_sel ? col1_count     : col0_count;
    ap_uint<24> sel_p0    = col1_sel ? col1_sumsq_p0  : col0_sumsq_p0;
    ap_uint<24> sel_p1    = col1_sel ? col1_sumsq_p1  : col0_sumsq_p1;
    ap_uint<24> sel_p2    = col1_sel ? col1_sumsq_p2  : col0_sumsq_p2;
    ap_uint<24> sel_p3    = col1_sel ? col1_sumsq_p3  : col0_sumsq_p3;

    bool count_reset = (sel_count == 0);

    pixel_t new_min = (count_reset || normalized_pixel < sel_min) ? normalized_pixel : sel_min;
    pixel_t new_max = (count_reset || normalized_pixel > sel_max) ? normalized_pixel : sel_max;
    ap_uint<16> new_sum = (count_reset ? ap_uint<16>(0) : sel_sum) + ap_uint<16>(normalized_pixel);

    // Sum-of-squares: 4 round-robin partial accumulators per tile
    // column (see the class header for why) -- `part_idx` selects
    // which partial this sample updates, `part_first_touch` is true
    // exactly on that partial's first use this tile (samples 0-3 each
    // touch a different partial for the first time; every later
    // multiple-of-4 sample reuses one).
    ap_uint<2> part_idx         = sel_count(1, 0);
    bool       part_first_touch = (sel_count(6, 2) == 0);
    ap_uint<24> square = ap_uint<24>(normalized_pixel) * ap_uint<24>(normalized_pixel);
    ap_uint<24> old_partial =
        (part_idx == ap_uint<2>(0)) ? sel_p0 :
        (part_idx == ap_uint<2>(1)) ? sel_p1 :
        (part_idx == ap_uint<2>(2)) ? sel_p2 : sel_p3;
    ap_uint<24> new_partial = (part_first_touch ? ap_uint<24>(0) : old_partial) + square;
    // Force the accumulate-add off the DSP48 the multiply above binds
    // to -- see the 10.5 follow-up header note, item (1).
#pragma HLS BIND_OP variable=new_partial op=add impl=fabric

    ap_uint<24> p0 = (part_idx == ap_uint<2>(0)) ? new_partial : sel_p0;
    ap_uint<24> p1 = (part_idx == ap_uint<2>(1)) ? new_partial : sel_p1;
    ap_uint<24> p2 = (part_idx == ap_uint<2>(2)) ? new_partial : sel_p2;
    ap_uint<24> p3 = (part_idx == ap_uint<2>(3)) ? new_partial : sel_p3;
    ap_uint<24> total_sumsq = p0 + p1 + p2 + p3;

    // Population mean/variance, exact power-of-two shift, no rounding
    // mode (preflight.md §9.1, frozen): mean = sum>>6, variance =
    // max(0, (sumsq>>6) - mean^2). Computed every sample (see the 10.5
    // follow-up header note, item (2)), only the final gated write
    // below cares whether this sample is really its tile's last.
    mean_t computed_mean = mean_t(new_sum >> 6);
    ap_uint<32> mean_sq  = ap_uint<32>(computed_mean) * ap_uint<32>(computed_mean);
    ap_uint<32> sumsq_shifted = ap_uint<32>(total_sumsq >> 6);
    ap_uint<32> computed_variance =
        (sumsq_shifted > mean_sq) ? ap_uint<32>(sumsq_shifted - mean_sq) : ap_uint<32>(0);

    // Gated persistent-state updates: hold unchanged when !in_valid;
    // written back to whichever column this sample belongs to only
    // (the other column's registers pass through unchanged) -- the
    // accumulators reset (via count_reset/part_first_touch above) on
    // the sample *after* that same tile column's own tile completes.
    bool write_col0 = in_valid && !col1_sel;
    bool write_col1 = in_valid && col1_sel;
    ap_uint<7> next_count = tile_last ? ap_uint<7>(0) : ap_uint<7>(sel_count + 1);

    col0_min      = write_col0 ? new_min : col0_min;
    col1_min      = write_col1 ? new_min : col1_min;
    col0_max      = write_col0 ? new_max : col0_max;
    col1_max      = write_col1 ? new_max : col1_max;
    col0_sum      = write_col0 ? new_sum : col0_sum;
    col1_sum      = write_col1 ? new_sum : col1_sum;
    col0_sumsq_p0 = write_col0 ? p0 : col0_sumsq_p0;
    col1_sumsq_p0 = write_col1 ? p0 : col1_sumsq_p0;
    col0_sumsq_p1 = write_col0 ? p1 : col0_sumsq_p1;
    col1_sumsq_p1 = write_col1 ? p1 : col1_sumsq_p1;
    col0_sumsq_p2 = write_col0 ? p2 : col0_sumsq_p2;
    col1_sumsq_p2 = write_col1 ? p2 : col1_sumsq_p2;
    col0_sumsq_p3 = write_col0 ? p3 : col0_sumsq_p3;
    col1_sumsq_p3 = write_col1 ? p3 : col1_sumsq_p3;
    col0_count    = write_col0 ? next_count : col0_count;
    col1_count    = write_col1 ? next_count : col1_count;

    // Gated outputs: real value only on the real tile-last sample, 0
    // otherwise (matching the original version's own explicit
    // top-of-function zero defaults).
    minimum      = tile_last ? new_min : stat_t(0);
    maximum      = tile_last ? new_max : stat_t(0);
    mean         = tile_last ? computed_mean : mean_t(0);
    variance     = tile_last ? variance_t(computed_variance) : variance_t(0);
    tile_id_out  = tile_last ? tile_id : tile_id_t(0);
    frame_id_out = tile_last ? frame_id : frame_id_t(0);
    out_valid    = tile_last ? flag_t(1) : flag_t(0);
}
