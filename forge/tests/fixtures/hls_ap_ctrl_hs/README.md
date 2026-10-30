# hls_ap_ctrl_hs

`mac.cpp` (`a * b + c`, `ap_ctrl_hs`, pipelined II=1) and the Verilog Vitis
HLS 2024.1 synthesised from it (xcvu9p, 2.5 ns clock; latency 3 cycles). The
RTL is the tool's output, unedited: the test drives it the way HLS blocks are
driven, through `ap_start`/`ap_ready`/`ap_done`.
