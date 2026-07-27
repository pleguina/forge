# Changelog

Format loosely follows [Keep a Changelog](https://keepachangelog.com/).
This file starts from the `rename/forge` branch — history before that point
is available via `git log` but isn't backfilled here.

## [Unreleased]

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

### Added
- `MIGRATION.md`, `CONTRIBUTING.md`, `SECURITY.md`, root `LICENSE`,
  this changelog.
