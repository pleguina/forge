# 10 — Platform integration

**Step in this chapter:** `platform_wrapper` · **Tools needed:** Python, Vitis HLS, Vivado XSim

## Goal

Assemble a real three-domain platform — control, pixel, output — with a
runtime-configurable threshold delivered over a real `mailbox_transfer`
crossing, and a status interface every earlier design left as unexposed
internal signals.

## What you will learn

- How a control-plane clock domain configures a pixel-plane module at runtime.
- Why a design's *primary* domain (the one bound to `ap_clk`) is a real choice, not always the first one declared.
- What exposing a "status interface" at the top level actually looks like.

## Starting design

Reuses the full pixel-result/tile-statistics/packetizer topology chapter
09 assembled, plus a new control-plane path.

## What you add

```
control (clk_control, 50MHz)  --mailbox_transfer-->  pixel (ap_clk, 200MHz)
  ctrl_mailbox (threshold/kernel_mode/frame_limit bundle)

pixel (ap_clk) -- this design's primary domain, unlike design_cdc.yml
  norm -> winbld -> sobel  ─┐
       -> threshcfg (NEW: threshold_configurable_rtl -- takes its
          threshold from the mailbox crossing, latched at frame
          boundaries, not a compile-time parameter) ┴-> merge -> prpack ─┐
       -> tstats -> tjoin, tbnd -> tjoin -> tspack ┤                     │ [async FIFO x2]
                                                                          ▼
output (clk_output, 125MHz)                                        pktz -> forge.packet_stream.v1
```

`pixel` (not `control`) is this design's primary domain — a real,
deliberate choice: Verilog port names can't be parametrized, so reusing
`ctrl_mailbox_rtl.v` from chapter 06 (which treats `control` as primary)
alongside modules that treat `pixel` as primary needs a second,
otherwise-identical module (`ctrl_mailbox_platform_rtl.v`) with a
different literal clock port name — see
`docs/development/adr/0002-cdc-primitive-semantics.md`.

## Files you edit

| File | Ownership |
|---|---|
| `plugins/vision_pipeline_demo/algo/rtl/threshold_configurable_rtl.v` | **PROJECT SOURCE** |
| `plugins/vision_pipeline_demo/algo/rtl/ctrl_mailbox_platform_rtl.v` | **PROJECT SOURCE** |
| `plugins/vision_pipeline_demo/forge/designs/design_platform_wrapper.yml` | **PROJECT SOURCE** |

## What FORGE generates

| Artifact | Ownership |
|---|---|
| `gen-top/design_vision_pipeline_platform_wrapper/algo_top.v` | **FORGE GENERATED** |
| `plugins/vision_pipeline_demo/forge/verify/platform_wrapper_xsim/` | **FORGE GENERATED** + **TOOLCHAIN OUTPUT** |

## Command to run

```bash
./run_vision_pipeline_demo.sh --step platform_wrapper
```

## Expected terminal result

```
Scoreboard check: PASS
```

Two real 8×8 frames back to back (ramp then checkerboard, genuinely
different per-frame thresholds: 64 for the first frame, 160 for the
second — neither equal to the compile-time-default 96): **130/130
checks pass** (128 pixel-result + 2 tile-statistics records). The
content match itself is the proof that a real mailbox-written threshold
change reaches the pipeline's real output at the correct frame boundary
— not a separate "did it compile" check.

## Artifacts to inspect

The status interface — `merge.tag_mismatch`, `tjoin.join_mismatch`,
`tjoin.out_end_of_frame` — three real diagnostic/completion signals
every earlier design left as dangling internal outputs, exposed here as
real top-level ports, needing no new RTL. All three read `0` throughout
a passing run.

```bash
forge analyze latency-check plugins/vision_pipeline_demo/forge/designs/design_platform_wrapper.yml \
  --contracts-from plugins/vision_pipeline_demo/forge/modules.yml \
  --hls-build-root build_hls_vision_pipeline_demo
```

confirms zero merge-point mismatches — the same `sobel(17)`/`threshcfg(17)`
balance chapter 04 introduced, now with a mailbox-configured module in
place of a fixed-parameter one.

## Visual result

Not generated yet — planned for a future visualization pass (a platform
topology figure grouped by control/pixel/output domain). Today, use
`forge inspect --dot`/`--explorer` from [chapter
11](11-inspect-report-and-reproduce.md).

## Why the capability matters

A real product rarely has just one clock domain doing one job — a
control plane that configures behavior at runtime, a pixel plane doing
the real work, and an output plane draining results, are three
genuinely different concerns. This design proves FORGE's ordinary
`forge topgen gen-top` path (the same mechanism every earlier chapter
used) handles that assembly with no new core machinery.

## Common failure

`invalid_direct_bus_cdc.yml` (an undeclared crossing between the
control and pixel domains introduced in this chapter) is covered in
[chapter 12](12-diagnostics-and-negative-fixtures.md).

## What changed from the previous chapter

A third clock domain, a runtime-configurable module replacing a
fixed-parameter one, and a real status interface exposed at the top
level for the first time.

## Next chapter

[11 — Inspect, report, and reproduce](11-inspect-report-and-reproduce.md)
