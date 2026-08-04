#ifndef TILE_STATS_HLS_H
#define TILE_STATS_HLS_H

#include <ap_int.h>

// forge.pixel_stream.v1-shaped field widths (release-plan Phase 10),
// plus the tile-statistics record's own frozen field widths
// (preflight.md §6.1: minimum{8}, maximum{8}, mean{16}, variance{24}).
typedef ap_uint<8>  pixel_t;
typedef ap_uint<12> coord_t;
typedef ap_uint<16> frame_id_t;
typedef ap_uint<16> tile_id_t;
typedef ap_uint<1>  flag_t;
typedef ap_uint<8>  stat_t;
typedef ap_uint<16> mean_t;
typedef ap_uint<24> variance_t;

// Tile geometry, frozen (preflight.md §9.1/§10): 8x8 tiles, row-major
// raster traversal. A pixel at (x,y) is the last sample of its own tile
// iff (x % TILE_WIDTH == TILE_WIDTH-1) && (y % TILE_HEIGHT ==
// TILE_HEIGHT-1) -- true regardless of how many tiles a frame has,
// since it's a purely local per-tile-cell test. What this module does
// NOT yet handle is more than one tile being accumulated concurrently
// (needed once FRAME_WIDTH > TILE_WIDTH interleaves multiple tiles'
// pixels within a shared tile-row band) -- out of scope for slice 10.3,
// same deferral preflight.md's 10.2 scope note already established for
// the 16x16 full-functional dataset (that's 10.6/10.7's job, not this
// slice's).
#define TILE_WIDTH  8
#define TILE_HEIGHT 8

// tile_stats_hls — slice 10.3 (release-plan Phase 10, preflight.md §6.2,
// spec §11 "tile_stats_hls"): a real per-tile streaming statistics
// accumulator. Consumes the same normalized-pixel-tagged stream
// pixel_normalizer emits (fan-out sibling of window_builder_rtl /
// threshold_rtl, not downstream of either), accumulating running
// minimum/maximum/sum/sum-of-squares across one tile's 64 samples, and
// emitting the closed-form population statistics (preflight.md §9.1,
// frozen):
//
//   mean     = sum_of_64_pixels >> 6
//   variance = max(0, (sum_of_squares >> 6) - mean^2)
//
// on the tile's last accepted sample. Fixed 6-cycle latency, II=2 --
// the spec's own "bounded 6..10" framing (§11/§15.2) turned out, once
// actually synthesized, to be one fixed depth, not a real per-invocation
// range; see modules.yml's tile_stats_hls entry for the full correction
// and tile_stats_hls.cpp's LATENCY pragma comment.
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
