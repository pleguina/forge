#include "sobel_hls.h"

void sobel_hls(
    pixel_t     win_r0c0, pixel_t win_r0c1, pixel_t win_r0c2,
    pixel_t     win_r1c0, pixel_t win_r1c1, pixel_t win_r1c2,
    pixel_t     win_r2c0, pixel_t win_r2c1, pixel_t win_r2c2,
    coord_t     x,
    coord_t     y,
    frame_id_t  frame_id,
    tile_id_t   tile_id,
    flag_t      end_of_line,
    flag_t      end_of_frame,
    flag_t      in_valid,
    magnitude_t &gradient_magnitude,
    coord_t     &x_out,
    coord_t     &y_out,
    frame_id_t  &frame_id_out,
    tile_id_t   &tile_id_out,
    flag_t      &end_of_line_out,
    flag_t      &end_of_frame_out,
    flag_t      &out_valid
) {
#pragma HLS PIPELINE II=1
// LATENCY min=4 max=4 — a real fixed latency (same pattern
// pixel_normalizer.cpp/trigger_logic.cpp already establish): two
// 6-term weighted sums, an abs()+add(), then a saturating clamp, is
// meaningfully more arithmetic than the normalizer's single
// scale+offset+clamp (3 cycles), so 4 is the honest pipeline depth
// this forces, not a copy-pasted number.
#pragma HLS LATENCY min=4 max=4
#pragma HLS INTERFACE ap_ctrl_none port=return
#pragma HLS INTERFACE ap_none port=win_r0c0
#pragma HLS INTERFACE ap_none port=win_r0c1
#pragma HLS INTERFACE ap_none port=win_r0c2
#pragma HLS INTERFACE ap_none port=win_r1c0
#pragma HLS INTERFACE ap_none port=win_r1c1
#pragma HLS INTERFACE ap_none port=win_r1c2
#pragma HLS INTERFACE ap_none port=win_r2c0
#pragma HLS INTERFACE ap_none port=win_r2c1
#pragma HLS INTERFACE ap_none port=win_r2c2
#pragma HLS INTERFACE ap_none port=x
#pragma HLS INTERFACE ap_none port=y
#pragma HLS INTERFACE ap_none port=frame_id
#pragma HLS INTERFACE ap_none port=tile_id
#pragma HLS INTERFACE ap_none port=end_of_line
#pragma HLS INTERFACE ap_none port=end_of_frame
#pragma HLS INTERFACE ap_none port=in_valid
#pragma HLS INTERFACE ap_none port=gradient_magnitude
#pragma HLS INTERFACE ap_none port=x_out
#pragma HLS INTERFACE ap_none port=y_out
#pragma HLS INTERFACE ap_none port=frame_id_out
#pragma HLS INTERFACE ap_none port=tile_id_out
#pragma HLS INTERFACE ap_none port=end_of_line_out
#pragma HLS INTERFACE ap_none port=end_of_frame_out
#pragma HLS INTERFACE ap_none port=out_valid

    // Standard Sobel kernel:
    //   Gx = -r0c0 + r0c2 - 2*r1c0 + 2*r1c2 - r2c0 + r2c2
    //   Gy = -r0c0 - 2*r0c1 - r0c2 + r2c0 + 2*r2c1 + r2c2
    // Signed intermediates (never exposed on any interface); bounded by
    // +/-1020 for 8-bit pixel taps, comfortably within ap_int<16>.
    ap_int<16> gx =
        - ap_int<16>(win_r0c0) + ap_int<16>(win_r0c2)
        - 2 * ap_int<16>(win_r1c0) + 2 * ap_int<16>(win_r1c2)
        - ap_int<16>(win_r2c0) + ap_int<16>(win_r2c2);

    ap_int<16> gy =
        - ap_int<16>(win_r0c0) - 2 * ap_int<16>(win_r0c1) - ap_int<16>(win_r0c2)
        + ap_int<16>(win_r2c0) + 2 * ap_int<16>(win_r2c1) + ap_int<16>(win_r2c2);

    ap_int<16> abs_gx = (gx < 0) ? ap_int<16>(-gx) : gx;
    ap_int<16> abs_gy = (gy < 0) ? ap_int<16>(-gy) : gy;
    ap_int<16> sum    = abs_gx + abs_gy;

    // Saturate to gradient_magnitude's 12-bit unsigned range -- a
    // safety net, not a normal-operation branch: the standard Sobel
    // kernel on 8-bit pixels bounds |Gx|+|Gy| to 2040, well under 4095.
    if (sum > 4095) {
        gradient_magnitude = 4095;
    } else {
        gradient_magnitude = magnitude_t(sum);
    }

    x_out            = x;
    y_out             = y;
    frame_id_out      = frame_id;
    tile_id_out       = tile_id;
    end_of_line_out   = end_of_line;
    end_of_frame_out  = end_of_frame;
    out_valid         = in_valid;
}
