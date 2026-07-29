# Project Scope

This is a short, honest status note about what FORGE's example plugins
currently are and are not — not a tutorial, and not a claim that a gap has
been closed.

## Domain-neutral reference project

A complete, domain-neutral streaming reference project — something with
no CMS/OMTF provenance in its naming or shape at all, exercising RTL, HLS,
scalar interfaces, and array/prefix wiring together — is **planned for a
later release phase, not built yet**. `docs/plan/FORGE_release_plan.md`'s
Phase 10 records the intended shape (an `image_pipeline_demo`-style
pipeline: pixel source → normalization RTL → parallel HLS filters → edge
detection → threshold → metadata path → latency-aligned merge → output
packetizer) as the recommended project for that phase. Nothing on this
page should be read as a preview of that project's actual implementation
— it doesn't exist yet, and this page deliberately doesn't get more
specific than the plan does.

## What today's tutorials use instead

Until that reference project exists, FORGE's tutorials use the two real
plugins currently in the repository as the closest available examples:

- [`passthrough_demo`](../tutorials/rtl-example.md) — RTL-only, a single
  trivial registered passthrough module, with no algorithm-specific
  vocabulary in its own naming.
- [`trigger_demo`](../tutorials/mixed-hls-rtl-example.md) — mixed HLS/RTL,
  a realistic 7-module trigger pipeline exercising every supported
  topology-wiring pattern.

Both are real, maintained, CI-exercised plugins — not placeholders — and
both are good teaching examples for their respective purpose (simplest
possible plugin, and richest realistic plugin). Neither is a
genuinely-unrelated-domain example, though: `trigger_demo` is explicitly
CMS/OMTF-flavored in its module names and pipeline shape (hit decoding,
trigger logic, φ coordinates), and even `passthrough_demo`'s home
directory sits alongside `trigger_demo` in a repository whose origin is
CMS/OMTF trigger/DAQ firmware.

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
