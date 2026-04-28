#include "trigger_logic.h"

void trigger_logic(
    ap_uint<NHITS_W>   n_hits,
    ap_uint<PHI_W>     phi_sum,
    ap_uint<1>         in_valid,
    ap_uint<1>         &trigger_accept,
    ap_uint<QUALITY_W> &trigger_quality,
    ap_uint<1>         &trigger_valid
) {
#pragma HLS PIPELINE II=1
// LATENCY=3 forces HLS to insert 3 pipeline register stages in RTL,
// making ap_clk visible in the synthesised Verilog and demonstrating
// the framework latency-tracking + register_stages infrastructure.
// C-sim is unaffected (LATENCY is an RTL-synthesis-only constraint).
#pragma HLS LATENCY min=3 max=3
#pragma HLS INTERFACE ap_ctrl_none port=return
#pragma HLS INTERFACE ap_none port=n_hits
#pragma HLS INTERFACE ap_none port=phi_sum
#pragma HLS INTERFACE ap_none port=in_valid
#pragma HLS INTERFACE ap_none port=trigger_accept
#pragma HLS INTERFACE ap_none port=trigger_quality
#pragma HLS INTERFACE ap_none port=trigger_valid

    // Majority trigger: accept if hit count meets threshold
    trigger_accept  = (n_hits >= TRIGGER_THRESHOLD) ? ap_uint<1>(1) : ap_uint<1>(0);

    // Quality metric: raw hit multiplicity (could be extended with phi_sum)
    trigger_quality = (ap_uint<QUALITY_W>)n_hits;

    // Pass through validity from upstream
    trigger_valid   = in_valid;
}
