#ifndef MATRIX_H
#define MATRIX_H
#include <ap_int.h>
#include <hls_stream.h>

#define ROWS 3
#define COLS 4

typedef ap_uint<12> word_t;
typedef ap_int<8>   small_t;

typedef struct {
    ap_uint<1>  flag;
    ap_uint<11> value;
    ap_int<4>   delta;
} packed_t;              // expect 16 bits packed

typedef word_t grid_t[ROWS][COLS];
typedef word_t line_t[COLS];
#endif
