# ADR 0005: Package ownership boundaries and naming convention

## Status

Accepted and **fully executed, with no compatibility shims**:
`forge/framework/` → `forge/integration/`, `forge/verify/` →
`forge/verification/`, `forge/analyze/` → `forge/analysis/`, and
`forge/topgen/` split into `forge/contracts/` + `forge/generation/`. Every
rename is a clean, breaking move — the old import path was deleted
outright, not aliased.

**History, for anyone wondering why real plugin code once imported
`forge.verify`/`forge.analyze` directly**: this rename was originally
executed with compatibility shims for `verify`→`verification` and
`analyze`→`analysis` (kept permanent, since
`plugins/vision_pipeline_demo/forge/verify/tools/bootstrap.py` and three
other plugin tool files imported `forge.verify.*`/`forge.analyze.*`
submodules directly) and a deprecation-window shim for the `topgen`
split (since `forge.topgen.generators`/`forge.topgen.ip` were part of the
frozen public Python API). All three shims were built and verified —
then deleted in full once it was confirmed that FORGE has no public
release yet and no consumer outside this repository has installed it or
written code against any of the old names. The in-repo plugins that used
the old paths were themselves updated in the same pass. Carrying
permanent compatibility aliases for names nobody outside this repo has
ever depended on was pure complexity with no corresponding benefit — see
[MIGRATION.md](../migration.md) for the same reasoning applied
consistently across every rename in this project, including
`framework`→`integration`, which was always a breaking rename with no
shim. If FORGE ships a real public release and later needs to rename a
package again, *that* rename should get a deprecation-window shim; this
one didn't need one because there was nothing yet to be compatible with.

One correction found while originally executing the `verify` rename (kept
here because it still explains why `forge.analyze`'s consumer surface was
non-trivial, not because a shim still exists): `forge.analyze` turned out
to have the **same** plugin-facing dependency `forge.verify` did —
`plugins/vision_pipeline_demo/forge/verify/tools/bootstrap.py` itself
imported `forge.analyze.dashboards.attachments` directly (an earlier
version of this ADR claimed no plugin depended on `forge.analyze`; that
was wrong). A second correction found while executing the `topgen` split:
`config.py` (`DesignConfig`/`Module`/`Connection`) turned out to be
depended on by `forge/contracts/` itself (6 of its files need
`DesignConfig`), not just by the generators — it joined `forge/contracts/`
rather than `forge/generation/` as originally sketched, keeping the
dependency direction strictly one-way (`generation` → `contracts`, never
the reverse).

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

**No compatibility shim policy (pre-release)**: for as long as FORGE has
no public release and no confirmed external consumer, a package rename
is executed as a clean `git mv` plus a full fix of every internal and
in-repo-plugin consumer — never as a shim at the old path. This keeps the
tree free of duplicate-looking directories and avoids shipping dead
aliases nobody asked for. Once FORGE has real external consumers, a
future rename should reconsider a deprecation-window shim on a
case-by-case basis.

## Package tree (as executed)

```
forge/
  core/          shared mechanisms only (see boundary rule above)
  ir/            canonical resolved-design IR
  contracts/     config (DesignConfig/Module/Connection schema),
                 cardinality, CDC, contract loading/verification,
                 coordinates, domains, matching, topology derivation
  generation/    validation, migration helpers, and the structural
                 Verilog/VHDL/Block-Design generators — depends on
                 contracts/, never the reverse
  integration/   external framework/ABI import, detector I/O
                 resolution, payload generation
  verification/  backends, datasets, golden-model runner, flows,
                 results, stimulus
  analysis/      latency, throughput, HLS reports, design explorer,
                 dashboards, plots
  hls/           unchanged
  docsgen/       unchanged

  # no framework/, verify/, analyze/, or topgen/ directories —
  # deleted outright, no compat shim at any old path.
```

## `forge/verify` → `forge/verification`

`git mv forge/verify forge/verification`, then every internal absolute
self-import (`forge.verify.X` → `forge.verification.X`) and every
external consumer across `forge/`, `plugins/`, `docs/` fixed in the same
pass, including the in-repo plugin `bootstrap.py` files that imported
`forge.verify.plugin_registry` directly. `forge/verify/__init__.py` had
run a real import-time side effect
(`_register_framework_backends()`, registering xsim/csim/verilator into
a global registry) — this now runs once, at `forge.verification` import
time, with no shim layer relaying it.

## `forge/analyze` → `forge/analysis`

Same recipe as `verify`, adapted for a nested-subpackage tree (8
subpackages — `dashboards`, `hls_reports`, `latency_runtime`,
`latency_static`, `result_plots`, `design_explorer`, `throughput_static`,
`throughput_runtime` — plus one top-level module, `latency_model.py`).
`forge.analysis.design_explorer` is part of the frozen public API (real
`__all__`). The in-repo plugin tool files that previously imported
`forge.analyze.dashboards.attachments.register_report_attachment_provider`,
`forge.analyze.hls_reports.extractor.collect_reports`, and
`forge.analyze.throughput_static.model.build_static_throughput_analysis`
directly were updated to the `forge.analysis.*` path in the same commit.

## `forge/topgen` → `forge/contracts` + `forge/generation`

A split, not a 1:1 rename. `git mv forge/topgen/ip forge/contracts`;
`forge/topgen/{config,validation,migrate}.py` and
`forge/topgen/generators/` moved into a new `forge/generation/`.
`forge.topgen.generators` and `forge.topgen.ip` had been part of the
frozen public Python API (`docs/reference/public-python-api.md`), which
is now regenerated to list `forge.contracts`/`forge.generation.generators`
instead — no alias kept at the old dotted path. `forge/ir/build.py` and
`forge/ir/model.py` (not moving) had relative imports reaching into the
old `topgen/`, corrected to `forge/contracts`/`forge/generation` — the
highest-value correctness check here, since `forge/ir/` is this repo's
most heavily-tested subsystem. Also found and fixed one lazy,
function-body-level relative import (`forge/contracts/config.py`'s
`coordinate_key()` did `from .ip.coordinates import ...`, missed by the
initial line-anchored sweep since it wasn't at module level) and several
pre-existing bare `from topgen.X import Y` test imports (a recurring
sys.path-quirk bug class, also found and fixed during the `analyze`
rename).

## Consequences

- New subsystem code goes into the noun that names its capability, never
  into `core/` because "it didn't fit anywhere else."
- CLI command names (`forge verify`, `forge analyze`, `forge framework`)
  are stable independent of the packages backing them; a future package
  rename is not, by itself, a CLI-breaking change.
- No package in `forge/` has a compatibility alias at an old name. Any
  future rename follows the same no-shim policy until FORGE has a real
  public release with confirmed external consumers to be compatible
  with.
