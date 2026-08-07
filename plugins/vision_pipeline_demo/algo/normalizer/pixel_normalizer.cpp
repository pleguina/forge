#include "pixel_normalizer.h"

void pixel_normalizer(
    pixel_t     pixel,
    coord_t     x,
    coord_t     y,
    frame_id_t  frame_id,
    tile_id_t   tile_id,
    flag_t      end_of_line,
    flag_t      end_of_frame,
    flag_t      pixel_valid,
    pixel_t     &normalized_pixel,
    coord_t     &x_out,
    coord_t     &y_out,
    frame_id_t  &frame_id_out,
    tile_id_t   &tile_id_out,
    flag_t      &end_of_line_out,
    flag_t      &end_of_frame_out,
    flag_t      &normalized_valid
) {
#pragma HLS PIPELINE II=1
// LATENCY=3 forces HLS to insert 3 pipeline register stages in RTL,
// making ap_clk visible in the synthesised Verilog — the same pattern
// plugins/trigger_demo/algo/
// trigger_logic/trigger_logic.cpp already establishes. C-sim is
// unaffected (LATENCY is an RTL-synthesis-only constraint) — the golden
// model this module is checked against (both the Python
// GoldenModelProvider driving RTL-level stimulus, and this same
// closed-form logic re-implemented directly in the C-sim testbench)
// computes the identical formula with no notion of pipeline depth.
#pragma HLS LATENCY min=3 max=3
#pragma HLS INTERFACE ap_ctrl_none port=return
#pragma HLS INTERFACE ap_none port=pixel
#pragma HLS INTERFACE ap_none port=x
#pragma HLS INTERFACE ap_none port=y
#pragma HLS INTERFACE ap_none port=frame_id
#pragma HLS INTERFACE ap_none port=tile_id
#pragma HLS INTERFACE ap_none port=end_of_line
#pragma HLS INTERFACE ap_none port=end_of_frame
#pragma HLS INTERFACE ap_none port=pixel_valid
#pragma HLS INTERFACE ap_none port=normalized_pixel
#pragma HLS INTERFACE ap_none port=x_out
#pragma HLS INTERFACE ap_none port=y_out
#pragma HLS INTERFACE ap_none port=frame_id_out
#pragma HLS INTERFACE ap_none port=tile_id_out
#pragma HLS INTERFACE ap_none port=end_of_line_out
#pragma HLS INTERFACE ap_none port=end_of_frame_out
#pragma HLS INTERFACE ap_none port=normalized_valid

    // normalized = clamp(scale*pixel + offset, 0, 255) — signed
    // intermediate (never exposed on any interface), unsigned,
    // saturated result (every external/interface field on this module
    // is unsigned, so the signed working value never leaks past the
    // clamp).
    ap_int<16> scaled =
        (ap_int<16>(pixel) * NORMALIZER_SCALE_NUM) / NORMALIZER_SCALE_DEN
        + NORMALIZER_OFFSET;

    if (scaled < 0) {
        normalized_pixel = 0;
    } else if (scaled > 255) {
        normalized_pixel = 255;
    } else {
        normalized_pixel = pixel_t(scaled);
    }

    // Every other field is passed through unchanged, aligned by the same
    // PIPELINE/LATENCY register stages as normalized_pixel above.
    x_out            = x;
    y_out             = y;
    frame_id_out      = frame_id;
    tile_id_out       = tile_id;
    end_of_line_out   = end_of_line;
    end_of_frame_out  = end_of_frame;
    normalized_valid  = pixel_valid;
}
