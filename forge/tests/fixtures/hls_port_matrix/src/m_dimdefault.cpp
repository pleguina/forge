#include "matrix.h"
// Same array shape, three partitioning spellings. Does a bare `complete`
// mean dim=0 (all dims) or dim=1 (outer only)?
void m_dimdefault(const grid_t a_bare, const grid_t b_dim0, word_t &out) {
#pragma HLS PIPELINE II=1
#pragma HLS INTERFACE ap_ctrl_none port=return
#pragma HLS ARRAY_PARTITION variable=a_bare complete
#pragma HLS ARRAY_PARTITION variable=b_dim0 complete dim=0
    word_t s = 0;
    for (int i = 0; i < ROWS; i++)
        for (int j = 0; j < COLS; j++) s += a_bare[i][j] + b_dim0[i][j];
    out = s;
}
