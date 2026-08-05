# 01 — Quickstart

**Step in this chapter:** `quickstart` · **Tools needed:** Python, Vitis HLS, Vivado XSim

## Goal

Build and run the smallest possible mixed HLS/RTL pipeline in FORGE, end
to end, so every later chapter can assume you've seen the basic
validate → generate → verify shape once.

## What you will learn

- How a `design.yml` + `modules.yml` pair becomes real generated RTL.
- How FORGE tells project-authored files apart from generated ones.
- How a golden model replaces hand-typed expected values.

## Starting design

There is no smaller design in this tutorial — this is the first rung.

## What you add

```
external pixel stream
  -> pixel_normalizer (HLS): normalized = clamp(scale*pixel + offset, 0, 255)
  -> threshold_rtl (RTL):    threshold_mask = (normalized_pixel >= THRESHOLD)
  -> external result stream
```

Two module instances, one clock domain, no CDC, no tiling.

## Files you edit

| File | Ownership |
|---|---|
| `plugins/vision_pipeline_demo/algo/normalizer/pixel_normalizer.cpp` | **PROJECT SOURCE** |
| `plugins/vision_pipeline_demo/algo/rtl/threshold_rtl.v` | **PROJECT SOURCE** |
| `plugins/vision_pipeline_demo/forge/modules.yml` | **PROJECT SOURCE** |
| `plugins/vision_pipeline_demo/forge/designs/design.yml` | **PROJECT SOURCE** |

## What FORGE generates

| Artifact | Ownership |
|---|---|
| `gen-top/design_vision_pipeline_quickstart/algo_top.v` | **FORGE GENERATED** |
| `plugins/vision_pipeline_demo/forge/verify/quickstart_pipeline_xsim/verify.flow.yml`, `tb_algo_top.sv`, `stimulus_current.svh` | **FORGE GENERATED** |
| `build_hls_vision_pipeline_demo/pixel_normalizer/solution1/` | **TOOLCHAIN OUTPUT** |

## Command to run

The one-line version, real and tested:

```bash
pip install -e forge/
./run_vision_pipeline_demo.sh --step quickstart
```

Or the full manual sequence, so you can see what that one line does:

```bash
forge topgen validate           plugins/vision_pipeline_demo/forge/designs/design.yml
forge topgen validate-registry  plugins/vision_pipeline_demo/forge/modules.yml

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

export CSIM_TB_TB_PIXEL_NORMALIZER="build_hls_vision_pipeline_demo/pixel_normalizer/solution1/csim/build/csim.exe"
forge verify run plugins/vision_pipeline_demo/forge/verify/pixel_normalizer_csim/verify.flow.yml \
  --plugin vision_pipeline_demo --consumer-root .
forge verify run plugins/vision_pipeline_demo/forge/verify/quickstart_pipeline_xsim/verify.flow.yml \
  --plugin vision_pipeline_demo --consumer-root .
```

## Expected terminal result

```
Scoreboard check: PASS
...
All 2 flow(s)/check(s) behaved as expected.
```

Both flows pass: `pixel_normalizer_csim` exercises just the HLS module's
C-sim binary; `quickstart_pipeline_xsim` runs the generated `algo_top`
end to end. A passing run means the live golden-model output —
normalized pixel, threshold mask, valid — matched the RTL exactly.

## Artifacts to inspect

- `gen-top/design_vision_pipeline_quickstart/algo_top.v` — the generated
  top level. Port names are instance-prefixed (`norm_x`,
  `thresh_out_pixel`, …), not the bare interface-contract role names.
- `plugins/vision_pipeline_demo/forge/verify/quickstart_pipeline_xsim/xsim_work/simulate.log`
  — the real Vivado xsim transcript.

## Visual result

Figures for this chapter (input/normalized/threshold-mask panels) are
not generated yet — that's later tutorial work (project-specific
visualization). Today, inspect the real pass/fail scoreboard output
shown above; nothing here is a rendered image yet.

## Why the capability matters

Every later design in this tutorial reuses this exact validate →
gen-top → verify generate → verify run shape. Learning it once here,
on the smallest possible design, means chapter 03 onward can focus on
just what's new.

## Common failure

No standalone `single_module_rtl` flow exists for `pixel_normalizer` —
its generated testbench would drive the raw Vitis-synthesized RTL's port
names directly, which aren't known until real HLS synthesis has
actually run once. `quickstart_pipeline_xsim` exercises the same module
through FORGE's own generator instead, which handles that translation
via the interface contract.

## What changed from the previous chapter

Nothing — this is the first chapter.

## Next chapter

[02 — Project structure and contracts](02-project-structure-and-contracts.md)
