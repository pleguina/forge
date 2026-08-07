#ifndef TRIGGER_LOGIC_H
#define TRIGGER_LOGIC_H

#include "trigger_types.h"

// Simple majority trigger.
// Fires (trigger_accept=1) when n_hits >= TRIGGER_THRESHOLD.
// Quality output equals the hit count.
void trigger_logic(
    ap_uint<NHITS_W>   n_hits,
    ap_uint<PHI_W>     phi_sum,
    ap_uint<1>         in_valid,
    ap_uint<1>         &trigger_accept,
    ap_uint<QUALITY_W> &trigger_quality,
    ap_uint<1>         &trigger_valid
);

#endif // TRIGGER_LOGIC_H
