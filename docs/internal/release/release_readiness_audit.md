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
(`docs/reference/public-python-api.md` + `public_api_inventory.json`), and
found/fixed two real bugs described under P0/P1 below. All of this is
committed to the working tree as of this audit; see `git status` for the
exact diff if this file is read before those changes are committed.

## P0 — release blockers

### P0-1: the built wheel does not install a working `forge` CLI (FIXED this session)

`forge/pyproject.toml`'s `[tool.setuptools] packages` list was hand-
maintained and had drifted: `forge.core.cli.groups`, `forge.framework`,
`forge.analyze.throughput_static`, `forge.analyze.throughput_runtime`, and
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

### P0-2: `<org>` placeholder — no decided public host

`forge/pyproject.toml [project.urls]`, `README.md`, and `CONTRIBUTING.md`
all point at `https://github.com/<org>/forge` — a literal placeholder, not
a real destination. `docs/index.md`/`README.md` are otherwise honest about
this (framed as "pending," not silently wrong), but per plan §15
("public hosting targets are ready") this blocks stable publication
specifically, not RC-candidate work. **Not fixed this session** — this is
a real decision (which org, whether the CERN GitLab origin migrates or a
new host is chosen) that only you can make; flagged here rather than
guessed at.

## P1 — must fix before stable

### P1-1: `ci/stale_reference_check.sh` currently fails on this branch's HEAD

This is a real, blocking CI job (`.gitlab-ci.yml` `forge:stale-reference-
check`, stage `validate`, no `allow_failure`) that fails right now,
independent of this session's edits (reproduced on a clean `git stash` of
the pre-session tree too). Confirmed causes, all false positives in
**tracked** files (would reproduce in real CI, not just locally):

- `docs/development/migration.md` mirrors root `MIGRATION.md` (documented,
  intentional — see `docs/development/cli_exit_codes.md`'s own "Pointer
  pages" pattern) but the script's exclusion list only names the root
  `MIGRATION.md`/`CHANGELOG.md`, not their `docs/development/` mirrors —
  so the mirror's legitimate historical `fw_verify` mentions trip the
  guard.
- `forge/topgen/migrate.py` and its tests (`test_migrate.py`,
  `test_topgen_migrate_cli.py`) exist specifically to detect and rewrite
  the legacy `framework/verify/python` sys.path pattern — they necessarily
  contain the literal string they're built to find, the same class of
  false positive the script's own header already excuses for
  `MIGRATION.md`/`CHANGELOG.md`, just not extended to these files.
  `docs/development/migration.md` trips this pattern too, same root cause
  as above.
- The "bare `topgen <subcommand>`" regex flags **prose** mentions in
  backticks (README.md, several docstrings) that describe the command,
  not actual bare invocations — the negative-lookbehind only excludes a
  literal preceding `` forge `` / `` forge --debug ``, not backtick-quoted
  documentation context.

**Not fixed this session** (out of the approved scope for this pass —
this is a pre-existing bug in an unrelated CI script, not part of the
AI-plan-doc-removal/hygiene work); recommend narrowing the script's
exclusion list and regex in a dedicated follow-up before relying on this
gate for the release candidate.

### P1-2: broad "release-plan §X.Y / Phase N / slice N.N" citation convention across ~186 files in `forge/`

Plan §7.2 requires production comments to "explain semantics, not
Phase/Slice history." A narrow slice of this (the ~30 files with literal
`docs/development/release-readiness.md` / `docs/internal/phase10/*`
path citations, which would have become dangling links once those files
were removed) was fixed this session. The broader convention —
`release-plan §4.2`, `Phase 6`, `slice 10.7B`-style citations with no
literal path, referencing `docs/plan/FORGE_release_plan.md` (gitignored,
never shipped) — is used throughout **~186 files** across `forge/` and
`plugins/` as this codebase's standard rationale-comment style, confirmed
via `git grep -lE 'release-plan|migration step|Phase [0-9]+|Slice
[0-9]+\.' -- forge plugins`. `ci/vision_pipeline_demo_reference_check.sh`
already documents this exact tradeoff in its own comments: it enforces the
citation-free style for `vision_pipeline_demo` and its tutorial pages, and
explicitly excludes `forge/` core as "a separate, pre-existing, much
larger convention... explicitly out of scope for this productization
pass." This audit concurs with that prior scoping decision rather than
reversing it under time pressure: a blind mechanical rewrite of ~186 files
in one pass risks subtle regressions in working, tested code comments for
a cosmetic (if real) issue, and isn't itself a release blocker — nothing
in these comments is factually wrong (unlike the one stale docstring found
and fixed, see below), they just cite an internal, non-shipped document.
**Recommendation**: a dedicated follow-up session, scoped and reviewed
like any other refactor, not a rider on this freeze pass.

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

## Not attempted this session (explicitly out of scope — see conversation)

Per the agreed session boundary: no release branch/tag was created, no
package was published anywhere, and the `<org>` public-host placeholder
was not resolved. External fresh-user validation was performed via a real
clean venv + a real independent CI-equivalent script in this sandbox, but
not on a genuinely separate machine/account — that remains a real gap
against plan §14 for the actual go/no-go decision.
