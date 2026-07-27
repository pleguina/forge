# Changelog

Format loosely follows [Keep a Changelog](https://keepachangelog.com/).
This file starts from the `rename/forge` branch — history before that point
is available via `git log` but isn't backfilled here. See `CONTRIBUTING.md`
for the versioning policy.

## [Unreleased]

### Added
- `ci/stale_reference_check.sh` (`forge:stale-reference-check` in CI): a
  standing guard against the exact class of bug this branch kept
  rediscovering by hand — a CLI hint, docstring, or generated-artifact
  default quietly referencing `fw_verify`, bare `topgen`, or
  `framework/verify/python` after they stopped existing.
- Real test coverage for three previously-0%-covered, actually-used
  modules: `forge/core/stale_detection.py`, `forge/verify/stimulus_contract.py`,
  `forge/verify/manifest_compile.py`. Raised the coverage floor from 22%
  to 26% to match (measured: 27.09%).
- (Confirmed dead code, not covered: `forge/topgen/validators.py` and
  `forge/hls/parse_hls_logs.py` are never imported anywhere in the
  codebase. Left alone rather than either testing unreachable code or
  deleting it outside the scope of this pass — worth a follow-up.)

### Changed
- Closed the open PyPI-vs-git-install question from the `2.0.0` release
  notes: confirmed `forge` is already taken on public PyPI by an unrelated
  package, so git install (already the default in `ci/plugin-consumer.yml`)
  is the permanent distribution story, not a placeholder. Documented in
  `CONTRIBUTING.md`.
- Reduced the `mypy` baseline from 115 to 100 errors: safe mechanical
  fixes (implicit-`Optional` defaults, a stray `any`/`Any` typo, missing
  container annotations) plus two genuinely real latent bugs found along
  the way — see Fixed.

### Fixed
- `forge/verify/rtl_introspection.py`'s `write_port_signature()` was
  annotated `-> None` while its docstring documented (and its
  implementation actually did) return the computed hash string, masked
  with a `# type: ignore[return-value]` rather than fixed. Its only
  caller, `extract_and_write()`, was already relying on the real return
  value despite the type saying it couldn't exist. Corrected the
  annotation and removed the now-unneeded ignore.
- `forge/core/cli/__init__.py`'s unused `main()` wrapper (the real entry
  point is `forge.core.cli.main:main`, used by neither this wrapper nor
  imported anywhere) claimed `-> int` while delegating to a function that
  always returns `None`. Corrected to `-> None`.
- Several dead-command reference bugs the same class as this branch's
  earlier `fw_verify`/`topgen` fixes, found by the new
  `stale_reference_check.sh`: stale `topgen ...` hints (missing the
  `forge` prefix) in `forge/core/stale_detection.py`,
  `forge/core/utils/port_signature.py` (including the default
  `"generated_by"` provenance string written into every generated
  `port_signature.json`), `forge/topgen/generators/sv_testbench_generator.py`,
  and `forge/verify/preflight.py`.

## [2.0.0] - 2026-07-27

### Added
- `MIGRATION.md`, `CONTRIBUTING.md`, `SECURITY.md`, root `LICENSE`,
  `CODEOWNERS`, this changelog.
- `forge/tests/test_hdl_parser.py` — real coverage for the HDL parameter
  evaluator (previously 0%).
- A `--cov-fail-under` coverage floor (22%, matching the measured baseline)
  so overall test coverage can't silently regress.
- `mypy` now actually runs in CI (`forge:type-check`), report-only —
  the codebase has a pre-existing baseline of ~115 type errors (mostly in
  `forge/topgen/generators/structural_verilog.py` and
  `forge/verify/__main__.py`) that hasn't been paid down yet. The job makes
  that baseline visible without blocking merges on unscoped work.
- `plugins/passthrough_demo/` — a second, minimal, deliberately generic
  reference plugin (one RTL module, no HLS, no CMS-flavored naming
  anywhere) alongside `plugins/trigger_demo/`. Verified end to end
  including a real Vivado xsim run, not just static checks.

