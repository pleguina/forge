# Vision Pipeline: The Full Reference Design

`plugins/vision_pipeline_demo/` doesn't stop at the [quickstart
tier](vision-pipeline-quickstart.md). By slice 10.7C
(`docs/internal/phase10/preflight.md` §11) it's grown into 8 real designs
sharing one module registry, exercising every capability the quickstart
page deliberately left out: parallel HLS filters, fan-out, an
exact-cycle merge, bounded/elastic metadata joins, all five CDC kinds
across three real clock domains, an output packetizer with async-FIFO
backpressure, and a runtime-configurable threshold delivered over a
`mailbox_transfer` crossing. This page assumes you've already read the
[quickstart tutorial](vision-pipeline-quickstart.md) — it covers what's
different, not the basics.

Everything below is real and CI-exercised (`.gitlab-ci.yml`'s
`forge:vision-pipeline-release-gate` job runs the exact commands this
page describes). Nothing here is a preview of unbuilt work.

## The 8 designs

| Design (`forge/designs/`) | Flow | What it proves |
|---|---|---|
| `design.yml` | `quickstart_pipeline_xsim` | Baseline: one HLS + one RTL module, one clock (slice 10.1) |
| `design_pixel_result.yml` | `pixel_result_xsim` | `window_builder_rtl` → `sobel_hls` → `edge_mask_merge_rtl`, merged against a delayed threshold branch — fan-out, alignment delay, exact-cycle merge (slice 10.2) |
| `design_tile_stats.yml` | `tile_stats_xsim` | `tile_stats_hls` (II=2) → `tile_boundary_rtl` → `tile_summary_join_rtl` — bounded→elastic tagged join (slice 10.3) |
| `design_cdc.yml` | `cdc_xsim` | All five CDC kinds (`level_sync`/`pulse_sync`/`mailbox_transfer`/`async_fifo`/`reset_sync`) across control/pixel/output domains, three genuinely independent free-running clocks (slice 10.4) |
| `design_packetizer.yml` | `packetizer_xsim` | Both record kinds multiplexed onto one `forge.packet_stream.v1`, each crossing a real `async_fifo`, throughput/backpressure/occupancy (slice 10.5) |
| `invalid_fifo_depth_packetizer.yml` | `invalid_fifo_depth_xsim` | Negative fixture: same design, FIFO depth 4 instead of 64 — **expected to fail** (spec §17.5) |
| `design_full_functional.yml` | `full_functional_xsim` | One shared `norm` fanning out to all four downstream consumers, a real 16×16/2×2-tile frame, 260/260 conservation checks (slice 10.7A) |
| `design_platform_wrapper.yml` | `platform_wrapper_xsim` | A genuine 3-domain assembly (control/pixel/output) with a real status interface and a runtime-configurable threshold delivered via `mailbox_transfer` (slice 10.7B) |

Plus one topology-only negative fixture with no flow of its own:
`invalid_direct_bus_cdc.yml` — an undeclared clock-domain crossing that
must be rejected by `forge topgen gen-top --strict` (ATG023/ATG024), not
just warned about by plain `validate` (spec §8.5).

## Run everything with one command

```bash
./run_vision_pipeline_demo.sh
```

This builds all three HLS modules (`pixel_normalizer`, `sobel_hls`,
`tile_stats_hls` — csim for `pixel_normalizer` only, since it's the only
one with a standalone C-sim testbench; synth for all three), gen-tops
all 8 designs, regenerates every verification flow, drives each flow's
own `gen_stimulus_*.py`, runs `forge verify doctor`, then runs all 9
flows — including the two negative fixtures, which are checked for
*expected* failure rather than treated as bugs. `--skip-hls` reuses an
existing `build_hls_vision_pipeline_demo/`; `--no-clean` skips the
pre-run artifact wipe.

## A closer look: the CDC design

`design_cdc.yml` is the smallest genuinely multi-domain design here, so
it's the best one to read manually if you want to see how a FORGE
design declares a real clock/reset topology instead of doctoring one
clock into several roles.

```bash
forge topgen validate plugins/vision_pipeline_demo/forge/designs/design_cdc.yml
```

