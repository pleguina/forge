# 11 — Inspect, report, and reproduce

<nav class="forge-step-strip" aria-label="Chapter progress" markdown="span">
[01](01-quickstart.md) [02](02-project-structure-and-contracts.md) [03](03-mixed-rtl-hls.md) [04](04-parallel-paths-and-latency.md) [05](05-bounded-and-elastic-processing.md) [06](06-clock-domains-and-cdc.md) [07](07-throughput-backpressure-and-fifos.md) [08](08-datasets-and-golden-models.md) [09](09-full-functional-design.md) [10](10-platform-integration.md) **11** [12](12-diagnostics-and-negative-fixtures.md)
</nav>

*Step 11 of 12*

**Step in this chapter:** *(none — analysis commands over `design_full_functional.yml`/`design_platform_wrapper.yml`)* · **Tools needed:** Python, Graphviz (optional, for `--svg`)

## Goal

Use FORGE's own topology/provenance/report tooling — not simulation —
to inspect a design, prove its build plan is deterministic, and produce
one offline report bundle covering everything this tutorial has built.

## What you will learn

- How to render a design's real topology as DOT/SVG or an interactive offline HTML explorer.
- What a plan hash proves, and what `--accept-plan-hash` is for.
- How `forge report` assembles topology, latency, HLS, and verification sections into one self-contained bundle.

## Starting design

`design_full_functional.yml` (chapter 09) and `design_platform_wrapper.yml`
(chapter 10) — already gen-topped, no new build needed.

## What you add

Nothing built — every command below only reads existing designs.

## Files you edit

None.

## What FORGE generates

| Artifact | Ownership |
|---|---|
| a `.dot`/`.svg` topology rendering | <span class="badge badge-generated">FORGE GENERATED</span> |
| a self-contained interactive explorer `.html` | <span class="badge badge-generated">FORGE GENERATED</span> |
| a provenance manifest `.json` | <span class="badge badge-generated">FORGE GENERATED</span> |
| an offline report bundle (`dashboard.html`, `topology.svg`, `maturity.md`, `latency_check.md`, `hls_summary.md`, `verification_results.md`, `summary.md`) | <span class="badge badge-generated">FORGE GENERATED</span> |

## Command to run

**Topology — DOT and interactive explorer:**

```bash
forge inspect plugins/vision_pipeline_demo/forge/designs/design_full_functional.yml \
  --contracts-from plugins/vision_pipeline_demo/forge/modules.yml \
  --dot /tmp/vpd_full_functional.dot

forge inspect plugins/vision_pipeline_demo/forge/designs/design_full_functional.yml \
  --contracts-from plugins/vision_pipeline_demo/forge/modules.yml \
  --explorer /tmp/vpd_explorer.html
```

**Provenance and plan hash:**

```bash
forge inspect plugins/vision_pipeline_demo/forge/designs/design_full_functional.yml \
  --contracts-from plugins/vision_pipeline_demo/forge/modules.yml \
  --provenance /tmp/vpd_provenance.json

forge build plugins/vision_pipeline_demo/forge/designs/design_full_functional.yml \
  --contracts-from plugins/vision_pipeline_demo/forge/modules.yml \
  --hls-build-root build_hls_vision_pipeline_demo \
  --consumer-root . \
  --plan
```

**Offline report bundle:**

```bash
forge report plugins/vision_pipeline_demo/forge/designs/design_full_functional.yml \
  --contracts-from plugins/vision_pipeline_demo/forge/modules.yml \
  --hls-build-root build_hls_vision_pipeline_demo \
  --output /tmp/vpd_report
```

## Expected terminal result

`--dot`/`--explorer`/`--provenance` each print `✅ ... written to
<path>` followed by a real design summary (module count, connection
count, clock/reset domains). `forge build --plan` prints a plan hash and
artifact list without writing anything (a real, code-verified invariant:
`--plan` alone never writes files).

`forge report` prints a status (`PASS`), real metrics
(`topology_edges: 116`, `latency_mismatches: 0`, `hls_modules: 4` for
`design_full_functional.yml`), and an artifact list including
`dashboard.html`. This command has never needed anything from
`vision_pipeline_demo` beyond a design file and the shared module
registry — every section it produces is a generic FORGE renderer,
reused as-is.

## Artifacts to inspect

- The DOT output labels each CDC edge with its real kind, e.g.
  `async_fifo CDC reset-domain-crossing` — the suffix appears whenever
  the two instances are also in different reset domains, not just
  different clock domains.
- `/tmp/vpd_report/dashboard.html` — open it directly in a browser, no
  server needed. It embeds the topology SVG, the maturity/latency/HLS
  summaries, and (once you pass `--junit-xml`/`--golden-comparison-json`
  from a real `forge verify run`) the verification results too.
- Re-run `forge build --plan --accept-plan-hash <a-wrong-hash>` to see a
  real mismatch: FORGE reports the expected vs. actual hash and refuses
  to proceed, rather than silently accepting a stale plan.

## Visual result

`dashboard.html` and the interactive explorer are the real visual
output this chapter produces — open them directly; nothing here is a
placeholder.

## Why the capability matters

A plan hash lets a CI pipeline or a teammate prove "this generated RTL
came from exactly this design, unchanged" without re-reading every file
by hand. The offline report bundle is the single place a reviewer can
see topology, latency, HLS synthesis, and verification results together
— without running Vivado themselves.

## Common failure

A plan-hash mismatch is not a bug — it's the intended signal that the
design changed since the hash was recorded. [Chapter
12](12-diagnostics-and-negative-fixtures.md) covers the related
staleness-detection workflow (`--explain-staleness`).

## What changed from the previous chapter

Nothing built — this chapter applies FORGE's own analysis tools to
designs chapters 09 and 10 already produced.

## Next chapter

[12 — Diagnostics and negative fixtures](12-diagnostics-and-negative-fixtures.md)
