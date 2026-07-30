# vision_pipeline_demo

The **quickstart tier** of FORGE's vision-pipeline reference project
(release-plan Phase 10, slice 10.1 — see
`docs/internal/phase10/preflight.md` and
`docs/internal/phase10/vision_pipeline_reference_project.md` for the full,
much larger design this is the first rung of). Deliberately small: one
HLS module, one RTL module, one clock domain, no CDC, no tiling — the
fuller design (Sobel, tile statistics, CDC, throughput/backpressure) is
later slices, not attempted here.

What makes this different from `plugins/passthrough_demo/` — beyond
mixing HLS and RTL — is the golden model: the golden dataset carries only
input values, no hand-typed expected outputs. Expected outputs are
computed live by a real `GoldenModelProvider`
(`forge/verify/tools/golden_model_provider.py`), routed through the new
`forge.verify.dataset_service.DatasetService` +
`forge.verify.golden_model.run_golden_model` machinery (release-plan
Phase 10, slice 10.0A) — the concrete fix for the "hand-typed, never
cross-checked golden values" pattern every prior reference plugin used.

## What it does

```
external pixel stream
  -> pixel_normalizer (HLS): normalized = clamp(scale*pixel + offset, 0, 255)
  -> threshold_rtl (RTL):    threshold_mask = (normalized_pixel >= THRESHOLD)
  -> external result stream
```

Every `forge.pixel_stream.v1`-shaped field (`x`/`y`/`frame_id`/`tile_id`/
`end_of_line`/`end_of_frame`) is passed through both modules unchanged —
`tile_id` is fixed at 0 this slice (no real tiling exists until slice
10.3). See `algo/normalizer/pixel_normalizer.cpp`/`algo/rtl/threshold_rtl.v`.

## Layout

```
plugins/vision_pipeline_demo/
├── algo/
│   ├── normalizer/pixel_normalizer.{cpp,h}   ← authored: HLS module
│   └── rtl/threshold_rtl.v                    ← authored: RTL module
├── forge/
│   ├── modules.yml                            ← authored: 2 modules (hls + rtl)
│   ├── designs/design.yml                     ← authored: 2 instances, 1 connection
│   ├── interfaces/*.interface.yaml            ← authored: per-module contracts
│   └── verify/
│       ├── design.verification.yml            ← authored: 2 flows (hls_csim, full_chip_rtl)
│       ├── schemas/data/vision_pipeline_quickstart_golden.xml  ← authored: <in>-only dataset
│       ├── include/, src/, tests/, CMakeLists.txt  ← authored: HLS C-sim verif harness
│       ├── tools/
│       │   ├── bootstrap.py               ← authored: plugin registration
│       │   ├── dataset_adapter.py         ← authored: layer-B adapter
│       │   ├── golden_model_provider.py   ← authored: the real golden model
│       │   └── gen_stimulus.py            ← authored: xsim stimulus generator
│       └── quickstart_pipeline_xsim/      ← generated: verify.flow.yml, tb, wave.tcl
└── README.md
```

Deliberately **not** included this slice: a standalone `single_module_rtl`
verify flow for `pixel_normalizer` — its generated testbench would drive
the raw Vitis-synthesized RTL's port names directly, which aren't known
until real HLS synthesis has actually run once. The `full_chip_rtl` flow
exercises the same module through FORGE's own generator instead, which
handles that translation via the interface contract.

## Running it

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

Or the shortcut: `./run_vision_pipeline_demo.sh` (mirrors
`run_trigger_demo.sh` exactly, including its `--skip-hls` escape hatch).

## Golden dataset

`forge/verify/schemas/data/vision_pipeline_quickstart_golden.xml` — 64
events, one per pixel of a synthetic 8x8 ramp frame
(`pixel = (i*4) & 0xFF`). No `<golden>` tags — see "What makes this
different" above.
