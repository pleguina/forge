#include "hit_decoder.h"

void hit_decoder(
    ap_uint<RAW_HIT_W>     raw_hit,
    ap_uint<1>             raw_valid,
    ap_uint<DECODED_HIT_W> &decoded_hit,
    ap_uint<1>             &decoded_valid
) {
#pragma HLS PIPELINE II=1
#pragma HLS INTERFACE ap_ctrl_none port=return
#pragma HLS INTERFACE ap_none port=raw_hit
#pragma HLS INTERFACE ap_none port=raw_valid
#pragma HLS INTERFACE ap_none port=decoded_hit
#pragma HLS INTERFACE ap_none port=decoded_valid

    ap_uint<PHI_W> phi = raw_hit.range(31, 16);
    ap_uint<ETA_W> eta = raw_hit.range(15, 0);

    // Detector-specific calibration: phi offset correction
    ap_uint<PHI_W> phi_corrected = phi + 1;

    decoded_hit = (phi_corrected, eta);
    decoded_valid = raw_valid & (raw_hit != 0);
}
