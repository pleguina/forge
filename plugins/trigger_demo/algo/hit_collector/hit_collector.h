#ifndef HIT_COLLECTOR_H
#define HIT_COLLECTOR_H

#include "trigger_types.h"

// Collects decoded hits from N_CHANNELS decoder instances (fan-in).
// Counts valid channels and sums their phi coordinates.
//
// Port naming: in_hit_0..3, in_valid_0..3  (matches prefix_array contract)
void hit_collector(
    ap_uint<DECODED_HIT_W> in_hit_0,   ap_uint<1> in_valid_0,
    ap_uint<DECODED_HIT_W> in_hit_1,   ap_uint<1> in_valid_1,
    ap_uint<DECODED_HIT_W> in_hit_2,   ap_uint<1> in_valid_2,
    ap_uint<DECODED_HIT_W> in_hit_3,   ap_uint<1> in_valid_3,
    ap_uint<NHITS_W>       &n_hits,
    ap_uint<PHI_W>         &phi_sum,
    ap_uint<1>             &collector_valid
);

#endif // HIT_COLLECTOR_H
