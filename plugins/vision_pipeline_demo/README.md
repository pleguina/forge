# vision_pipeline_demo

FORGE's domain-neutral vision-pipeline reference project. The full
design is built: 8 real designs sharing one module registry, covering
Sobel edge detection, tile statistics, all five CDC kinds across three
real clock domains, an output packetizer with async-FIFO backpressure, a
3-domain platform wrapper with a runtime-configurable threshold, and two
negative fixtures. `./run_vision_pipeline_demo.sh` runs all of it end to
end (see "Running it" below). Only the optional hls4ml CNN extension
remains unbuilt.

The **quickstart tier** — one HLS module, one RTL module, one clock
domain, no CDC, no tiling — is still the smallest complete example and
the best place to start; see
`docs/tutorials/vision-pipeline-quickstart.md` for a full manual
walkthrough of it, and
`docs/tutorials/vision-pipeline-full-design.md` for the rest.

What makes this different from `plugins/passthrough_demo/` — beyond
mixing HLS and RTL — is the golden model: the golden dataset carries only
input values, no hand-typed expected outputs. Expected outputs are
computed live by a real `GoldenModelProvider`
(`forge/verify/tools/golden_model_provider.py`), routed through
`forge.verification.dataset_service.DatasetService` +
`forge.verification.golden_model.run_golden_model` machinery — the concrete
fix for the "hand-typed, never cross-checked golden values" pattern
every prior reference plugin used. See
`docs/development/adr/0004-golden-model-provider-boundary.md` for the
ownership split behind this.

## What it does

The quickstart pipeline is the simplest complete example:

```
external pixel stream
  -> pixel_normalizer (HLS): normalized = clamp(scale*pixel + offset, 0, 255)
  -> threshold_rtl (RTL):    threshold_mask = (normalized_pixel >= THRESHOLD)
  -> external result stream
```

Every `forge.pixel_stream.v1`-shaped field (`x`/`y`/`frame_id`/`tile_id`/
`end_of_line`/`end_of_frame`) is passed through both modules unchanged.
See `algo/normalizer/pixel_normalizer.cpp`/`algo/rtl/threshold_rtl.v`.

The full design (all 8 designs, `docs/tutorials/vision-pipeline-full-design.md`
has the complete walkthrough) adds: `window_builder_rtl` → `sobel_hls` →
`edge_mask_merge_rtl` (parallel Sobel edge detection, exact-cycle merge
against a delayed threshold branch); `tile_stats_hls` →
`tile_boundary_rtl` → `tile_summary_join_rtl` (per-tile statistics,
bounded→elastic tagged join); all five CDC kinds
(`level_sync`/`pulse_sync`/`mailbox_transfer`/`async_fifo`/`reset_sync`)
across control/pixel/output clock domains; `packetizer_rtl` multiplexing
both record kinds onto one `forge.packet_stream.v1` with real
throughput/backpressure; and a 3-domain platform wrapper with a
runtime-configurable threshold delivered via `mailbox_transfer`.

## Layout

```
plugins/vision_pipeline_demo/
├── algo/
│   ├── normalizer/pixel_normalizer.{cpp,h}   ← authored: HLS, quickstart
│   ├── sobel/sobel_hls.{cpp,h}                ← authored: HLS, edge detection
│   ├── tile_stats/tile_stats_hls.{cpp,h}      ← authored: HLS, per-tile stats
│   └── rtl/*.v                                ← authored: 15 RTL modules (threshold,
│                                                  window builder, merge, CDC primitives,
│                                                  packers, packetizer, ...)
├── datasets/                                   ← authored: DatasetService adapters
│                                                  (synthetic/image-folder/numpy), CLI
├── forge/
│   ├── modules.yml                            ← authored: 19 modules (3 hls + 16 rtl)
│   ├── designs/                                ← authored: 8 real designs + 2 negative
│   │   ├── design.yml                             fixtures (invalid_direct_bus_cdc.yml,
│   │   ├── design_pixel_result.yml                invalid_fifo_depth_packetizer.yml)
│   │   ├── design_tile_stats.yml
│   │   ├── design_cdc.yml
│   │   ├── design_packetizer.yml
│   │   ├── design_full_functional.yml
│   │   └── design_platform_wrapper.yml
│   ├── interfaces/*.interface.yaml            ← authored: per-module contracts
│   └── verify/
│       ├── design.verification.yml            ← authored: 9 flows (1 hls_csim, 8 full_chip_rtl)
│       ├── schemas/data/*.xml                 ← authored: 3 <in>-only golden datasets
│       ├── include/, src/, tests/, CMakeLists.txt  ← authored: HLS C-sim verif harness
│       ├── tools/
│       │   ├── bootstrap.py               ← authored: plugin registration
│       │   ├── dataset_adapter.py         ← authored: layer-B adapter
│       │   ├── golden_model_provider.py   ← authored: the real golden model
│       │   ├── gen_stimulus*.py           ← authored: one xsim stimulus generator per flow
│       │   └── tests/test_cli_workflows.py ← authored: forge build/inspect CLI coverage
│       └── <flow_name>_xsim/, pixel_normalizer_csim/  ← generated: verify.flow.yml, tb, wave.tcl
└── README.md
```

