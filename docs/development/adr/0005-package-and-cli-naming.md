# ADR 0005: Package ownership boundaries and naming convention

## Status

Accepted and **fully executed**: `forge/framework/` → `forge/integration/`,
`forge/verify/` → `forge/verification/`, `forge/analyze/` →
`forge/analysis/` (both with a permanent compatibility shim at the old
path), and `forge/topgen/` split into `forge/contracts/` +
`forge/generation/` (deprecation-window shim). One correction found while
executing the `verify` rename: `forge.analyze` turned out to have the
**same** plugin-facing dependency `forge.verify` did —
`plugins/vision_pipeline_demo/forge/verify/tools/bootstrap.py` itself
imports `forge.analyze.dashboards.attachments` directly — so it got the
same permanent-shim treatment as `verify`, not a lighter one (an earlier
version of this ADR claimed otherwise; that was wrong). A second
correction found while executing the `topgen` split: `config.py`
(`DesignConfig`/`Module`/`Connection`) turned out to be depended on by
`forge/contracts/` itself (6 of its files need `DesignConfig`), not just
by the generators — it joined `forge/contracts/` rather than
`forge/generation/` as originally sketched below, keeping the
dependency direction strictly one-way (`generation` → `contracts`,
never the reverse).

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

  # deprecation-window compat shim only, no real logic:
  topgen/{generators,ip}/   DeprecationWarning, removable after one
                            minor release once the public-API freeze
                            no longer needs them
  # permanent compat shims, no real logic, never removed:
  verify/, analyze/, framework/ (-> integration/ handled separately)
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

## `forge/topgen` → `forge/contracts` + `forge/generation` (done)

A split, not a 1:1 rename. `forge.topgen.generators` and `forge.topgen.ip`
were both part of the frozen public Python API
(`docs/reference/public-python-api.md`); their shims carry a real
`DeprecationWarning` (via `from warnings import warn as _warn; _warn(...)`
— a bare-name import, not `warnings.warn(...)`, to avoid colliding with
`forge.docsgen.diagnostics_registry`'s AST scanner, which treats *any*
`.warn(...)` attribute call as a diagnostic-code emission site) for at
least one minor release, per this project's compatibility policy, unlike
`verify`/`analyze`'s permanent, silent shims. No plugin imported
`forge.topgen.*` submodules directly (confirmed by grep), so only
`forge/__init__.py`'s 5 top-level re-exports and internal callers needed
fixing — no plugin-facing smoke test was required here, unlike
`verify`/`analyze`. `forge/ir/build.py` and `forge/ir/model.py` (not
moving) had relative imports reaching into the old `topgen/`, corrected
to `forge/contracts`/`forge/generation` — the highest-value correctness
check, since `forge/ir/` is this repo's most heavily-tested subsystem.
Also found and fixed one lazy, function-body-level relative import
(`forge/contracts/config.py`'s `coordinate_key()` did `from .ip.coordinates
import ...`, missed by the initial line-anchored sweep since it wasn't at
module level) and 11 pre-existing bare `from topgen.X import Y` test
imports (same sys.path-quirk bug class found and fixed during the
`analyze` rename).

## Consequences

- New subsystem code goes into the noun that names its capability, never
  into `core/` because "it didn't fit anywhere else."
- CLI command names (`forge verify`, `forge analyze`, `forge framework`)
  are stable independent of the packages backing them; a future package
  rename is not, by itself, a CLI-breaking change.
- `forge/topgen/generators` and `forge/topgen/ip` should be removed
  (not just deprecated) once the next minor version ships, per the
  compatibility policy — track this as a real follow-up, not left to
  linger indefinitely the way a "permanent" shim would.
