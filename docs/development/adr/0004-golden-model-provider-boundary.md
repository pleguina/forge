# ADR 0004: Golden-model provider boundary

## Status

Accepted.

## Context

Every verification flow in `vision_pipeline_demo` needs an expected
output for each input event. Earlier FORGE reference plugins
(`passthrough_demo`, `trigger_demo`) hand-type expected values into the
golden dataset itself — values that are never independently
cross-checked against the algorithm they're supposed to validate.
`vision_pipeline_demo` instead computes expected outputs live, from a
real model of the algorithm, so the golden dataset only ever needs to
carry inputs.

## Decision

Golden-model computation is split across a clean boundary:

- **The domain math is project-owned.** Each `GoldenModelProvider` in
  `forge/verify/tools/golden_model_provider.py` (one per verified path —
  quickstart normalize+threshold, pixel-result, tile-statistics,
  packetizer, platform-wrapper) implements the *exact same* closed-form
  algorithm the HLS/RTL modules implement, independently, in Python.
  For example, `QuickstartNormalizerThresholdProvider` computes
  `normalized = clamp(scale*pixel + offset, 0, 255)` and
  `threshold_mask = normalized >= threshold` — the identical formula
  `pixel_normalizer.cpp` implements in HLS and `threshold_rtl.v`
  implements in RTL, kept in sync by convention (each provider's module
  docstring names the RTL/HLS files it must match).
- **Invocation determinism and hashing are FORGE-owned.** Providers
  register via `forge.verification.golden_model.register_golden_model_provider`
  and are invoked by `forge.verification.golden_model.run_golden_model`, which
  owns running the provider deterministically and hashing its output —
  never the provider module itself. A provider only computes values; it
  never decides how those values get compared, hashed, or cached.

## Consequences

- Any new verified path in this plugin must add its own
  `GoldenModelProvider` implementing the algorithm independently of the
  HLS/RTL source, not by importing or re-deriving it from that source —
  the value of the golden model is that it's an independent
  cross-check, not a restatement.
- A provider that groups records by a key that isn't unique across the
  full dataset (e.g. `tile_id` alone, when multiple frames share tile
  geometry) will silently conflate unrelated samples. Any provider that
  groups per-tile or per-window output must key on the full identifying
  tuple (e.g. `(frame_id, tile_id)`), not just whichever geometric or
  positional ID happens to repeat across frames.
- FORGE core's `run_golden_model`/determinism/hashing machinery must
  stay domain-neutral — it must never need to know what a "pixel" or a
  "tile" is to run a provider correctly.
