# Framework Core Interface

This document defines the current Layer 1 public framework interface for a standalone framework repository.

Layer 1 is the minimum supported contract a plugin consumer can depend on without relying on framework internals.

## Purpose

Layer 1 covers the framework-owned runtime and verification interface surface.

It does not cover optional HLS convenience tooling or implementation details inside the Python packages.

## Public Layer 1 surface

The current public Layer 1 surface is:

- `arc verify` installed CLI sub-commands
- `python -m arc.verify` module entrypoint
- `plugins/<plugin>/arc/verify/include/` and `.../src/` — plugin-owned verification headers and runtime implementation, built via the plugin's own CMake target
- `docs/VERIFY_PLUGIN_AUTHOR_GUIDE.md`
- `docs/MINIMAL_CONSUMER_QUICKSTART.md`

## Verification contract owned by Layer 1

For the current public `v0.0` framework surface, Layer 1 standardizes the following verification model:

- datasets are XML-backed
- plugin-authored verification root contains `design.verification.yml`
- the framework generates per-flow `verify.flow.yml`, `tb_<module>.sv`, and `wave.tcl`
- the plugin generates `stimulus_current.svh`
- plugin bootstrap is an explicit lifecycle requirement

Framework-standard runtime selectors for XML-backed flows are:

- `--xml-input`
- `--event-id`
- `--event-list`
- `--all-events`

## Plugin author responsibilities under Layer 1

A plugin consumer must provide:

- `plugins/<plugin>/verify/design.verification.yml`
- `plugins/<plugin>/verify/tools/bootstrap.py`
- `plugins/<plugin>/verify/tools/gen_stimulus.py`
- XML datasets under `plugins/<plugin>/verify/schemas/data/`
- optional checker logic if self-checking testbenches are not sufficient

The plugin does not need to author framework-generated artifacts such as `verify.flow.yml`, generated testbenches, or `wave.tcl`.

## Stability rule

The following are considered public Layer 1 behavior for the current release line:

- framework-owned verification file layout and generation model
- `arc verify` CLI subcommands and documented flags
- documented plugin bootstrap lifecycle
- documented verification contract fields
- documented default error-handling behavior and `--debug` traceback opt-in

The following are not Layer 1 API and may change without being treated as a public compatibility break:

- internal Python module layout under `arc/verify/`
- internal helper functions and private module names
- repo-local test fixtures and maintainer-only scripts

## Acceptance proof

The current Layer 1 acceptance proof is based on:

- `plugins/trigger_demo/arc/verify/` as the maintained non-OMTF proof consumer
- `tests/external_consumer_proof/`
- `ci/verify_framework_release.sh`

If those surfaces stop passing, Layer 1 must be treated as regressed until fixed or reclassified.