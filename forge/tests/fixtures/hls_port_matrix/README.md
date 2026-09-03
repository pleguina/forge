# HLS port matrix

Five HLS top functions covering the interface/pragma combinations, and the
**exact port list each one synthesises to**, per Vitis HLS release
(`golden/<version>.json`).

`forge/hls/port_prediction.py`'s rules are derived from this real output
rather than from documentation, and `forge/tests/test_hls_port_prediction.py`
asserts the predictor reproduces it. When the two disagree, this fixture is
right.

## Layout

| Path | What it is |
|---|---|
| `src/` | the C++ kernels |
| `cases.json` | the predictor inputs for each kernel, plus the differences it is documented **not** to reproduce, each with its reason |
| `golden/<version>.json` | the real synthesised port list per kernel, for that Vitis HLS release |

HLS port naming moves between releases, so a prediction validated against
one release is evidence about that release and nothing else
(`forge/hls/tool_matrix.py`). Adding a release is a **data change**:
synthesise this corpus under it with the script below, drop the port lists
in as `golden/<version>.json`, and the support matrix, `forge doctor` and
the generated docs page pick it up. A release with no file here is reported
as untested — never assumed to work.

The predictor inputs live in `cases.json` rather than inside a test so that
validating a new release replays *exactly* the inputs the old one was
replayed with; two copies of those argument lists would make the comparison
meaningless the first time they drifted.

| Case | Covers |
|---|---|
| `m_scalars.cpp` | `ap_ctrl_none`; scalar `ap_none` / `ap_vld` / `ap_ack` / `ap_hs` / `ap_stable`, in both directions |
| `m_arrays.cpp` | `ap_ctrl_hs`; `ARRAY_PARTITION complete dim=0` vs `dim=1`; `ap_memory` read and write; `ap_fifo`; struct argument; struct return |
| `m_axi.cpp` | `axis` streams; `s_axilite` bundles; `m_axi` master |
| `m_structs.cpp` | the same struct as a scalar argument, an array element, and a return value |
| `m_dimdefault.cpp` | `ARRAY_PARTITION complete` with and without `dim=0`, on the same array shape |

## Five things the real output settles

1. **A struct's packing depends on where it sits.** `m_structs.cpp` puts
   the same `packed_t` (16 bits of declared fields) in three positions:
   as a scalar argument it synthesises to **48** bits (members padded), as
   an **array element** to **16** (bit-packed, the exact field sum), and as
   a return value to **48**. So a summed width is right for array elements
   and wrong for scalars and returns — which is why `T_GP_HIT` resolves to
   1+11+9=21 in a real design while the fixture's scalar does not.
2. **AXI-Stream `TDATA` rounds up to a byte multiple.** A 12-bit element
   gives a 16-bit `TDATA`.
3. **A bare `complete` partitions `dim=1`, not every dimension.**
   `m_dimdefault.cpp` puts both spellings on one `word_t[3][4]`:
   `complete` leaves depth-4 memories (**18** ports, including a second
   port set), `complete dim=0` gives **12** plain wires. One missing word,
   and the port list changes shape entirely — this is why two identically
   shaped `hit_t[18][14]` arguments in this project synthesise completely
   differently.
4. **An AXI interface flips the block reset.** `m_axi.cpp` has `ap_rst_n`
   where the other two have `ap_rst` — a whole-block consequence of an
   unrelated argument's interface mode.

## Regenerating

Needs `vitis_hls` on PATH. From this directory:

```bash
for top in m_scalars m_arrays m_axi m_dimdefault m_structs; do
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

Then re-extract the port lists from each
`prj_<top>/sol/syn/verilog/<top>.v` port declaration list into
`golden/<version>.json`, keyed by top name. The synthesis products
themselves are not committed — only the port lists, which are what the
predictor is checked against.

Run `python -m pytest forge/tests/test_hls_tool_matrix.py` afterwards: it
replays every kernel against every recorded release and fails on any
difference the corpus does not already document and explain.
