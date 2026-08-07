#include "trigger_output.h"

void trigger_output(
    ap_uint<1>              trigger_accept,
    ap_uint<QUALITY_W>      trigger_quality,
    ap_uint<1>              trigger_valid,
    ap_uint<TRIGGER_WORD_W> &trigger_word,
    ap_uint<1>              &out_valid
) {
#pragma HLS PIPELINE II=1
#pragma HLS INTERFACE ap_ctrl_none port=return
#pragma HLS INTERFACE ap_none port=trigger_accept
#pragma HLS INTERFACE ap_none port=trigger_quality
#pragma HLS INTERFACE ap_none port=trigger_valid
#pragma HLS INTERFACE ap_none port=trigger_word
#pragma HLS INTERFACE ap_none port=out_valid

    // Pack: [31]=accept, [23:16]=quality, [15:0]=reserved
    trigger_word = ((ap_uint<TRIGGER_WORD_W>)trigger_accept  << 31) |
                   ((ap_uint<TRIGGER_WORD_W>)trigger_quality << 16);
    out_valid    = trigger_valid;
}
