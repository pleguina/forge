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
modeled as `forge.contracts.config.ModuleTiming` and carried on
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

## `input_alignment`: what a merge point requires

By default every input of a merge point must arrive on the same cycle. A
registry entry can declare otherwise with `input_alignment`
(`forge.contracts.config.InputAlignment`):

```yaml
modules:
  - name: lookup
    kind: hls
    input_alignment:
      offsets: {writer: 4}   # writer's data arrives 4 cycles after the others
  - name: collector
    kind: hls
    input_alignment: {kind: window, window_cycles: 4}
  - name: tagged_join
    kind: hls
    input_alignment: {kind: framed}
```

- `exact` (the default): all inputs on the same cycle.
- `window`: the inputs may spread over at most `window_cycles` cycles.
- `framed`: the inputs carry their own framing (an in-band frame or event
  tag), so no cycle relation between them is required.
- `offsets` (any kind): per source module (its design name or its registry
  entry), how many cycles after the module's other inputs that source must
  arrive. This is a protocol skew of
  the consuming module that no latency report can provide.

`forge analyze latency-check` applies the declaration when it compares the
paths into the module. A design-level module entry can declare or override
it, like the latency fields.

## `delay_cycles: auto`

A connection or topology group may declare `delay_cycles: auto` instead of
a number:

```yaml
connections:
  - from: fast_branch
    to: merge
    port_map: [[dout, in_fast]]
    delay_cycles: auto
```

`forge topgen gen-top` (and `forge build`) then computes the depth from
the latency graph: each `auto` input of a merge point is delayed to the
latest arrival at that merge point, after its `input_alignment` offsets.
A `framed` merge point needs no delay, so its `auto` inputs resolve to 0.
Merge points are resolved in topological order, so a depth chosen at one
merge point is part of every path below it. The depths follow the module
latencies (`latency_cycles`, then the HLS reports under
`--hls-build-root`, then `latency_hint`), so a re-synthesised module with a
different latency changes the generated delays without an edit to the
design. `forge analyze latency-check` resolves the same depths before it
checks, and lists them in its report; `build_manifest.json` records them
under `resolved_auto_delays`.

Generation stops with an error when a depth cannot be resolved: a path into
the merge point has unknown latency, one instance pair mixes `auto` and
fixed connections, one connection would need different depths on different
instances, or the merge point is on a feedback loop. Fixed inputs are never
changed: a fixed input that arrives late is reported as a mismatch. `auto`
is not accepted together with `boundary`, whose depth is a physical
register chain and must be given explicitly.

For the commands that run this check, read its report, and compare
predicted latency against simulation-observed latency, see
[How to Analyze Performance](../how-to/analyze-performance.md).
