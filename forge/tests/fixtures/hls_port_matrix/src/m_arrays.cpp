#include "matrix.h"
// Block protocol ap_ctrl_hs + array/aggregate shapes.
//   grid_full   : 2-D, complete dim=0  -> expect scalars name_i_j
//   grid_dim1   : 2-D, complete dim=1  -> expect per-row memory ports
//   line_mem    : 1-D, no partition    -> expect ap_memory address/ce/q
//   line_fifo   : 1-D, ap_fifo         -> expect dout/empty_n/read
//   st_in       : struct by value      -> expect one packed port
// returns packed_t by value            -> expect ap_return
packed_t m_arrays(
    const grid_t grid_full,
    const grid_t grid_dim1,
    const line_t line_mem,
    const line_t line_fifo,
    packed_t     st_in,
    line_t       line_out
) {
#pragma HLS INTERFACE ap_ctrl_hs port=return
#pragma HLS INTERFACE ap_none port=grid_full
#pragma HLS ARRAY_PARTITION variable=grid_full complete dim=0
#pragma HLS ARRAY_PARTITION variable=grid_dim1 complete dim=1
#pragma HLS INTERFACE ap_fifo port=line_fifo
#pragma HLS INTERFACE ap_none port=st_in
    packed_t r;
    ap_uint<11> acc = 0;
    for (int i = 0; i < ROWS; i++)
        for (int j = 0; j < COLS; j++)
            acc += grid_full[i][j] + grid_dim1[i][j];
    for (int j = 0; j < COLS; j++) {
        acc += line_mem[j] + line_fifo[j];
        line_out[j] = acc;
    }
    r.flag = st_in.flag; r.value = acc; r.delta = st_in.delta;
    return r;
}
