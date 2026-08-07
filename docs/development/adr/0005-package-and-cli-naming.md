# ADR 0005: Package ownership boundaries and naming convention

## Status

Accepted. `forge/framework/` → `forge/integration/`,
`forge/verify/` → `forge/verification/`, and `forge/analyze/` →
`forge/analysis/` (both with a permanent compatibility shim at the old
path) have been executed under this decision. The `forge/topgen` split
is **not yet done** — see "Deferred renames" below. One correction found
while executing the `verify` rename: `forge.analyze` turned out to have
the **same** plugin-facing dependency `forge.verify` did —
`plugins/vision_pipeline_demo/forge/verify/tools/bootstrap.py` itself
imports `forge.analyze.dashboards.attachments` directly — so it got the
same permanent-shim treatment as `verify`, not a lighter one (an earlier
version of this ADR claimed otherwise; that was wrong).

## Context

`forge/`'s package tree mixed several unrelated naming conventions with
no documented rule behind any of them: standard architectural names
(`core`, `ir`, `hls`), verbs used as package names (`analyze`, `verify`),
an implementation-history name (`topgen`, which owns far more than "top
generation" — see below), and one name so vague it was self-referential
(`framework/`, inside a package that is itself already a framework).

Two concrete problems came from this, not just aesthetics:

1. **`core/` had no stated boundary**, so nothing prevented it from
   becoming a dumping ground for "things that didn't obviously belong
   anywhere else" rather than genuinely cross-cutting mechanisms.
2. **`forge/topgen/ip/` already held more than top-generation internals**
   before this ADR — CDC, contract verification, matching, cardinality,
   and domain resolution are foundational design semantics that
   `forge/verify`, `forge/ir`, and `forge/analyze` all depend on, not
   private implementation details of generating a top-level netlist.
   Phase 10 added CDC kinds, protocol semantics, and throughput
   transformations on top of this already-wrong boundary.

## Decision

**Packages are nouns; CLI commands are verbs.** `forge verify`,
`forge analyze`, and `forge build` are good command names — a user
invokes an action. `forge.verification`, `forge.analysis` are better
package names — a package owns a capability, not an action. The two
vocabularies are allowed to diverge: renaming a package must not, by
itself, require renaming the CLI command that uses it.

**`core/` boundary rule**: code belongs in `forge/core/` only when at
least two independent FORGE subsystems depend on it, and it contains no
subsystem-specific semantics. Concretely:

| Module | In `core/`? | Why |
|---|---|---|
| `artifact_schema.py` | Yes | Every generated-artifact type across every subsystem uses the same schema-tagging convention. |
| `schema_version.py` | Yes | Same reason — versioning is a cross-cutting concern, not owned by any one artifact producer. |
| `portable_path.py` | Yes | Path-portability discipline is required by the IR, the design explorer, and provenance alike. |
| CDC validation | No | Subsystem-specific semantics (topology/generation), belongs with contracts. |
| Dataset service | No | Subsystem-specific (verification), belongs with verification. |
| HLS report extraction | No | Subsystem-specific (analysis), belongs with analysis. |

`forge/core/cli/` is treated as an exception to the letter of this rule
(each `forge/core/cli/groups/*.py` file is subsystem-specific CLI
plumbing, not a shared mechanism) but not its spirit: it's the one place
subsystem-specific code is allowed under `core/` because command routing
*is* the cross-cutting concern, and every subsystem needs exactly one
place to register a command group. Nothing else gets this exception.

## Target package tree

```
forge/
  core/            shared mechanisms only (see boundary rule above)
  ir/               canonical resolved-design IR — keep, already clean
  contracts/        cardinality, CDC, contract loading/verification,
                    coordinates, domains, matching, topology derivation
                    (currently forge/topgen/ip/*)
  generation/        config, validation, migration helpers, and the
                    structural Verilog/VHDL/Block-Design generators
                    (currently forge/topgen/{config,validation,migrate}.py
                    + forge/topgen/generators/*)
  integration/       external framework/ABI import, detector I/O
                    resolution, payload generation (was forge/framework/)
  verification/      backends, datasets, golden-model runner, flows,
                    results, stimulus (currently forge/verify/)
  analysis/          latency, throughput, HLS reports, design explorer,
                    dashboards, plots (currently forge/analyze/)
  hls/              keep
  docsgen/          keep
```

## `forge/verify` → `forge/verification` (done)

`forge/verify/__init__.py` ran a real import-time side effect
(`_register_framework_backends()`, registering xsim/csim/verilator into
a global registry) — the shim's `__init__.py` triggers this exactly once
by importing `forge.verification` rather than re-implementing the
registration. Every plugin's `bootstrap.py` does `from
forge.verify.plugin_registry import declare_plugin_bootstrap` directly;
the shim is a real package at `forge/verify/` (one thin
`from forge.verification.X import *` file per real submodule, generated
programmatically), kept **permanently**, not on a deprecation timer —
verified by an identity check (`forge.verify.plugin_registry.declare_plugin_bootstrap
is forge.verification.plugin_registry.declare_plugin_bootstrap`) and a
real `python -m forge.verify --help` invocation, both passing.

## `forge/analyze` → `forge/analysis` (done)

Same recipe as `verify`, adapted for a nested-subpackage tree (8
subpackages — `dashboards`, `hls_reports`, `latency_runtime`,
`latency_static`, `result_plots`, `design_explorer`, `throughput_static`,
`throughput_runtime` — plus one top-level module, `latency_model.py`).
`forge.analyze.design_explorer` is one of the modules already in the
frozen public API (real `__all__`); the shim's `from
forge.analysis.design_explorer import *` respects that `__all__`
directly, verified explicitly (`DesignGraph`/`build_design_graph`
identity checks). Also verified: the exact import statements real plugin
tool files use (`forge.analyze.dashboards.attachments.register_report_attachment_provider`,
`forge.analyze.hls_reports.extractor.collect_reports`,
`forge.analyze.throughput_static.model.build_static_throughput_analysis`).

## Deferred renames

The `forge/topgen` split into `forge/contracts` + `forge/generation` is
**not yet executed**. `forge.topgen.generators` and `forge.topgen.ip` are
both part of the frozen public Python API
(`docs/reference/public-python-api.md`) today. Splitting them requires a
shim carrying a `DeprecationWarning` for at least one minor release, per
this project's own compatibility policy, and the public-API freeze must
be regenerated *after* the shim lands, not touched mid-rename. It should
be its own reviewed unit: do the rename, add the compat shim, regenerate
the public-API freeze, and prove it with the full test suite **plus** a
clean-venv wheel install **plus** a real smoke test (`forge topgen
gen-top --dry-run`, `forge init`'s full chain) — the same recipe `verify`
and `analyze` just went through.

## Consequences

- New subsystem code goes into the noun that names its capability, never
  into `core/` because "it didn't fit anywhere else."
- CLI command names (`forge verify`, `forge analyze`, `forge framework`)
  are stable independent of the packages backing them; a future package
  rename is not, by itself, a CLI-breaking change.
- `forge/topgen/ip/` should not accumulate more foundational
  contract/CDC/matching semantics while it's still named after top
  generation — new work in that area is a signal the split in this ADR
  is overdue, not a reason to keep deferring it.