Three domains: `control` (`ap_clk`, 50MHz, the design's primary clock),
`pixel` (`clk_pixel`, 200MHz), `output` (`clk_output`, 125MHz). `pixel`
and `output`'s resets (`rst_pixel`/`rst_output`) are declared under
`reset_domains: *.sync: reset_sync` — internally synchronized from the
external `ap_rst` by a real `cdc_reset_sync` instance, not driven by the
testbench. Six connections, six `cdc:` blocks, one of every kind:

```
ctrl_level_rtl   --level_sync-->      pxsink   (control -> pixel)
ctrl_pulse_rtl   --pulse_sync-->      pxsink   (control -> pixel)
ctrl_mailbox_rtl --mailbox_transfer-> pxsink   (control -> pixel)
pxsink           --async_fifo-->      outsink_level_rtl  (pixel -> output)
outsink_level_rtl --pulse_sync-->     ctrl_pulse_rtl (output -> control)
outsink_pulse_rtl --level_sync-->     ctrl_level_rtl (output -> control)
```

Run it:

```bash
forge verify generate plugins/vision_pipeline_demo/forge/verify/design.verification.yml
python3 plugins/vision_pipeline_demo/forge/verify/tools/gen_stimulus_cdc.py --flow cdc_xsim
forge verify run plugins/vision_pipeline_demo/forge/verify/cdc_xsim/verify.flow.yml \
  --plugin vision_pipeline_demo --consumer-root .
```

`gen_stimulus_cdc.py` drives fixed constants directly rather than a
per-event dataset — a scalar control-plane test doesn't fit the
golden-model-provider mold the pixel-stream flows use (see that script's
own header). 6/6 checks pass: `error_level_status`, `frame_done_count`,
`enable_status`, `apply_count`, `mailbox_status`, `result_status` — one
value carried correctly across a real clock-domain boundary for each of
the five CDC kinds.

## Negative fixtures: what "expected to fail" means

Two fixtures in this plugin are **supposed** to fail — that failure is
the evidence, not a bug:

- **`invalid_direct_bus_cdc.yml`** (spec §8.5) — an undeclared crossing.
  `forge topgen validate` (non-strict) only *warns* (ATG023/ATG024, exit
  0); the real hard-failure proof is `--strict`:

  ```bash
  forge topgen gen-top plugins/vision_pipeline_demo/forge/designs/invalid_direct_bus_cdc.yml \
    --mode verilog --consumer-root . \
    --contracts-from plugins/vision_pipeline_demo/forge/modules.yml \
    --hls-build-root build_hls_vision_pipeline_demo \
    --output /tmp/rejected.v --strict
  # exit 1, no RTL written
  ```

- **`invalid_fifo_depth_xsim`** (spec §17.5) — `design_packetizer.yml`
  with the pixel-result crossing's FIFO depth set to 4 instead of 64.
  depth=4 is still a valid power of two, so it passes `validate` — FIFO
  depth *sufficiency* for a given burst pattern can only be measured by
  actually running the simulation. `design_packetizer.yml`'s own
  depth=64 run measures a real high-water mark of 28
  (`docs/internal/phase10/preflight.md`'s 10.5 completion evidence);
  depth=4 provably can't hold that. `forge verify run` on this flow
  reports a scoreboard **FAIL** — that's the fixture working, not
  broken.

## Visual explorer and provenance

Every design here also works with the CLI-workflow tools slice 10.7C
added test coverage for:

```bash
forge inspect --dot plugins/vision_pipeline_demo/forge/designs/design_full_functional.yml \
  --contracts-from plugins/vision_pipeline_demo/forge/modules.yml
forge inspect --explorer plugins/vision_pipeline_demo/forge/designs/design_full_functional.yml \
  --contracts-from plugins/vision_pipeline_demo/forge/modules.yml \
  --output /tmp/vision_pipeline_explorer.html
forge inspect --provenance plugins/vision_pipeline_demo/forge/designs/design_full_functional.yml \
  --contracts-from plugins/vision_pipeline_demo/forge/modules.yml
```

`--dot` labels each CDC edge with its kind (`async_fifo CDC`, plus
`reset-domain-crossing` where the two instances are also in different
reset domains); `--explorer` opens an offline interactive HTML graph;
`--provenance` reports the plan hash and every emission-relevant input
hash — see `plugins/vision_pipeline_demo/forge/verify/tools/tests/test_cli_workflows.py`
for the full, code-verified behavior these commands produce against this
plugin's own designs.

## Next

- `docs/internal/phase10/preflight.md` — the frozen design decisions and
  every slice's own completion evidence, if you want the full technical
  depth this page intentionally summarizes.
- [Project scope](../explanation/project-scope.md) — what's real today.
