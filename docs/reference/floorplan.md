# Floorplan of a block-design top level

`forge dev framework floorplan` writes one `USER_SLR_ASSIGNMENT` per cell of a
`--mode bd` top level, with no coordinates: the placer is free inside the SLR.
It is a placement proposal from the links and the graph of the design, not a
timing result: whether it closes timing is for Vivado to say, and
`floorplan-compare` reads what Vivado said.

```bash
forge dev framework floorplan \
  --block-design gen/block_design.tcl --ir gen/design.ir.json \
  --crossings gen/block_design.crossings.json \
  --detector-io detector_io.resolved.json \
  --spec floorplan.yml --out slr_assign.xdc
```

```yaml
floorplan:
  root: "*pload/u_algo_top/algo_top_bd_i"   # the block-design instance in the netlist
  pblock: "pblock_slr{slr}"                 # optional: also add each cell to this pblock
  reserved: [1]                             # SLRs only a declaration puts cells in
  slr:
    1: [phi_dist_processor, pdf, best_sel]  # cell name patterns (fnmatch)
    0: [bx_timing, "gmt_tmux_dly_*"]
```

A cell's SLR comes from, in order:

1. the `slr` patterns;
2. the links: the instance that receives a link's top-level port goes to the
   SLR of the link's endpoint in the resolved I/O, and so does an instance it
   feeds that no other link's data reaches (a link demux and the decoders
   behind it). A new mapping moves them with it;
3. its neighbours: the SLR most of the cells it shares a net with have, a few
   hops out, ties to the lowest SLR, never a `reserved` one.

The registered crossings listed in `--crossings` are left to the placer, so
they can sit on the SLR boundary. `USER_SLR_ASSIGNMENT` alone is soft;
`pblock` makes it hard.

## The proposal's report

`--report fp.json` also writes what the proposal amounts to, and the spec
then needs the device: `part` (one FORGE knows the SLRs of: VU13P, VU9P,
KU115) or `slrs`.

```yaml
floorplan:
  part: xcvu13p-flga2577-1-e
  crossing_budget: {"0-1": 40}     # or one number for every boundary
  capacity: {1: {LUT: 300000}}     # per SLR, checked against --resources
```

| Field | Content |
|-------|---------|
| `per_slr` | Cells and, with `--resources` (an HLS metrics JSON), LUT/FF/BRAM/DSP/URAM per SLR |
| `crossings.nets` | Block-design nets between cells of different SLRs, per boundary |
| `crossings.unregistered` | Those with no registered crossing at either end |
| `unassigned` | Cells no rule placed |
| `issues` | A pattern that matches no cell (warning), a cell declared in two SLRs, an SLR not on the device, a budget or capacity exceeded (errors) |

An error in `issues` makes the command fail after writing the files.

## After implementation

`fp.check.tcl`, written next to the report, records after `place_design` the
SLRs each assigned cell's primitives were placed in
(`floorplan_placed.csv`). Then:

```bash
forge dev framework floorplan-compare --report fp.json \
  --placed floorplan_placed.csv \
  --timing-summary timing_summary.rpt \
  --timing-paths timing_paths.rpt --out fp_compare.json
```

reports the cells placed as proposed and the ones placed elsewhere (or
spread over SLRs), WNS and TNS from `report_timing_summary`, and from a
`report_timing -max_paths N` a slack histogram and the worst slack of the
paths starting or ending in each assigned cell.
