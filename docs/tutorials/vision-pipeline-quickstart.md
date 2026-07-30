# Vision Pipeline Quickstart: vision_pipeline_demo

`plugins/vision_pipeline_demo/` is the **quickstart tier** of FORGE's
domain-neutral vision-pipeline reference project (release-plan Phase 10,
slice 10.1) — the first rung of a much larger design frozen in
`docs/internal/phase10/preflight.md` and
`docs/internal/phase10/vision_pipeline_reference_project.md`. Deliberately
small: one HLS module, one RTL module, one clock domain, no CDC, no
tiling. Every command below is copied from either
`plugins/vision_pipeline_demo/README.md` or the live `--help` output of
the command itself, so nothing here is aspirational.

What makes this different from [`passthrough_demo`](rtl-example.md) and
[`trigger_demo`](mixed-hls-rtl-example.md) — beyond mixing HLS and RTL —
is the **golden model**: the golden dataset carries only input values, no
hand-typed expected outputs. Expected outputs are computed live by a real
`GoldenModelProvider`, routed through `forge.verify.dataset_service`'s
`DatasetService` and `forge.verify.golden_model.run_golden_model`
(release-plan Phase 10, slice 10.0A) — the fix for the "hand-typed, never
cross-checked golden values" pattern the other two reference plugins
still use.

If you haven't installed FORGE yet, do that first:
[installation](../getting-started/installation.md). If you're new to
FORGE entirely, start with the [golden-path tutorial](golden-path.md)
first — this page assumes you already know the basic
validate → gen-top → verify generate → run shape.

## Pipeline

```
external pixel stream
  -> pixel_normalizer (HLS): normalized = clamp(scale*pixel + offset, 0, 255)
  -> threshold_rtl (RTL):    threshold_mask = (normalized_pixel >= THRESHOLD)
  -> external result stream
```

Every `forge.pixel_stream.v1`-shaped field (`x`/`y`/`frame_id`/`tile_id`/
`end_of_line`/`end_of_frame`) passes through both modules unchanged —
`tile_id` is fixed at 0 this slice (no real tiling exists until a later
slice). See `plugins/vision_pipeline_demo/algo/normalizer/pixel_normalizer.cpp`
and `plugins/vision_pipeline_demo/algo/rtl/threshold_rtl.v`.

## 1. Validate the topology

```bash
forge topgen validate           plugins/vision_pipeline_demo/forge/designs/design.yml
forge topgen validate-registry  plugins/vision_pipeline_demo/forge/modules.yml
```

`design.yml` declares two module instances — `norm` (`pixel_normalizer`,
HLS) and `thresh` (`threshold_rtl`, RTL) — connected by one `port_map`
connection, with `norm`'s inputs and `thresh`'s outputs exposed at the
top level.

## 2. Build the HLS module

```bash
forge hls gen-tcl --hls-config plugins/vision_pipeline_demo/forge/modules.yml \
  --output-dir build_hls_vision_pipeline_demo

forge hls run --registry plugins/vision_pipeline_demo/forge/modules.yml \
  --hls-build-root build_hls_vision_pipeline_demo --stages csim,synth \
  --modules pixel_normalizer
```

Requires Vitis HLS on `PATH`. `gen-top` (next step) needs the real
synthesized RTL to scan for `pixel_normalizer`'s port metadata, so this
must run first.

## 3. Generate the algorithm top level

```bash
forge topgen gen-top plugins/vision_pipeline_demo/forge/designs/design.yml \
  --mode verilog \
  --consumer-root . \
  --contracts-from plugins/vision_pipeline_demo/forge/modules.yml \
  --hls-build-root build_hls_vision_pipeline_demo \
  --output gen-top/design_vision_pipeline_quickstart/algo_top.v
```

Top-level port names are instance-prefixed by the generator — `norm_x`,
`norm_pixel_valid`, `thresh_out_pixel`, `thresh_threshold_mask`,
`thresh_out_valid`, and so on (`<instance name>_<raw port>`), not the
bare interface-contract role names. This matters once you write a
stimulus generator against the generated `algo_top`.

## 4. Generate the verification flow and stimulus

```bash
forge verify generate plugins/vision_pipeline_demo/forge/verify/design.verification.yml
python3 plugins/vision_pipeline_demo/forge/verify/tools/gen_stimulus.py \
  --flow quickstart_pipeline_xsim --event-id 0
```

`gen_stimulus.py` loads the real golden dataset
(`vision_pipeline_quickstart_golden.xml` — 64 events, one per pixel of a
synthetic 8x8 ramp frame) through `DatasetService`, runs the real
`GoldenModelProvider` over it through `run_golden_model`, and writes
`stimulus_current.svh` driving/checking the instance-prefixed signal
names from step 3. Nothing here is hand-typed: change the input dataset
or the golden model's math and the checked values change with it.

## 5. Health-check before running

```bash
forge verify doctor plugins/vision_pipeline_demo/forge/verify/design.verification.yml
```

## 6. Run both flows

```bash
export CSIM_TB_TB_PIXEL_NORMALIZER="build_hls_vision_pipeline_demo/pixel_normalizer/solution1/csim/build/csim.exe"

forge verify run plugins/vision_pipeline_demo/forge/verify/pixel_normalizer_csim/verify.flow.yml \
  --plugin vision_pipeline_demo --consumer-root .

forge verify run plugins/vision_pipeline_demo/forge/verify/quickstart_pipeline_xsim/verify.flow.yml \
  --plugin vision_pipeline_demo --consumer-root .
```

`pixel_normalizer_csim` (`hls_csim`, backend `csim`) exercises just the
HLS module's C-sim binary. `quickstart_pipeline_xsim` (`full_chip_rtl`,
backend `xsim`) runs the generated `algo_top` end to end and requires
Vivado `xsim` on `PATH`. A passing run means the live golden-model
output — normalized pixel, threshold mask, valid — matched the RTL
exactly.

Or the shortcut, which runs all six steps above in order:

```bash
./run_vision_pipeline_demo.sh
```

(`--skip-hls` skips steps 2 and the `pixel_normalizer_csim` flow;
`--no-clean` skips the pre-run artifact cleanup.)

## Deliberately out of scope this slice

No standalone `single_module_rtl` flow exists for `pixel_normalizer` —
its generated testbench would drive the raw Vitis-synthesized RTL's port
names directly, which aren't known until real HLS synthesis has actually
run once. `quickstart_pipeline_xsim` exercises the same module through
FORGE's own generator instead, which handles that translation via the
interface contract. See `plugins/vision_pipeline_demo/README.md` and
`design.verification.yml`'s header comment for the full reasoning.

The fuller design frozen in `docs/internal/phase10/preflight.md` —
parallel HLS filters, edge detection, tile statistics, CDC, multi-clock
throughput/backpressure, a latency-aligned merge, an output packetizer —
is later slices, not attempted here.

## Next

- [Authoring topology contracts](../how-to/author-topology-contracts.md)
  — the general `design.yml`/`modules.yml`/interface-contract reference.
- [The mixed HLS/RTL example](mixed-hls-rtl-example.md) — `trigger_demo`,
  a richer but hand-typed-golden-data pipeline.
- [Project scope](../explanation/project-scope.md) — what's real today
  versus what's still planned for `vision_pipeline_demo`.
