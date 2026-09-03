# How to Use the Visual Design Explorer

This is the operational companion to
[the visual explorer concept page](../concepts/visual-explorer.md). There
is a real, wired-in CLI path for generating the explorer — this is not
only reachable via direct Python API.

## Generate it via `forge inspect`

```bash
forge inspect plugins/passthrough_demo/forge/designs/design.yml \
  --contracts-from plugins/passthrough_demo/forge/modules.yml \
  --explorer out/topology_explorer.html
```

`forge inspect` accepts three related, independent output flags:

- `--dot <path>` — write a deterministic Graphviz DOT rendering. Never
  requires the `dot` binary (DOT is a plain text format).
- `--svg <path>` — write an SVG rendering. Requires the real `dot` binary
  on `PATH`; fails loudly and specifically if it isn't, rather than
  silently skipping.
- `--explorer <path>` — write the self-contained, offline, interactive
  HTML explorer described in
  [the concept page](../concepts/visual-explorer.md).

Any combination can be passed in the same invocation; all three render
from the same resolved `DesignGraph`. Open the written HTML file directly
in a browser — no server, no build step, no network access needed.

## Generate it as part of a full report

`forge report` writes a topology section unconditionally as part of its
report bundle — you don't need to ask for the explorer separately if
you're already generating a report:

```bash
forge report plugins/passthrough_demo/forge/designs/design.yml \
  --contracts-from plugins/passthrough_demo/forge/modules.yml \
  --output out/report
```

This writes `out/report/topology.dot` always, `out/report/topology.svg`
when the `dot` binary is available (noted honestly in the command's
diagnostics/next-actions when it isn't, never silently omitted), and
`out/report/topology_explorer.html` — the same interactive explorer
`forge inspect --explorer` produces, generated from the same
`build_design_graph()` call.

## Direct Python API (for scripting or embedding)

If you need the graph model or renderers outside the CLI — for example,
to post-process the graph or embed it in another tool — the same
functions the CLI calls are public:

```python
from forge.analysis.design_explorer.graph_model import build_design_graph
from forge.analysis.design_explorer.html_renderer import render_explorer_html
from forge.ir import build_project_ir_with_match_report

project, _cfg, _match_report = build_project_ir_with_match_report(
    "plugins/passthrough_demo/forge/designs/design.yml",
    contracts_from="plugins/passthrough_demo/forge/modules.yml",
)
graph = build_design_graph(project, source_roots=["plugins/passthrough_demo"])
render_explorer_html(graph, "out/topology_explorer.html")
```

`forge/core/cli/groups/inspect.py` and `forge/core/cli/groups/report.py`
both call exactly these functions — the CLI is a thin wrapper over this
same API, not a separate code path.

## Answer an open decision from the explorer

If the project has connections FORGE refused to make — a producer whose
width and direction fit two different consumers — they appear in the
sidebar under **Open decisions**. Selecting one shows the question, every
candidate, and for each candidate two things: the `forge.yml` entry that
records it, and the equivalent command.

```
connections:
- from: classifier.candidate_out
  to: formatter.candidate_in
```

```
forge connect classifier.candidate_out formatter.candidate_in
```

The page never writes anything and holds no state of its own. The
candidates it shows are the ones `forge check` reports, computed by the
same scan; the answer goes into `forge.yml`, and the next `forge adopt` or
`forge check` reads it from there. That is deliberate: an explorer that
kept its own idea of the topology would be a second source of truth for the
design, and the first thing it would do is disagree with the generator.

Open decisions only appear for a project with a `forge.yml` — one adopted
with `forge adopt`. A design in the older `plugins/<id>/forge/` layout has
no such scan to draw on, and the explorer renders exactly as before.
