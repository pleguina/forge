#include "matrix.h"
// AXI-family interfaces: axis stream, s_axilite control+scalar, m_axi master.
void m_axi(
    hls::stream<word_t> &strm_in,
    hls::stream<word_t> &strm_out,
    word_t               cfg_a,
    word_t              &status_b,
    word_t               mem[COLS]
) {
#pragma HLS INTERFACE axis      port=strm_in
#pragma HLS INTERFACE axis      port=strm_out
#pragma HLS INTERFACE s_axilite port=cfg_a    bundle=ctrl
#pragma HLS INTERFACE s_axilite port=status_b bundle=ctrl
#pragma HLS INTERFACE s_axilite port=return   bundle=ctrl
#pragma HLS INTERFACE m_axi     port=mem      offset=slave bundle=gmem
    word_t acc = cfg_a;
    for (int i = 0; i < COLS; i++) { acc += mem[i]; }
    if (!strm_in.empty()) { acc += strm_in.read(); }
    strm_out.write(acc);
    status_b = acc;
}
