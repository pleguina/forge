# HLS port matrix

Three HLS top functions covering the interface/pragma combinations, and the
**exact port list each one synthesises to** under Vitis HLS 2024.1
(`golden_ports.json`).

`forge/hls/port_prediction.py`'s rules are derived from this real output
rather than from documentation, and `forge/tests/test_hls_port_prediction.py`
asserts the predictor reproduces it. When the two disagree, this fixture is
right.

| Case | Covers |
|---|---|
| `m_scalars.cpp` | `ap_ctrl_none`; scalar `ap_none` / `ap_vld` / `ap_ack` / `ap_hs` / `ap_stable`, in both directions |
| `m_arrays.cpp` | `ap_ctrl_hs`; `ARRAY_PARTITION complete dim=0` vs `dim=1`; `ap_memory` read and write; `ap_fifo`; struct argument; struct return |
| `m_axi.cpp` | `axis` streams; `s_axilite` bundles; `m_axi` master |

## Three things the real output settles

1. **A struct is padded, not concatenated.** `packed_t` declares 16 bits of
   fields (`ap_uint<1>` + `ap_uint<11>` + `ap_int<4>`) and synthesises to
   **48**. Summing declared widths is wrong in a way that looks right, so
   struct widths are reported as unknown rather than computed.
2. **AXI-Stream `TDATA` rounds up to a byte multiple.** A 12-bit element
   gives a 16-bit `TDATA`.
3. **An AXI interface flips the block reset.** `m_axi.cpp` has `ap_rst_n`
   where the other two have `ap_rst` — a whole-block consequence of an
   unrelated argument's interface mode.

## Regenerating

Needs `vitis_hls` on PATH. From this directory:

```bash
for top in m_scalars m_arrays m_axi; do
  cat > run_$top.tcl <<TCL
open_project -reset prj_$top
add_files src/$top.cpp -cflags "-Isrc"
set_top $top
open_solution -reset sol -flow_target vivado
set_part {xcvu9p-flga2104-2L-e}
create_clock -period 4.0
csynth_design
exit
TCL
  vitis_hls -f run_$top.tcl
done
```

Then re-extract `golden_ports.json` from each
`prj_<top>/sol/syn/verilog/<top>.v` port declaration list. The synthesis
products themselves are not committed — only the port lists, which are what
the predictor is checked against.
