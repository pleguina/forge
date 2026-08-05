# 12 — Diagnostics and negative fixtures

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

Not generated yet — planned for a future visualization pass (a
depth-4-vs-depth-64/128 comparison figure, and an error-map figure for a
mismatched record). Today, the real diagnostic/scoreboard output above
is the source of truth.

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
