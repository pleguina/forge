#ifndef TRIGGER_OUTPUT_H
#define TRIGGER_OUTPUT_H

#include "trigger_types.h"

// Packs trigger decision into a 32-bit output word for readout.
//   trigger_word layout:
//     [31]    = trigger_accept
//     [23:16] = trigger_quality
//     [15:0]  = reserved (zero)
void trigger_output(
    ap_uint<1>              trigger_accept,
    ap_uint<QUALITY_W>      trigger_quality,
    ap_uint<1>              trigger_valid,
    ap_uint<TRIGGER_WORD_W> &trigger_word,
    ap_uint<1>              &out_valid
);

#endif // TRIGGER_OUTPUT_H
