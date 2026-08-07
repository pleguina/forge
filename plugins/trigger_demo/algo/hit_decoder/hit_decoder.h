#ifndef HIT_DECODER_H
#define HIT_DECODER_H

#include "trigger_types.h"

// Decodes a 32-bit packed detector hit word.
//   Input:  raw_hit = {phi[31:16], eta[15:0]}, raw_valid
//   Output: decoded_hit = {phi+1[31:16], eta[15:0]}, decoded_valid
// The +1 phi correction simulates a detector-specific calibration offset.
void hit_decoder(
    ap_uint<RAW_HIT_W>     raw_hit,
    ap_uint<1>             raw_valid,
    ap_uint<DECODED_HIT_W> &decoded_hit,
    ap_uint<1>             &decoded_valid
);

#endif // HIT_DECODER_H
