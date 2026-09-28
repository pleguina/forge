#include "matrix.h"
// Same struct, three positions: scalar by value, array element (scalarised),
// and returned. Does HLS bit-pack it or pad it?
typedef packed_t sgrid_t[2][2];
packed_t m_structs(packed_t as_scalar, const sgrid_t as_array_elem, word_t &o) {
#pragma HLS PIPELINE II=1
#pragma HLS INTERFACE ap_ctrl_none port=return
#pragma HLS INTERFACE ap_none port=as_scalar
#pragma HLS INTERFACE ap_none port=as_array_elem
#pragma HLS ARRAY_PARTITION variable=as_array_elem complete dim=0
    packed_t r = as_scalar;
    ap_uint<11> acc = 0;
    for (int i = 0; i < 2; i++)
        for (int j = 0; j < 2; j++) acc += as_array_elem[i][j].value;
    r.value = acc; o = acc;
    return r;
}
