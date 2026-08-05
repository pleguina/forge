#ifndef PIXEL_NORMALIZER_H
#define PIXEL_NORMALIZER_H

#include <ap_int.h>

// Fixed-point scale/offset applied to the incoming pixel, then saturated
// to an 8-bit range. Compile-time constants — this module does not
// implement config-mailbox-driven runtime configuration (that pattern
// is established elsewhere, e.g. threshold_configurable_rtl.v).
#define NORMALIZER_SCALE_NUM 3
#define NORMALIZER_SCALE_DEN 2
#define NORMALIZER_OFFSET    (-64)

// forge.pixel_stream.v1-shaped field widths.
typedef ap_uint<8>  pixel_t;
typedef ap_uint<12> coord_t;
typedef ap_uint<16> frame_id_t;
typedef ap_uint<16> tile_id_t;
typedef ap_uint<1>  flag_t;

// pixel_normalizer — quickstart-tier HLS module:
// normalized = clamp(scale*pixel + offset, 0, 255).
// Every other forge.pixel_stream.v1 field is passed through unchanged,
// aligned by the same PIPELINE/LATENCY-driven register stages the
// normalized_pixel output goes through (see pixel_normalizer.cpp).
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
);

#endif // PIXEL_NORMALIZER_H
