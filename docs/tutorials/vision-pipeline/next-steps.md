# Next steps

You've built and run every real design and flow this reference project
has, in order: a two-module quickstart pipeline, parallel HLS filters
with an aligned merge, bounded/elastic metadata joins, three clock
domains with all five CDC kinds, an output packetizer with real
backpressure, a full-scale 16×16 assembly, and a three-domain
runtime-configurable platform — plus both real negative fixtures.

## Run everything again, in one command

```bash
./run_vision_pipeline_demo.sh
```

8 designs, 9 flows, both negative fixtures checked for their expected
outcome — the same commands `.gitlab-ci.yml`'s
`forge:vision-pipeline-release-gate` job runs on every release.

## Build your own project on these mechanisms

- [Authoring topology contracts](../../how-to/author-topology-contracts.md)
  — the general `design.yml`/`modules.yml`/interface-contract reference
  this entire tutorial builds on.
- [Clock and reset domains](../../concepts/clock-and-reset-domains.md) —
  the concepts chapter 06 exercised concretely.
- [Contracts and protocols](../../concepts/contracts-and-protocols.md) —
  the concepts chapter 02 exercised concretely.

## Other FORGE reference projects

- [The golden-path tutorial](../golden-path.md) — the shortest possible
  FORGE walkthrough, if you want the absolute basics without the vision
  domain.
- [`passthrough_demo`](../rtl-example.md) — a pure-RTL reference project.
- [`trigger_demo`](../mixed-hls-rtl-example.md) — a richer but
  hand-typed-golden-data mixed HLS/RTL pipeline.

## What's not covered here

- **hls4ml** — an optional CNN extension, tracked separately and not
  part of this tutorial's mandatory path.
- **Your own images/arrays** — chapter 08 covered `ImageFolderAdapter`/
  `NumpyArrayAdapter`; try pointing one at your own data.

## Something not working?

Every command in this tutorial is copied from a real, currently-passing
run — if one doesn't behave as documented, that's a real bug worth
reporting, not a typo to work around silently.
