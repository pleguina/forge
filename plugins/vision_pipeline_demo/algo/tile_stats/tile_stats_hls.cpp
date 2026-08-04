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
    static stat_t      run_min     = 255;
    static stat_t      run_max     = 0;
    static ap_uint<16> run_sum     = 0;
    static ap_uint<24> run_sumsq_p0 = 0;
    static ap_uint<24> run_sumsq_p1 = 0;
    static ap_uint<24> run_sumsq_p2 = 0;
    static ap_uint<24> run_sumsq_p3 = 0;
    static ap_uint<7>  run_count   = 0;
#pragma HLS RESET variable=run_min
#pragma HLS RESET variable=run_max
#pragma HLS RESET variable=run_sum
#pragma HLS RESET variable=run_sumsq_p0
#pragma HLS RESET variable=run_sumsq_p1
#pragma HLS RESET variable=run_sumsq_p2
#pragma HLS RESET variable=run_sumsq_p3
#pragma HLS RESET variable=run_count

    // Tile-cell-local completion test (tile_stats_hls.h, frozen 8x8
    // geometry, row-major traversal) -- true regardless of how many
    // tiles a frame has; this slice's scope is single-tile-column
    // frames (FRAME_WIDTH == TILE_WIDTH == 8), see the header comment.
    bool is_last_row = ((y % TILE_HEIGHT) == (TILE_HEIGHT - 1));
    bool is_last_col = ((x % TILE_WIDTH)  == (TILE_WIDTH  - 1));
    bool tile_last   = in_valid && is_last_row && is_last_col;
    bool count_reset = (run_count == 0);

    pixel_t new_min = (count_reset || normalized_pixel < run_min) ? normalized_pixel : run_min;
    pixel_t new_max = (count_reset || normalized_pixel > run_max) ? normalized_pixel : run_max;
    ap_uint<16> new_sum = (count_reset ? ap_uint<16>(0) : run_sum) + ap_uint<16>(normalized_pixel);

    // Sum-of-squares: 4 round-robin partial accumulators (see header for
    // why) -- `part_idx` selects which partial this sample updates,
    // `part_first_touch` is true exactly on that partial's first use
    // this tile (samples 0-3 each touch a different partial for the
    // first time; every later multiple-of-4 sample reuses one).
    ap_uint<2> part_idx         = run_count(1, 0);
    bool       part_first_touch = (run_count(6, 2) == 0);
    ap_uint<24> square = ap_uint<24>(normalized_pixel) * ap_uint<24>(normalized_pixel);
    ap_uint<24> old_partial =
        (part_idx == ap_uint<2>(0)) ? run_sumsq_p0 :
        (part_idx == ap_uint<2>(1)) ? run_sumsq_p1 :
        (part_idx == ap_uint<2>(2)) ? run_sumsq_p2 : run_sumsq_p3;
    ap_uint<24> new_partial = (part_first_touch ? ap_uint<24>(0) : old_partial) + square;
    // Force the accumulate-add off the DSP48 the multiply above binds
    // to -- see header item (1).
#pragma HLS BIND_OP variable=new_partial op=add impl=fabric

    ap_uint<24> p0 = (part_idx == ap_uint<2>(0)) ? new_partial : run_sumsq_p0;
    ap_uint<24> p1 = (part_idx == ap_uint<2>(1)) ? new_partial : run_sumsq_p1;
    ap_uint<24> p2 = (part_idx == ap_uint<2>(2)) ? new_partial : run_sumsq_p2;
    ap_uint<24> p3 = (part_idx == ap_uint<2>(3)) ? new_partial : run_sumsq_p3;
    ap_uint<24> total_sumsq = p0 + p1 + p2 + p3;

    // Population mean/variance, exact power-of-two shift, no rounding
    // mode (preflight.md §9.1, frozen): mean = sum>>6, variance =
    // max(0, (sumsq>>6) - mean^2). Computed every sample now (see
    // header item (2)), only the final gated write below cares whether
    // this sample is really the tile's last.
    mean_t computed_mean = mean_t(new_sum >> 6);
    ap_uint<32> mean_sq  = ap_uint<32>(computed_mean) * ap_uint<32>(computed_mean);
    ap_uint<32> sumsq_shifted = ap_uint<32>(total_sumsq >> 6);
    ap_uint<32> computed_variance =
        (sumsq_shifted > mean_sq) ? ap_uint<32>(sumsq_shifted - mean_sq) : ap_uint<32>(0);

    // Gated persistent-state updates: hold unchanged when !in_valid;
    // the accumulators reset (via count_reset/part_first_touch above)
    // on the sample *after* a tile completes, same as the original
    // branching version's own `run_count = 0` on `tile_last`.
    run_min      = in_valid ? new_min : run_min;
    run_max      = in_valid ? new_max : run_max;
    run_sum      = in_valid ? new_sum : run_sum;
    run_sumsq_p0 = in_valid ? p0 : run_sumsq_p0;
    run_sumsq_p1 = in_valid ? p1 : run_sumsq_p1;
    run_sumsq_p2 = in_valid ? p2 : run_sumsq_p2;
    run_sumsq_p3 = in_valid ? p3 : run_sumsq_p3;
    run_count    = in_valid ? (tile_last ? ap_uint<7>(0) : ap_uint<7>(run_count + 1)) : run_count;

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
