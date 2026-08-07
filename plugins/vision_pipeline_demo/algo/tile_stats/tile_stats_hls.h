#ifndef TILE_STATS_HLS_H
#define TILE_STATS_HLS_H

#include <ap_int.h>

// forge.pixel_stream.v1-shaped field widths,
// plus the tile-statistics record's own field widths
// (minimum{8}, maximum{8}, mean{16}, variance{24}) -- this record layout is
// fixed by docs/development/adr/0003-vision-packet-format.md.
typedef ap_uint<8>  pixel_t;
typedef ap_uint<12> coord_t;
typedef ap_uint<16> frame_id_t;
typedef ap_uint<16> tile_id_t;
typedef ap_uint<1>  flag_t;
typedef ap_uint<8>  stat_t;
typedef ap_uint<16> mean_t;
typedef ap_uint<24> variance_t;

// Tile geometry: 8x8 tiles, row-major
// raster traversal. A pixel at (x,y) is the last sample of its own tile
// iff (x % TILE_WIDTH == TILE_WIDTH-1) && (y % TILE_HEIGHT ==
// TILE_HEIGHT-1) -- true regardless of how many tiles a frame has,
// since it's a purely local per-tile-cell test.
#define TILE_WIDTH  8
#define TILE_HEIGHT 8

// Concurrent-tile-column support: row-major traversal of a frame wider
// than one tile (e.g. a 16-wide acceptance frame with 8-wide tiles)
// interleaves two tile columns within the same 8-row
// band -- tile column 0 accumulates samples 0-7 of each row, tile
// column 1 accumulates samples 8-15, and traversal returns to column 0
// on the next row before column 1's tile has completed. A single
// accumulator set would be stomped every 8 pixels, so this module keeps
// MAX_TILE_COLS independent named register sets (never a real array --
// see tile_stats_hls.cpp's header for why a variable-indexed static
// array serializes the HLS schedule), selected by `x`'s TILE_COL_BIT.
// Fixed at 2 because no supported frame width needs more than 2
// concurrent tile columns; not generalized to arbitrary NUM_TILE_COLS
// since speculative widening would add real accumulator hardware with
// no exercised use -- the same "as-needed, not speculative" scope every
// other accumulator change in this module follows (e.g. the 4-way
// sum-of-squares split).
#define MAX_TILE_COLS 2
#define TILE_COL_BIT  3   // x[3] == (x / TILE_WIDTH) for TILE_WIDTH == 8

// tile_stats_hls: a real per-tile streaming statistics
// accumulator. Consumes the same normalized-pixel-tagged stream
// pixel_normalizer emits (fan-out sibling of window_builder_rtl /
// threshold_rtl, not downstream of either), accumulating running
// minimum/maximum/sum/sum-of-squares across one tile's 64 samples, and
// emitting the closed-form population statistics:
//
//   mean     = sum_of_64_pixels >> 6
//   variance = max(0, (sum_of_squares >> 6) - mean^2)
//
// on the tile's last accepted sample. Fixed 1-cycle latency, real II=1
// (see modules.yml's tile_stats_hls entry and tile_stats_hls.cpp's
// header for the full derivation of why an earlier version needed
// II=2 and what removed it). The accumulator is extended to
// MAX_TILE_COLS concurrent tile-column banks (above) so a frame wider
// than one tile keeps real II/latency, not just single-tile-per-frame
// correctness.
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
);

#endif // TILE_STATS_HLS_H
