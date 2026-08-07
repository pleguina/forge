# 12 — Diagnostics and negative fixtures

<nav class="forge-step-strip" aria-label="Chapter progress" markdown="span">
[01](01-quickstart.md) [02](02-project-structure-and-contracts.md) [03](03-mixed-rtl-hls.md) [04](04-parallel-paths-and-latency.md) [05](05-bounded-and-elastic-processing.md) [06](06-clock-domains-and-cdc.md) [07](07-throughput-backpressure-and-fifos.md) [08](08-datasets-and-golden-models.md) [09](09-full-functional-design.md) [10](10-platform-integration.md) [11](11-inspect-report-and-reproduce.md) **12**
</nav>

*Step 12 of 12*

**Steps in this chapter:** `invalid_direct_bus_cdc`, `invalid_fifo_depth` · **Tools needed:** Python, Vitis HLS, Vivado XSim

## Goal

See FORGE reject or fail on purpose, and understand why each failure is
the evidence a capability works, not a bug.

## What you will learn

- Why an undeclared clock-domain crossing is rejected before any RTL is generated.
- Why a FIFO depth can pass structural validation and still fail at runtime.
- What a stale build plan looks like, and how to accept a new one deliberately.

## Starting design

Reuses `design_cdc.yml` (chapter 06) and `design_packetizer.yml`
(chapter 07)'s own modules — these fixtures don't introduce new RTL,
only misconfigured topology.

## What you add

Nothing new is built. Both fixtures already exist:
`invalid_direct_bus_cdc.yml` and `invalid_fifo_depth_packetizer.yml`.

## Files you edit

None — you run existing fixtures, you don't create new ones.

## What FORGE generates

Diagnostics, not RTL — see each fixture below for exactly what.

## Command to run

### Fixture 1: an undeclared clock-domain crossing

```bash
./run_vision_pipeline_demo.sh --step invalid_direct_bus_cdc
```

`invalid_direct_bus_cdc.yml` wires `ctrl_mailbox` (control, `ap_clk`)
directly to `pxsink` (pixel, `clk_pixel`) with no `cdc:` block at all —
a bare multi-bit wire crossing unrelated clock domains.

1. `forge topgen validate` (non-strict) only *warns*, exit 0:

    ```bash
    forge topgen validate plugins/vision_pipeline_demo/forge/designs/invalid_direct_bus_cdc.yml
    ```

2. The real hard-failure proof is `gen-top --strict`:

    ```bash
    forge topgen gen-top plugins/vision_pipeline_demo/forge/designs/invalid_direct_bus_cdc.yml \
      --mode verilog --consumer-root . \
      --contracts-from plugins/vision_pipeline_demo/forge/modules.yml \
      --hls-build-root build_hls_vision_pipeline_demo \
      --output /tmp/rejected.v --strict
    ```

### Fixture 2: a FIFO depth too shallow for the real burst

```bash
./run_vision_pipeline_demo.sh --step invalid_fifo_depth
```

`invalid_fifo_depth_packetizer.yml` is identical to chapter 07's
`design_packetizer.yml` except the pixel-result crossing's `async_fifo`
depth is 4 instead of 64.

## Expected terminal result

**Fixture 1:** `forge topgen validate` exits 0 with ATG023/ATG024
warnings. `gen-top --strict` exits 1 — **no RTL is written**.

**Fixture 2:** depth=4 is still a valid power of two, so it passes
`validate` — depth *sufficiency* for a given burst pattern can only be
measured by actually running the simulation. `forge verify run` reports
a scoreboard **FAIL** with real `overflow_attempt`/`dropped_transactions`
events. This FAIL is the fixture working correctly, not a regression —
`./run_vision_pipeline_demo.sh` itself checks for this *expected*
failure and only reports a problem if the fixture unexpectedly passes.

## Artifacts to inspect

Chapter 07's own real depth-64 run measured a high-water mark of **28**
for the same burst — depth=4 provably can't hold that. Compare the two
runs' `throughput_result.json` (where generated) to see the difference
directly rather than taking it on faith.

## Visual result

[Chapter 07](07-throughput-backpressure-and-fifos.md)'s
`fifo-high-water-mark.png` shows the real, measured high-water mark (28)
for this same burst at depth 64 — the number `invalid_fifo_depth_xsim`'s
depth-4 FIFO provably cannot hold. A dedicated depth-4-vs-64 comparison
figure isn't committed yet: `invalid_fifo_depth_xsim` doesn't currently
have its own checked-in `throughput_result.json` (that requires a
`--probe-log` run captured before this document's own reference-asset
generator can render it) — this is a real, honest gap, not a hidden
placeholder.

## Why the capability matters

A tutorial that only shows passing flows teaches half the story. FORGE
distinguishes structural validity (does this design parse and wire up
correctly) from runtime sufficiency (does this configuration actually
work for a real workload) — both fixtures above are examples of passing
the first check and deliberately failing the second, for different
underlying reasons.

## A related diagnostic: stale build plans

Not a checked-in fixture, but a real workflow worth seeing once. A
provenance manifest, once written, can prove a design has *not* changed:

```bash
forge inspect plugins/vision_pipeline_demo/forge/designs/design.yml \
  --contracts-from plugins/vision_pipeline_demo/forge/modules.yml \
  --provenance /tmp/quickstart_provenance.json

forge inspect plugins/vision_pipeline_demo/forge/designs/design.yml \
  --contracts-from plugins/vision_pipeline_demo/forge/modules.yml \
  --explain-staleness /tmp/quickstart_provenance.json
# ✅ fresh — no reason to regenerate
```

Edit `design.yml`'s `clock_period:` (or any semantic field) and re-run
`--explain-staleness` against the same manifest:

```
⚠️  stale — 2 reason(s):
    - canonical IR content changed: <old-hash> -> <new-hash>
    - changed input: design.yml
```

This is the same mechanism `forge build --accept-plan-hash` (chapter 11)
uses to refuse a stale plan rather than silently building against
out-of-date assumptions.

## Common failure

Not applicable — this entire chapter is about interpreting expected
failures correctly rather than avoiding one.

## What changed from the previous chapter

Nothing new is built — every earlier design/module is reused, only
misconfigured, to demonstrate what FORGE catches and when.

## Next chapter

You've reached the end of the guided path — see
[Next steps](next-steps.md).
