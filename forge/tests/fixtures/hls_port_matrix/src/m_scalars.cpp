#include "matrix.h"
// Block protocol ap_ctrl_none + every scalar interface mode.
void m_scalars(
    word_t  in_none,
    word_t  in_vld,
    word_t  in_ack,
    word_t  in_hs,
    word_t  in_stable,
    small_t in_signed,
    word_t &out_none,
    word_t &out_vld,
    word_t &out_ack,
    word_t &out_hs
) {
#pragma HLS PIPELINE II=1
#pragma HLS INTERFACE ap_ctrl_none port=return
#pragma HLS INTERFACE ap_none   port=in_none
#pragma HLS INTERFACE ap_vld    port=in_vld
#pragma HLS INTERFACE ap_ack    port=in_ack
#pragma HLS INTERFACE ap_hs     port=in_hs
#pragma HLS INTERFACE ap_stable port=in_stable
#pragma HLS INTERFACE ap_none   port=in_signed
#pragma HLS INTERFACE ap_none   port=out_none
#pragma HLS INTERFACE ap_vld    port=out_vld
#pragma HLS INTERFACE ap_ack    port=out_ack
#pragma HLS INTERFACE ap_hs     port=out_hs
    word_t s = in_none + in_vld + in_ack + in_hs + in_stable + (word_t)in_signed;
    out_none = s; out_vld = s + 1; out_ack = s + 2; out_hs = s + 3;
}
