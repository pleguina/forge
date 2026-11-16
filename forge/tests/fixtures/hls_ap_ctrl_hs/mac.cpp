#include <ap_int.h>
ap_uint<24> mac(ap_uint<12> a, ap_uint<12> b, ap_uint<8> c) {
#pragma HLS INTERFACE ap_ctrl_hs port=return
#pragma HLS PIPELINE II=1
    return a * b + c;
}
