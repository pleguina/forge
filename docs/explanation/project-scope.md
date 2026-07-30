# Project Scope

This is a short, honest status note about what FORGE's example plugins
currently are and are not — not a tutorial, and not a claim that a gap has
been closed.

## Domain-neutral reference project

A complete, domain-neutral streaming reference project — something with
no CMS/OMTF provenance in its naming or shape at all, exercising RTL, HLS,
scalar interfaces, and array/prefix wiring together — is under construction
as `vision_pipeline_demo`, in progressive-complexity slices
(`docs/internal/phase10/preflight.md` and
`docs/internal/phase10/vision_pipeline_reference_project.md` record the
frozen design decisions; `docs/plan/FORGE_release_plan.md`'s Phase 10
tracks the slice sequencing). Only the first slice — the **quickstart
tier** — is built and CI-real today: pixel source → normalization (HLS) →
threshold (RTL) → output, one clock domain, a real golden-model-driven
comparison, no hand-typed expected values. The fuller design the preflight
freezes (parallel HLS filters, edge detection, tile statistics, CDC,
multi-clock throughput/backpressure, a latency-aligned merge, an output
packetizer) is **later slices, not built yet** — nothing on this page or
in the quickstart tutorial should be read as a preview of that later
work's actual implementation.

## What today's tutorials use

FORGE's tutorials use three real plugins currently in the repository:

- [`passthrough_demo`](../tutorials/rtl-example.md) — RTL-only, a single
  trivial registered passthrough module, with no algorithm-specific
  vocabulary in its own naming.
- [`trigger_demo`](../tutorials/mixed-hls-rtl-example.md) — mixed HLS/RTL,
  a realistic 7-module trigger pipeline exercising every supported
  topology-wiring pattern.
- [`vision_pipeline_demo`](../tutorials/vision-pipeline-quickstart.md) —
  mixed HLS/RTL, domain-neutral, golden-model-driven (quickstart tier
  only so far; see above).

All three are real, maintained, CI-exercised plugins — not placeholders —
and each is a good teaching example for its respective purpose (simplest
possible plugin, richest realistic plugin, and the domain-neutral/golden-model
example). `trigger_demo` is still explicitly CMS/OMTF-flavored in its
module names and pipeline shape (hit decoding, trigger logic, φ
coordinates), and even `passthrough_demo`'s home directory sits alongside
`trigger_demo` in a repository whose origin is CMS/OMTF trigger/DAQ
firmware — `vision_pipeline_demo` is the first example plugin with no such
provenance in its naming or shape.

## The framework itself is still enforced domain-agnostic

This naming gap in the *example plugins* is a different question from
whether FORGE's *core* (`forge/`) hardcodes any detector- or
algorithm-specific assumptions — it doesn't, and that's a real,
CI-enforced property, not just a stated intention.
`ci/agnosticism_check.sh` greps `forge/core`, `forge/topgen`, `forge/hls`,
`forge/verify`, `forge/analyze`, and `forge/framework` for a blocklist of
CMS/OMTF-specific vocabulary and fails CI if it finds any — see
[extension APIs](extension-apis.md) for the two concrete override points
(`detector_input_roles`, `reference_period_ns`) that let a plugin bring
its own domain's conventions instead of relying on a hardcoded default.
In short: the framework is domain-neutral by construction and by CI gate
today; a domain-neutral *example plugin* proving that end to end is the
piece that's still on the roadmap.
