# 07 — Throughput, backpressure, and FIFOs

**Step in this chapter:** `packetizer` · **Tools needed:** Python, Vitis HLS, Vivado XSim

## Goal

Multiplex two independent record-producing paths onto one output stream
through real async FIFOs, and measure real occupancy/throughput instead
of assuming it.

## What you will learn

- How two independent paths (pixel-result, tile-statistics) cross into one output domain.
- Why a FIFO needs a real per-record write-enable pin, not just a clock.
- How to read a real, measured throughput/occupancy result instead of a static estimate.

## Starting design

Reuses chapter 03's pixel-result path and chapter 05's tile-statistics
path unmodified, each behind its own `norm` instance (`norm_px` /
`norm_tile` — two independent normalizer instances, not one shared
fan-out source; chapter 09 is where that unification happens).

## What you add

```
                                                                      [async FIFO: pixel -> output, depth 64]
... pixel-result path ... -> pixel_result_packer_rtl (128-bit record) ─┐
                                                                        ┴-> packetizer_rtl (output domain)
... tile-statistics path ... -> tile_stats_packer_rtl (128-bit record) ┘   -> external forge.packet_stream.v1 (256-bit beat)
                                                                      [async FIFO: pixel -> output, depth 64]
```

Two clock domains: `pixel` (`ap_clk`, 200MHz) is this design's primary
domain; `output` (`clk_output`, 125MHz) is a real secondary domain.

## Files you edit

| File | Ownership |
|---|---|
| `plugins/vision_pipeline_demo/algo/rtl/pixel_result_packer_rtl.v`, `tile_stats_packer_rtl.v` | **PROJECT SOURCE** |
| `plugins/vision_pipeline_demo/algo/rtl/packetizer_rtl.v` | **PROJECT SOURCE** |
| `plugins/vision_pipeline_demo/forge/designs/design_packetizer.yml` | **PROJECT SOURCE** |

## What FORGE generates

| Artifact | Ownership |
|---|---|
| `gen-top/design_vision_pipeline_packetizer/algo_top.v` | **FORGE GENERATED** |
| `plugins/vision_pipeline_demo/forge/verify/packetizer_xsim/` | **FORGE GENERATED** + **TOOLCHAIN OUTPUT** |
| `plugins/vision_pipeline_demo/forge/verify/packetizer_xsim/throughput_result.json` | **VERIFICATION RESULT** |

## Command to run

```bash
./run_vision_pipeline_demo.sh --step packetizer
```

## Expected terminal result

```
Scoreboard check: PASS
```

## Artifacts to inspect

`throughput_result.json` (checked-in evidence, real and measured):

```json
{
  "fifo_object_id": "xform:pr_record_out:async_fifo",
  "accepted_transactions": 64,
  "emitted_transactions": 64,
  "stall_cycles": 52,
  "high_water_mark": 28,
  "full_events": 0,
  "empty_events": 2,
  "measured_rate_records_per_sec": 132467532.46753246,
  "dropped_transactions": 0,
  "duplicated_transactions": 0
}
```

64 accepted, 64 emitted, zero dropped or duplicated — real conservation,
not just a pass/fail flag. The depth-64 FIFO's real measured high-water
mark is **28** — meaningfully below its depth, with real headroom.
[Chapter 12](12-diagnostics-and-negative-fixtures.md) shows what happens
when the same burst is driven into a depth-4 FIFO instead.

Each packer module appends a 1-bit toggle to its 128-bit record so the
crossing detects real novelty — see
`docs/development/adr/0002-cdc-primitive-semantics.md` and
`docs/development/adr/0003-vision-packet-format.md` for why.

## Visual result

Not generated yet — planned for a future visualization pass (module
throughput, predicted-vs-observed throughput, FIFO occupancy over time,
depth-4-vs-depth-128 comparison figures). Today, the real JSON above is
the source of truth.

## Why the capability matters

A FIFO depth that's correct for one burst pattern can silently overflow
at a different scale or a different sustained rate — "does it compile"
is not the same question as "does it hold enough," and only a real
measured high-water mark answers the second one.

## Common failure

[Chapter 12](12-diagnostics-and-negative-fixtures.md) walks through
`invalid_fifo_depth_packetizer.yml` — the same design with the
pixel-result crossing's FIFO depth set to 4 instead of 64, which
provably cannot hold a burst this design's own depth-64 run just proved
needs 28.

## What changed from the previous chapter

Two previously-standalone paths (chapters 03 and 05) now converge
through real async FIFOs into one shared 256-bit output stream, with
real throughput/occupancy evidence to show for it.

## Next chapter

[08 — Datasets and golden models](08-datasets-and-golden-models.md)