### Changed
- **Breaking:** renamed the framework from ARC to FORGE — CLI command
  (`arc` → `forge`), Python package (`arc.*` → `forge.*`), top-level and
  per-plugin capsule directories (`arc/` → `forge/`), CMake project name,
  and all CI anchors/env vars. No compatibility shim; see `MIGRATION.md`.
- `forge.framework` (detector I/O resolution, ABI import, payload
  generation) and `forge.topgen` are now algorithm-agnostic: detector role
  matching, ABI provider validation, algo-port naming, and accelerator
  timing constants are config-driven with documented defaults instead of
  hardcoded CMS/OMTF assumptions. Verified byte-identical generated output
  for the existing trigger_demo/OMTF configuration.
- Rewrote the CI/CD pipeline: every job was silently broken (pre-dating
  even the rename) due to stale paths and commands from before the
  `framework/`→`arc/` flatten and CLI unification. Replaced the dead
  cmake install-tree release-gate proof with a job that runs
  `run_trigger_demo.sh` directly; rewrote `ci/plugin-consumer.yml`
  (the template downstream plugin repos include) to install `forge`
  correctly; consolidated two overlapping fresh-user-check scripts into
  one; added `ci/agnosticism_check.sh` as a standing regression guard.
- Extended `forge:lint` CI coverage to `forge/framework` and
  `forge/analyze`, which were previously unchecked.

### Fixed
- `forge verify` internals (`bootstrap.py`, `conftest.py`, backend
  registry, several test files) imported a top-level `fw_verify` package
  and referenced a `framework/verify/python` directory that hasn't existed
  since the pre-`arc` layout — only "worked" locally due to a stale,
  non-editable `fw_verify` package left in site-packages from an old
  build. Fails cleanly now instead of silently depending on environment
  pollution.
- `forge verify init-plugin` scaffolded new plugins into the wrong
  (pre-flatten) `<plugin>/verify/` layout and baked the same broken
  `fw_verify` sys.path hack into every generated plugin's `bootstrap.py`
  and `gen_stimulus.py`.
- `gen_stimulus.py` (trigger_demo) computed its repo-root path one
  directory level short after the plugin capsule gained its `forge/`
  layer, silently resolving to a doubled `plugins/plugins/...` path.
- `plugins/trigger_demo/forge/verify/tools/trigger_demo_verify_env.sh` had
  the same one-level-short path bug, resolving `TRIGGER_DEMO_CONSUMER_ROOT`
  to `plugins/` instead of the repo root. The four per-flow `run.sh`
  convenience scripts it's sourced from also invoked the dead
  `python3 -m fw_verify run` and had their own `source ../../tools/...`
  path miscalculation (should have been `../tools/...`) and were missing
  `--plugin`/`--consumer-root`. All four committed `verify.flow.yml` xsim
  flow files also had the pre-`forge/`-capsule dataset XML path baked in.
  Found and fixed by actually running the scripts, not just reading them —
  confirmed working end to end afterward with a real `run_trigger_demo.sh`
  execution (HLS csim + synth for all 4 modules, all 9 verification flows,
  including real Vivado xsim simulation) — all 9 flows passed.
- `forge/verify/__main__.py`'s `_doctor_emit()` type-annotated its
  `report` parameter as `"DiagnosticReport"` without the name being
  resolvable anywhere in the module (mypy: `name-defined`) — added a
  `TYPE_CHECKING`-guarded import.
- Root `CMakeLists.txt` unconditionally `add_subdirectory(verify)`'d a
  directory with no `CMakeLists.txt` of its own — `cmake -S . -B build`
  failed outright. Now guarded on the subdirectory actually existing.
- `forge/pyproject.toml` was missing the `parser` extra
  (`pip install -e forge[parser]`) that several error messages already
  told users to install.
- Replaced `eval()` in `forge/core/utils/hdl_parser.py`'s HDL
  parameter-expression evaluation with an AST-based safe arithmetic
  evaluator.
- Fixed a real `F811` duplicate import and an `E741` ambiguous variable
  name, both surfaced once lint coverage was extended to the packages
  that contained them.