Deliberately **not** included: a standalone `single_module_rtl` verify
flow for `pixel_normalizer` — its generated testbench would drive the
raw Vitis-synthesized RTL's port names directly, which aren't known
until real HLS synthesis has actually run once. Every `full_chip_rtl`
flow exercises HLS modules through FORGE's own generator instead, which
handles that translation via the interface contract.

## Running it

The full, real command sequence for the quickstart tier alone (2 module
instances, 1 clock, 2 flows):

```bash
pip install -e forge/

forge topgen validate           plugins/vision_pipeline_demo/forge/designs/design.yml
forge topgen validate-registry  plugins/vision_pipeline_demo/forge/modules.yml

# Requires Vitis HLS on PATH:
forge hls gen-tcl --hls-config plugins/vision_pipeline_demo/forge/modules.yml \
  --output-dir build_hls_vision_pipeline_demo
forge hls run --registry plugins/vision_pipeline_demo/forge/modules.yml \
  --hls-build-root build_hls_vision_pipeline_demo --stages csim,synth \
  --modules pixel_normalizer

forge topgen gen-top plugins/vision_pipeline_demo/forge/designs/design.yml \
  --mode verilog \
  --consumer-root . \
  --contracts-from plugins/vision_pipeline_demo/forge/modules.yml \
  --hls-build-root build_hls_vision_pipeline_demo \
  --output gen-top/design_vision_pipeline_quickstart/algo_top.v

forge verify generate plugins/vision_pipeline_demo/forge/verify/design.verification.yml
python3 plugins/vision_pipeline_demo/forge/verify/tools/gen_stimulus.py \
  --flow quickstart_pipeline_xsim --event-id 0
forge verify doctor plugins/vision_pipeline_demo/forge/verify/design.verification.yml

# Requires Vivado xsim on PATH:
forge verify run plugins/vision_pipeline_demo/forge/verify/quickstart_pipeline_xsim/verify.flow.yml \
  --plugin vision_pipeline_demo --consumer-root .
```

For everything else — all 8 designs, all 9 flows, both negative
fixtures — use the real shortcut script from the repo root:

```bash
./run_vision_pipeline_demo.sh              # full run, all designs/flows
./run_vision_pipeline_demo.sh --skip-hls   # reuse an existing HLS build
./run_vision_pipeline_demo.sh --no-clean   # skip the pre-run artifact wipe
```

It builds all 3 HLS modules, gen-tops all 8 designs, regenerates every
verification flow, drives each flow's own `gen_stimulus_*.py`, runs
`forge verify doctor`, then runs all 9 flows — checking the two negative
fixtures for their *expected* failure rather than treating it as a bug.
See `docs/tutorials/vision-pipeline-full-design.md` for the full
walkthrough, including how to run any one design/flow manually.

## Golden datasets

- `forge/verify/schemas/data/vision_pipeline_quickstart_golden.xml` — 64
  events, one per pixel of a synthetic 8x8 ramp frame
  (`pixel = (i*4) & 0xFF`). Used by the quickstart, pixel-result,
  tile-statistics, packetizer, and CDC flows.
- `forge/verify/schemas/data/vision_pipeline_full_functional_golden.xml`
  — 256 events, a real 16x16 checkerboard frame (2x2 tile grid).
- `forge/verify/schemas/data/vision_pipeline_platform_wrapper_golden.xml`
  — 128 events, two 8x8 frames back to back (ramp then checkerboard),
  proving a real mailbox-written threshold change actually changes
  pipeline behavior at the next frame boundary.

No `<golden>` tags in any of them — see "What makes this different"
above.
