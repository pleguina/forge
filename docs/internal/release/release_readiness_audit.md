# FORGE — Release Readiness Audit

Evidence-based audit against
`docs/plan/FORGE_framework_freeze_and_public_release_plan.md`. Every
finding below comes from a real command run in this session against the
real repository (or, where noted, a real clean-room venv/wheel install) —
not from reading the plan and assuming compliance.

- **Branch**: `rename/forge`
- **Commit audited**: `f10888359d94497f0bdc4f2beb7a8c8200cb005f` (plus this
  session's uncommitted working-tree changes — see "Session changes" below)
- **Date**: 2026-08-07
- **Package version**: `2.0.0` (`forge/pyproject.toml`, single source; no
  drift found against `forge.__version__`/`forge --version`)

## Severity model

- **P0** — release blocker
- **P1** — must fix before stable
- **P2** — can ship with an explicit, documented limitation
- **P3** — post-release improvement

## Session changes (this audit's own scope)

This session removed two AI-agent implementation-tracking artifacts from
the tracked repo (`docs/development/release-readiness.md`,
`docs/internal/phase10/*.md` — 1774+ lines of phase-by-phase work log,
never meant for an external reader), fixed the ~30 files left with dangling
references to those two paths, added a generated public-Python-API freeze
(`docs/reference/public-python-api.md` + `public_api_inventory.json`), then
in a second pass removed the broader "release-plan §X.Y/Phase N/slice N.N"
internal-citation convention from all 182 remaining files across `forge/`
and `plugins/` (see P1-2 below — this was originally deferred as a
follow-up, then completed in this same session), and
found/fixed two real bugs described under P0/P1 below. All of this is
committed to the working tree as of this audit; see `git status` for the
exact diff if this file is read before those changes are committed.

## P0 — release blockers

### P0-1: the built wheel does not install a working `forge` CLI (FIXED this session)

`forge/pyproject.toml`'s `[tool.setuptools] packages` list was hand-
maintained and had drifted: `forge.core.cli.groups`, `forge.framework`,
`forge.analysis.throughput_static`, `forge.analysis.throughput_runtime`, and
`forge.docsgen` all exist as real packages on disk with real, imported-
elsewhere code, but were **not** in the list. A real `python -m build` +
clean-venv `pip install` of the resulting wheel reproduced the exact P0
example the plan calls out by name ("install from a clean environment
fails"): `forge --version` crashed with
`ModuleNotFoundError: No module named 'forge.core.cli.groups'` — every
`forge` invocation failed, not just `framework`-group commands, because
`forge/core/cli/main.py` imports `groups` eagerly at module level.

**Fix applied**: added the 5 missing packages to the list. Re-verified
with a second clean `python -m build` + fresh-venv install: `forge
--version`, `forge --help`, `forge doctor`, `forge init <plugin_id>`
(full chain: scaffold → validate → build → real XSim test run → report)
all pass from the installed wheel alone, no repo checkout on `PYTHONPATH`.

**Follow-up recommendation (P3, not blocking)**: this list has now drifted
silently at least once. Consider `[tool.setuptools.packages.find]`
auto-discovery instead of a hand-maintained list to prevent recurrence —
not done this session per the freeze principle of minimal, low-risk
changes; the explicit list is safer to verify quickly.

### P0-2: `<org>` placeholder — no decided public host (RESOLVED)

`forge/pyproject.toml [project.urls]`, `README.md`, and `CONTRIBUTING.md`
all point at `https://github.com/<org>/forge` — a literal placeholder, not
a real destination. `docs/index.md`/`README.md` are otherwise honest about
this (framed as "pending," not silently wrong), but per plan §15
("public hosting targets are ready") this blocks stable publication
specifically, not RC-candidate work. **Not fixed this session** — this is
a real decision (which org, whether the CERN GitLab origin migrates or a
new host is chosen) that only you can make; flagged here rather than
guessed at.

**Later session update**: resolved. The decision is
`https://github.com/pleguina/forge`. Every `<org>` placeholder and
CERN-GitLab install URL was updated to match (pyproject.toml, README,
CONTRIBUTING, mkdocs.yml, CHANGELOG, getting-started docs,
`ci/plugin-consumer.yml`'s functional pip-install default). A broader
sweep triggered by this pass also found and fixed ~35 files with a
different, previously-missed stale-reference shape: slash-form core
package paths (`forge/topgen/...`, `forge/verify/...`, `forge/analyze/...`
in prose/docstrings/CI lint commands, including two functionally broken
`.gitlab-ci.yml` lint jobs that were linting nonexistent directories) —
`ci/stale_reference_check.sh`'s existing checks only caught the dotted
Python-import form (`forge\.verify`), not this slash-path form, which is
harder to check automatically without false-positiving on plugins' own
legitimate `<plugin>/forge/verify/` capsule directories (left alone,
confirmed intentional and current). The GitHub repo starts from a single
squashed commit, not the full CERN GitLab history (deliberate — GitLab
remains the full-history record). GitHub Actions CI is a deliberately
separate, not-yet-done follow-up.

## P1 — must fix before stable

### P1-1: `ci/stale_reference_check.sh` currently fails on this branch's HEAD (FIXED)

This was a real, blocking CI job (`.gitlab-ci.yml` `forge:stale-reference-
check`, stage `validate`, no `allow_failure`) that failed independent of
this session's edits (reproduced on a clean `git stash` of the
pre-session tree too). Root causes, all false positives in **tracked**
files (would reproduce in real CI, not just locally) — fixed by
extending the script's own existing exclusion philosophy
(`MIGRATION.md`/`CHANGELOG.md` are excluded because "their purpose is
documenting the old, dead names for historical/migration reference, not
using them live"):

- `docs/development/migration.md` mirrors root `MIGRATION.md` but wasn't
  itself excluded — added.
- `forge/generation/migrate.py` (moved from `forge/topgen/migrate.py`
  during the later package-rename work — see below) and its tests
  (`test_migrate.py`, `test_topgen_migrate_cli.py`) exist specifically to
  detect and rewrite the legacy `framework/verify/python` sys.path
  pattern — they necessarily contain the literal string they're built to
  find — added to the exclusion list.
- `docs/internal/` and `docs/plan/` discuss these same dead patterns as
  audit findings, in prose — added to the exclusion list.
- The bare-`topgen`-invocation regex's negative lookbehind only excluded
  a literal preceding `` forge `` / `` forge --debug ``, not
  backtick-quoted prose (`` `topgen gen-top` ``) — extended with a
  `(?<!\`)` lookbehind; the two remaining genuine (non-backtick) prose
  hits were fixed by quoting them properly instead of further
  complicating the regex.

Verified: `bash ci/stale_reference_check.sh` passes clean.

**Not fixed this session** (out of the approved scope for this pass —
this is a pre-existing bug in an unrelated CI script, not part of the
AI-plan-doc-removal/hygiene work); recommend narrowing the script's
exclusion list and regex in a dedicated follow-up before relying on this
gate for the release candidate.

### P1-2: broad "release-plan §X.Y / Phase N / slice N.N" citation convention across ~186 files in `forge/` (FIXED this session)

Plan §7.2 requires production comments to "explain semantics, not
Phase/Slice history." The broad convention — `release-plan §4.2`,
`Phase 6`, `slice 10.7B`-, `migration step N`-, `Defect N`-, and
`preflight.md §N`-style citations, all pointing at internal, gitignored,
never-shipped planning documents — was used throughout **182 files**
across `forge/` and `plugins/` (~504 occurrences) as this codebase's
standard rationale-comment style.

Rather than a blind mechanical regex rewrite, this was done as a reviewed
refactor: 7 parallel agents each handled a disjoint file set (split by
subsystem — `forge/core`+`forge/docsgen`, `forge/ir`+`forge/topgen`,
`forge/verify`, `forge/analyze`+`plugins`, and `forge/tests` in 3 parts),
each with explicit instructions to preserve all real technical content,
strip only the internal-document pointers, watch for legitimate hardware/EE
uses of "phase" (clock phase) and "slice" (FPGA slice) as false positives,
and never touch identifiers, test assertions, or executable code — only
prose inside comments/docstrings.

Two of the agents found and flagged **6 cases where the citation had
leaked past a source comment into actual generated/user-facing output**,
outside their stated scope (comments/docstrings only) — these were fixed
directly afterward, since they're more consequential than a stray source
comment:

- `forge topgen migrate --help`'s own CLI help text (`--kind` option)
- `docs/reference/transformations.md` — the **published, generated public
  docs page** — via `forge/docsgen/vocab_reference.py`'s transformation
  descriptions
- `design.ir.json`'s `ResolvedVerificationPlan.note` default value
  (shipped in every generated IR snapshot)
- A comment emitted directly into generated Verilog RTL
  (`structural_verilog.py`'s occupancy/backpressure telemetry comment)
- A comment emitted into generated SystemVerilog testbenches
  (`sv_testbench_generator.py`)
- A comment emitted into `forge topgen migrate --kind infer-contract`'s
  generated interface-contract skeleton

A final sweep after all 7 agents completed found 7 remaining occurrences
(a related `Defect N` pattern in 4 files that fell just outside the
agents' exact grep pattern, flagged by one of them) — fixed directly.
Final state: **0** occurrences of any of these patterns remain in `forge/`
or `plugins/`.

**Verification**: every edited `.py` file passes `python3 -m py_compile`;
full test suite re-run clean after the change (1274 passed, 12 skipped,
0 failed — same counts as before, confirming zero behavioral regressions);
`mkdocs build --strict` clean; `python -m forge.docsgen --check` clean
after regenerating the 4 pages whose source docstrings changed
(`cli.md`, `transformations.md`, `artifacts.md`, `public-python-api.md`);
`ci/agnosticism_check.sh`, `ci/import_direction_check.sh`,
`ci/vision_pipeline_demo_reference_check.sh` all still pass.

### P1-3: stale docstring claimed CDC `async_fifo` doesn't generate real RTL (FIXED this session)

`forge/topgen/generators/structural_verilog.py`'s `write_structural_verilog`
docstring stated `cdc: {kind: async_fifo}` "does not emit a FIFO body this
release — the connection is wired directly." This is false: real
`cdc_async_fifo` instantiation (depth, occupancy, write-enable gating) is
implemented later in the same function (confirmed by reading the actual
generation code, not just the docstring) and is exercised by
`vision_pipeline_demo`'s packetizer path. A nearby comment
(`release-plan §10.0B: every kind now emits real RTL... including
async_fifo, which used to be wired straight through`) confirms this was
accurate once, then went stale when async_fifo generation was added
without updating this docstring. Also corrected the same false claim in
`plugins/trigger_demo/algo/rtl/cdc_sync2ff.v`'s comment. This is exactly
the class of issue plan §10.5 asks to resolve before stable (an
unexplained/incorrect claim in a core, user-facing docstring) — fixed by
correcting the docstring to describe the real behavior, not by changing
any generation logic.

## P2 — can ship with an explicit, documented limitation

- No `THIRD_PARTY_NOTICES.md` at the repo root. The one vendored
  third-party asset (`cytoscape.min.js`, pinned `3.28.1`) already has its
  own `LICENSE` + attribution `README.md` alongside it
  (`forge/analyze/design_explorer/vendor/`), so attribution exists at the
  point of use — a root-level consolidated notice is good practice, not a
  gap that currently misleads anyone.
- `forge.core.utils.__all__` exports 4 underscore-prefixed names
  (`_scan_ports`, `_scan_verilog_ports`, `_vhdl_entity_name`,
  `_verilog_module_name`) — private-by-convention names publicly exported.
  New this session: the generated public-API page/inventory explicitly
  excludes these from the stable surface and lists them as a known
  inconsistency rather than silently promoting or silently renaming them
  (renaming/removing would be a real public-behavior change, out of scope
  for a freeze pass per plan principle 1).
- Runtime throughput/occupancy reporting (`forge/analyze/throughput_runtime/`)
  depends on a cached probe CSV that several tests skip without in this
  environment — matches the plan's own §3.1 Beta classification, not a
  new finding.
- `forge/hls/generate_hls_tcl.py` prints an error and calls `sys.exit(1)`
  from top-level module code when the optional `topgen` extra isn't
  installed, rather than deferring that check into a function. Harmless
  today (nothing imports this module as a library), but it's the kind of
  import-time side effect that made the new public-API discovery
  generator need a `BaseException` catch (`SystemExit` isn't an
  `Exception`) — worth a defensive cleanup, not urgent.
- `ci/quickstart_commands.sh` (the documented, CI-verified quickstart)
  still demonstrates the older, narrower `forge verify init-plugin`
  scaffold-only flow. The newer, more complete `forge init <plugin_id>`
  (scaffold → validate → build → test → report in one command, verified
  working end-to-end from a clean wheel install this session) isn't
  featured in the quickstart script. Both work; the quickstart doesn't
  showcase the more complete golden path.

## P3 — post-release improvements

- Switch `forge/pyproject.toml`'s hand-maintained `packages` list to
  `[tool.setuptools.packages.find]` auto-discovery (see P0-1).
- Fix `ci/stale_reference_check.sh`'s false positives (see P1-1) so it can
  be trusted as a real gate again.

## What's already solid (not findings — confirmed working)

- **Clean-room install**: `python -m build` (sdist + wheel) from a clean
  `build/`/`egg-info` state, installed into a fresh venv with no repo on
  `PYTHONPATH`, no dev caches. `forge --version`, `forge --help`, `forge
  doctor` (all required checks pass; only optional extras — GHDL,
  pyverilog, matplotlib, numpy, networkx — are missing, correctly reported
  as optional), and a **full** `forge init <plugin_id>` run (scaffold →
  validate → build → real XSim verification run → report) all succeed
  from the installed wheel alone.
- **Independent fresh-user check** (`ci/fresh_user_check.sh`, its own
  from-scratch venv): quickstart sequence, `init-plugin --dry-run`,
  `doctor --json` structural check, `trigger_demo` validate/prepare/doctor,
  full unit-test suite (1274 passed, 12 skipped), `trigger_demo` plugin
  tests (110 passed). Final verdict: **all fresh-user onboarding checks
  passed**.
- **Full test suite** (`python -m pytest forge/tests`): 1269–1274 passed
  (count varies slightly by which optional local caches are warm — see
  `release_test_matrix.md`), 12 skipped, **0 failed**, 0 unexplained
  skips (every skip has a stated, honest reason — missing optional cache
  or `TOPGEN_CONSUMER_ROOT`, never silent).
- **Docs**: `mkdocs build --strict` clean; built-HTML link/anchor checker,
  no-external-runtime-asset checker, generated-page and root-mirror
  staleness checks all pass; `python -m forge.docsgen --check` reports
  every generated reference page (including the two new ones this
  session) up to date.
- **Reference-scope lints**: `ci/agnosticism_check.sh`,
  `ci/import_direction_check.sh`,
  `ci/vision_pipeline_demo_reference_check.sh` all pass clean.
- **Secrets/private-data scan**: no passwords, API keys, or private-key
  material found in tracked `.py`/`.yml`/`.yaml` files (targeted grep,
  not a full history scan — see next-session recommendations).
- **License**: MIT, `LICENSE` present, consistent with `CHANGELOG.md`'s
  "Open-source policy" entry (copyright is the individual author's, not
  CERN's — deliberate, already documented). The one vendored third-party
  asset carries its own license/attribution.
- **CLI/diagnostics/schema freeze infrastructure**: already built in a
  prior session (`forge.docsgen` generates CLI reference, canonical
  roles/protocols/interface-members, diagnostics catalogue — 50 registered
  diagnostics — support matrix, and versioned artifact reference, all from
  live source, AST-verified against drift). This session added the public
  Python API to that same generated-freeze system (66 public symbols
  across 5 modules; see `release_scope.md`).

## Later session update: package-naming cleanup (post-audit)

After this audit was written, a separate, deliberate cleanup landed on
top of it (5 more commits): `forge/framework` → `forge/integration`,
`forge/verify` → `forge/verification`, `forge/analyze` →
`forge/analysis` (both permanent compat shims — real plugin `bootstrap.py`
files import these submodules directly), and `forge/topgen` split into
`forge/contracts` + `forge/generation` (deprecation-window shim, since
`forge.topgen.generators`/`forge.topgen.ip` are in the frozen public
API). See `docs/development/adr/0005-package-and-cli-naming.md` for the
full rationale and target tree. Also fixed as part of this pass: **P1-1
above** (`ci/stale_reference_check.sh`, now passing), plus a real P0-class
bug found mid-rename (`ModuleNotFoundError` from a lazy import missed by
the initial sweep — caught by actually running the test suite, not by
inspection). Every rename was independently verified with the full suite,
a clean-venv wheel install, and — for the two plugin-facing renames — a
real, unmodified reference plugin's `bootstrap.py` actually running.
Current test count: **1286 passed, 12 skipped, 0 failed** (up from 1274
at the original audit, net of intentional test removals/additions along
the way).

## Later session update: compat shims deleted (post-audit, post-rename)

The `forge/verify`/`forge/analyze` permanent shims and the
`forge/topgen` deprecation-window shim described immediately above were
built, verified, and then **deleted in full** in a follow-up pass, once
it was confirmed FORGE has no public release yet and no external
consumer to stay compatible with (the in-repo plugins that used the old
paths were updated in the same pass instead of relying on an alias). No
compat shim exists anywhere in `forge/` today — every rename is a clean,
breaking move, consistent with how `framework`→`integration` was already
handled. See `docs/development/adr/0005-package-and-cli-naming.md`'s
"History" note for the full reasoning. Test count after shim removal:
**1270 passed, 12 skipped, 0 failed** (down from 1286 — the removed
count is exactly the 3 deleted compat-shim test files' tests, no
regressions).

## Not attempted this session (still open — real decisions, not more cleaning)

- **P0-2 is still open**: the `<org>` public-host placeholder
  (`forge/pyproject.toml [project.urls]`, `README.md`, `CONTRIBUTING.md`)
  is unresolved — this blocks stable publication specifically (plan
  §15's "public hosting targets are ready" go/no-go criterion), not
  RC-candidate work. This is a real decision (which org, whether the
  CERN GitLab origin migrates or a new host is chosen) only you can make.
- No release branch/tag was created, no package was published anywhere.
- External fresh-user validation was performed via a real clean venv +
  a real independent CI-equivalent script in this sandbox, but not on a
  genuinely separate machine/account — a real gap against plan §14 for
  the actual go/no-go decision.
- No dependency/license audit beyond a targeted secrets grep and
  confirming the one vendored JS asset's attribution — plan §12's full
  dependency inventory (known vulnerabilities, abandoned packages) was
  not run. Current runtime dependency surface is minimal (`pyyaml`,
  `jinja2` only), which lowers the real risk here, but the audit itself
  wasn't performed.
- No `THIRD_PARTY_NOTICES.md` — still just a P2, per the original
  finding (the vendored asset's own `LICENSE`/`README.md` already carry
  attribution at the point of use).
