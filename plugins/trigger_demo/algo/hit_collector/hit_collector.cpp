#include "hit_collector.h"

void hit_collector(
    ap_uint<DECODED_HIT_W> in_hit_0,   ap_uint<1> in_valid_0,
    ap_uint<DECODED_HIT_W> in_hit_1,   ap_uint<1> in_valid_1,
    ap_uint<DECODED_HIT_W> in_hit_2,   ap_uint<1> in_valid_2,
    ap_uint<DECODED_HIT_W> in_hit_3,   ap_uint<1> in_valid_3,
    ap_uint<NHITS_W>       &n_hits,
    ap_uint<PHI_W>         &phi_sum,
    ap_uint<1>             &collector_valid
) {
#pragma HLS PIPELINE II=1
#pragma HLS INTERFACE ap_ctrl_none port=return
#pragma HLS INTERFACE ap_none port=in_hit_0
#pragma HLS INTERFACE ap_none port=in_valid_0
#pragma HLS INTERFACE ap_none port=in_hit_1
#pragma HLS INTERFACE ap_none port=in_valid_1
#pragma HLS INTERFACE ap_none port=in_hit_2
#pragma HLS INTERFACE ap_none port=in_valid_2
#pragma HLS INTERFACE ap_none port=in_hit_3
#pragma HLS INTERFACE ap_none port=in_valid_3
#pragma HLS INTERFACE ap_none port=n_hits
#pragma HLS INTERFACE ap_none port=phi_sum
#pragma HLS INTERFACE ap_none port=collector_valid

    // Count valid channels
    ap_uint<NHITS_W> count = in_valid_0 + in_valid_1 + in_valid_2 + in_valid_3;

    // Sum phi coordinates of valid hits (phi is upper 16 bits of decoded_hit)
    ap_uint<PHI_W> p0 = in_valid_0 ? (ap_uint<PHI_W>)in_hit_0.range(31, 16) : ap_uint<PHI_W>(0);
    ap_uint<PHI_W> p1 = in_valid_1 ? (ap_uint<PHI_W>)in_hit_1.range(31, 16) : ap_uint<PHI_W>(0);
    ap_uint<PHI_W> p2 = in_valid_2 ? (ap_uint<PHI_W>)in_hit_2.range(31, 16) : ap_uint<PHI_W>(0);
    ap_uint<PHI_W> p3 = in_valid_3 ? (ap_uint<PHI_W>)in_hit_3.range(31, 16) : ap_uint<PHI_W>(0);

    n_hits          = count;
    phi_sum         = p0 + p1 + p2 + p3;
    collector_valid = (count > 0) ? ap_uint<1>(1) : ap_uint<1>(0);
}
