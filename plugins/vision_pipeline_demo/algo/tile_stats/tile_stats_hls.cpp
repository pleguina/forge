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
#pragma HLS PIPELINE II=2
// LATENCY=6 -- a real fixed latency, empirically confirmed against a
// real Vitis HLS synthesis report, NOT the spec's own placeholder
// "bounded 6..10" figure (spec §11/§15.2) copied in uncorrected. First
// attempted as `LATENCY min=6 max=10` on the theory that the tile's
// *last* sample (which additionally walks the mean/variance closed
// form -- shift, square, subtract, clamp) would schedule to a real,
// different depth than a plain accumulate-only sample; the real
// csynth report instead showed min==max==6 (both control paths get
// folded into one static II=2-pipelined schedule with muxes, the usual
// behavior for a #pragma HLS PIPELINE function with no genuinely
// variable-length execution, e.g. no memory-latency-bound stall) --
// see modules.yml's tile_stats_hls entry, corrected to `kind: fixed,
// cycles: 6` to match. The declared-bounded architecture downstream
// (tile_boundary_rtl's held tag / tile_summary_join_rtl's tile_id-based
// match, not cycle-position) needed no change: it was never built
// assuming a specific tile_stats_hls latency kind, by design.
#pragma HLS LATENCY min=6 max=6
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
    // an 8-bit pixel): sum <= 64*255 = 16320 (15 bits), sum_sq <=
    // 64*255^2 = 4,161,600 (23 bits) -- both comfortably inside the
    // declared widths below with headroom to spare.
    static stat_t      run_min     = 255;
    static stat_t      run_max     = 0;
    static ap_uint<16> run_sum     = 0;
    static ap_uint<24> run_sumsq   = 0;
    static ap_uint<7>  run_count   = 0;
    static tile_id_t   run_tile_id = 0;
    static frame_id_t  run_frame_id = 0;
#pragma HLS RESET variable=run_min
#pragma HLS RESET variable=run_max
#pragma HLS RESET variable=run_sum
#pragma HLS RESET variable=run_sumsq
#pragma HLS RESET variable=run_count
#pragma HLS RESET variable=run_tile_id
#pragma HLS RESET variable=run_frame_id

    minimum      = 0;
    maximum      = 0;
    mean         = 0;
    variance     = 0;
    tile_id_out  = 0;
    frame_id_out = 0;
    out_valid    = 0;

    if (in_valid) {
        // Tile-cell-local completion test (tile_stats_hls.h, frozen
        // 8x8 geometry, row-major traversal) -- true regardless of how
        // many tiles a frame has; this slice's scope is single-tile-
        // column frames (FRAME_WIDTH == TILE_WIDTH == 8), see the
        // header comment.
        bool is_last_row = ((y % TILE_HEIGHT) == (TILE_HEIGHT - 1));
        bool is_last_col = ((x % TILE_WIDTH)  == (TILE_WIDTH  - 1));
        bool tile_last   = is_last_row && is_last_col;

        pixel_t new_min = (run_count == 0 || normalized_pixel < run_min) ? normalized_pixel : run_min;
        pixel_t new_max = (run_count == 0 || normalized_pixel > run_max) ? normalized_pixel : run_max;
        ap_uint<16> new_sum   = (run_count == 0 ? ap_uint<16>(0) : run_sum)
                               + ap_uint<16>(normalized_pixel);
        ap_uint<24> new_sumsq = (run_count == 0 ? ap_uint<24>(0) : run_sumsq)
                               + ap_uint<24>(normalized_pixel) * ap_uint<24>(normalized_pixel);

        run_min      = new_min;
        run_max      = new_max;
        run_sum      = new_sum;
        run_sumsq    = new_sumsq;
        run_tile_id  = tile_id;
        run_frame_id = frame_id;

        if (tile_last) {
            // Population mean/variance, exact power-of-two shift, no
            // rounding mode (preflight.md §9.1, frozen): mean = sum>>6,
            // variance = max(0, (sumsq>>6) - mean^2).
            mean_t computed_mean = mean_t(new_sum >> 6);
            ap_uint<32> mean_sq  = ap_uint<32>(computed_mean) * ap_uint<32>(computed_mean);
            ap_uint<32> sumsq_shifted = ap_uint<32>(new_sumsq >> 6);
            ap_uint<32> computed_variance =
                (sumsq_shifted > mean_sq) ? ap_uint<32>(sumsq_shifted - mean_sq) : ap_uint<32>(0);

            minimum      = new_min;
            maximum      = new_max;
            mean         = computed_mean;
            variance     = variance_t(computed_variance);
            tile_id_out  = tile_id;
            frame_id_out = frame_id;
            out_valid    = 1;

            // Tile complete -- reset the accumulator so the next tile's
            // first sample starts a fresh window (count==0 branch above).
            run_count = 0;
        } else {
            run_count = run_count + 1;
        }
    }
}
