# Latency Model

FORGE lets a plugin author annotate each module in `modules.yml` (or
override it at the design level) with optional timing metadata — how many
clock cycles a module takes to produce an output after receiving an
input. This metadata exists so two things can happen without needing a
full HLS build or simulation run first: a static latency-balance check
across a design's topology, and a comparison between predicted and
observed latency once simulation results exist. Both are described
operationally in [How to Analyze Performance](../how-to/analyze-performance.md);
this page describes the model itself — what the metadata represents, and
how it's resolved and consumed.

## `latency_cycles` / `latency_hint` / `variable_latency`

A `modules.yml` registry entry (or a design-level module entry, which
overrides the registry's value) may declare optional timing metadata,
modeled as `forge.topgen.config.ModuleTiming` and carried on
`Module.timing`:

```yaml
modules:
  - name: hit_decoder
    kind: hls
    latency_hint: 3        # rough manual estimate
```

```yaml
modules:
  - name: fixed_latency_module
    kind: rtl
    latency_cycles: 7      # explicit, authoritative fixed latency
```

```yaml
modules:
  - name: data_dependent_module
    kind: hls
    variable_latency: true # latency is data-dependent, not a fixed cycle count
```

**Resolution order** (used by `forge analyze latency-check` and the
canonical IR's `ResolvedModuleDefinition.latency_cycles`/`.latency_hint`/
`.is_variable_latency`): `latency_cycles` (explicit) > an externally
supplied HLS synthesis report (a runtime overlay from actual build
artifacts, not registry/design data — see
`forge.analysis.hls_reports.extractor`) > `latency_hint` (rough estimate) >
unknown.

**Conflict rule**: `latency_cycles` and `variable_latency: true` are
contradictory — declaring both is a registry validation error
(`forge topgen validate-registry`), not a silent override. Declare exactly
one, or neither (unknown latency).

All three fields are optional; a module that declares none of them has
`Module.timing is None` and behaves identically to a plugin authored
before this field existed.

## How it flows into `LatencyGraph`

`Module.timing` metadata is read by `forge.analysis.latency_static.graph`,
which builds a directed `LatencyGraph` from `design.yml` + `modules.yml`
(the same shared config loader the canonical IR itself uses, deliberately
stopping short of the full matched IR so latency analysis keeps working
before any IP is built). The graph resolves each module's latency using
the priority order above, propagates it along the topology's connections
— accounting for `register_stages`/`delay_cycles` annotations on
connections — and identifies merge points (nodes with two or more
incoming edges) where accumulated latency on every incoming path should
agree. A mismatch there almost always indicates a real bug: data from two
branches arriving at the same consumer on different cycles.

For the commands that run this check, read its report, and compare
predicted latency against simulation-observed latency, see
[How to Analyze Performance](../how-to/analyze-performance.md).
