# Floorplan of a block-design top level

`forge dev framework floorplan` writes one `USER_SLR_ASSIGNMENT` per cell of a
`--mode bd` top level, with no coordinates: the placer is free inside the SLR.

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
