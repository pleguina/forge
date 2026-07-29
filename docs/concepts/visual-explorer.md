# Visual Design Explorer

FORGE can render any resolved design as an interactive graph — a way to
*see* a topology (modules, instances, connections, wiring provenance,
diagnostics) rather than read it out of YAML. This page describes the
concept; for the actual CLI command that generates it, see
[Using the visual explorer](../how-to/use-visual-explorer.md).

## `DesignGraph`: a projection, never a second source of truth

`forge.analyze.design_explorer.graph_model.DesignGraph` is a
deterministic, read-only **visualization projection** of exactly one
canonical IR (`forge.ir.model.ResolvedProject`). This is a load-bearing
invariant of the module, not just a design preference: `DesignGraph` may
enrich IR data with external overlays (latency, maturity, verification
entry points), but it never independently resolves topology, infers
semantics, or in any way changes what the design *means*. Every fact
`DesignGraph` states about topology must already be a fact
`ResolvedProject` states — the graph model only re-shapes and enriches,
it never invents.

Nodes are explicit and typed (`GraphNodeKind`): `INSTANCE` nodes for real
`ResolvedInstance`s, plus three synthesized kinds
(`EXTERNAL_PORT`/`MODULE_GROUP`/`DOMAIN_GROUP`) that materialize concepts
the IR represents structurally but doesn't give a first-class node id —
for example, a real IR connection whose producer or consumer endpoint is
the `"$external"` or `"$tie_off"` sentinel gets a real, stable
`EXTERNAL_PORT` node to attach to, rather than a dangling or fabricated
endpoint. Edges carry their real `wiring_method`
(`contract_wiring`/`topology_group`/`port_map`/`auto_match`/`heuristic`) —
the same evidence `ResolvedConnection` already carries — and per-module
maturity is a real aggregation over these methods (`CONTRACT` when every
connection touching a module is contract-driven, `COMPATIBILITY` when
every one is auto-match/heuristic fallback, `MIXED` otherwise), not a
lossy boolean.

`DesignGraph` also carries its own provenance back to the IR it was built
from (source IR schema version, source IR content hash, overlay hashes),
so a rendered graph's identity is itself reproducible and auditable —
never derived from generation time, current working directory, or
client-side layout state (node positions are computed client-side by
Cytoscape.js at render time and are never stored in the graph model
itself).

## What gets generated

Two renderers consume the same `DesignGraph`:

- **`dot_renderer`** — deterministic Graphviz DOT text (plain string
  templating, no `graphviz`/`pydot` binding required — DOT is a text
  format). SVG rendering shells out to the real `dot` binary if it's on
  `PATH`; DOT text generation itself never requires it.
- **`html_renderer`** — a self-contained, offline, interactive HTML file.
  Interactivity (pan/zoom, search, collapse/expand, filters, overlays,
  a diagnostics-only view, path-to/from-node, physical-port expansion,
  a selected-object details panel) is one inline `<script>` reading a
  JSON data island embedded directly in the page — no server round-trip,
  fully functional offline once the file exists.

## The offline guarantee is tested, not just claimed

The HTML explorer vendors Cytoscape.js's core rendering library directly
into the generated page rather than loading it from a CDN. This is
verified, not just asserted in prose:
`forge/tests/test_design_explorer_html_renderer.py` renders the explorer
for both real reference plugins (`passthrough_demo`, `trigger_demo`) and
asserts the output contains none of the concrete mechanisms that would
cause a network request at view time — no `<script src=...>`, no
`<link href=...>`, no `fetch(`, no `XMLHttpRequest`, no `WebSocket`, no
external non-`data:` image/font URL. It's a precise, non-blanket check
(it doesn't ban the string `"https://"` outright, which would
false-positive on the vendor library's own license comment citing its
upstream source) — but it does mean the no-CDN-dependency guarantee is a
real regression-tested property of the output, not just documentation.

## Next

[Using the visual explorer](../how-to/use-visual-explorer.md) covers the
actual `forge inspect`/`forge report` flags that produce this output.
