#ifndef SOBEL_HLS_H
#define SOBEL_HLS_H

#include <ap_int.h>

// forge.pixel_stream.v1-shaped field widths,
// plus gradient_magnitude's own dedicated width.
typedef ap_uint<8>  pixel_t;
typedef ap_uint<12> coord_t;
typedef ap_uint<16> frame_id_t;
typedef ap_uint<16> tile_id_t;
typedef ap_uint<1>  flag_t;
typedef ap_uint<12> magnitude_t;

// sobel_hls: a
// small, stateless 3x3-compute HLS module. window_builder_rtl (RTL,
// upstream) already owns the line buffers, border zero-padding, and
// N-dimensional physical binding -- this module only computes
// gradient_magnitude = clamp(abs(Gx) + abs(Gy), 0, 4095) from the 9
// window taps it's handed, using the
// standard Sobel kernel:
//
//   Gx = [-1 0 1; -2 0 2; -1 0 1]   Gy = [-1 -2 -1; 0 0 0; 1 2 1]
//
// win_r1c1 (the center tap) has coefficient 0 in both kernels and is
// unused here -- it's exposed by window_builder_rtl for genericity, not
// because Sobel needs it.
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
);

#endif // SOBEL_HLS_H
