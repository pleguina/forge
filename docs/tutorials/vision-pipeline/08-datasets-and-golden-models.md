# 08 — Datasets and golden models

**Step in this chapter:** *(none — reading chapter, uses the datasets CLI directly)* · **Tools needed:** Python

## Goal

Understand where input data comes from and where expected output comes
from — two deliberately separate concerns in this plugin.

## What you will learn

- Why dataset loading is project-owned, not a FORGE-core capability today.
- The three real dataset adapters this plugin ships and when to use each.
- Why every golden dataset in this tutorial carries only inputs, never hand-typed expected values.

## Starting design

None — this chapter is about the datasets every design so far has
consumed, not a new topology.

## What you add

Nothing new is built. You generate a dataset and read a golden model.

## Files you edit

| File | Ownership |
|---|---|
| `plugins/vision_pipeline_demo/datasets/adapters/synthetic.py`, `image_folder.py`, `numpy_array.py` | **PROJECT SOURCE** |
| `plugins/vision_pipeline_demo/forge/verify/tools/golden_model_provider.py` | **PROJECT SOURCE** |

## What FORGE generates

Nothing from FORGE core itself — dataset generation and golden-model
computation are both project-owned (see
`docs/development/adr/0001-dataset-ownership-boundary.md` and
`docs/development/adr/0004-golden-model-provider-boundary.md`).

## Command to run

```bash
python3 plugins/vision_pipeline_demo/datasets/cli.py generate-synthetic \
  --output /tmp/vpd_dataset_demo --dataset-id demo_ramp \
  --width 8 --height 8 --pattern ramp --event-count 1
```

## Expected terminal result

```
Wrote /tmp/vpd_dataset_demo/demo_ramp.xml
Wrote /tmp/vpd_dataset_demo/demo_ramp.manifest.yml
```

Both files carry only `<in>` values — no `<golden>` tags. That's
deliberate: every flow in this tutorial computes its expected output
live, from a real `GoldenModelProvider`, not from a value some earlier
session hand-typed and never cross-checked.

## Artifacts to inspect

Three real adapters, each converting a different source into the same
canonical `DatasetEvent` shape:

| Adapter | Source | Used by |
|---|---|---|
| `SyntheticPatternAdapter` | Generated patterns (ramp, checkerboard, noise, …), deterministic and seeded | every flow in this tutorial's mandatory path |
| `ImageFolderAdapter` | A directory of PNG/PGM/JPEG/BMP images | optional, for your own images |
| `NumpyArrayAdapter` | `.npy`/`.npz` arrays under an explicit `HW`/`NHW`/`NHWC` layout | optional, for your own arrays |

Five real golden-model providers in
`forge/verify/tools/golden_model_provider.py`, one per verified path —
each independently reimplements the exact algorithm its HLS/RTL
counterpart implements (not a restatement derived from the RTL/HLS
source, which would defeat the point of an independent cross-check):

| Provider | Verifies |
|---|---|
| `QuickstartNormalizerThresholdProvider` | chapter 01 |
| `PixelResultProvider` | chapters 03–04 |
| `TileStatsProvider` | chapter 05 |
| `PacketizerProvider` | chapter 07 (reuses the two above for field math, owns only the 128-bit packing) |
| `PlatformWrapperProvider` | chapter 10 (adds a real per-frame threshold schedule) |

## Visual result

Not applicable to this chapter.

## Why the capability matters

A golden dataset with hand-typed expected values can drift from the
real algorithm silently — a bug in both the RTL/HLS and the hand-typed
value would never be caught. Computing expected output live, from an
independently-implemented model, is what actually proves the RTL/HLS
matches the intended algorithm rather than just matching itself.

## Common failure

BSDS500/Fashion-MNIST importers are intentionally not wired up — they'd
require a network download, which conflicts with keeping the mandatory
tutorial path offline-capable. Use `generate-synthetic` or your own
local images/arrays instead.

## What changed from the previous chapter

Nothing built — this chapter explains a concern every previous chapter's
flow already depended on.

## Next chapter

[09 — Full-functional design](09-full-functional-design.md)
