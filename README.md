# FORGE

Framework for Orchestrated RTL Generation & Evaluation.

FORGE is a plugin-agnostic framework for contract-driven DUT generation,
optional HLS orchestration, and reusable verification runtime flows.

Repository target:

- https://gitlab.cern.ch/pleguina/arc-framework

## Versioning

Two independent version numbers appear in this repo — they track different
things and are not expected to match:

- **Package version** (`forge/pyproject.toml`, currently `2.0.0`) — the
  installed `forge` Python package/CLI release. Follows semver; see
  `CONTRIBUTING.md` for what counts as a MAJOR/MINOR/PATCH change here.
  `2.0.0` is the ARC → FORGE rename — a breaking, shim-free change to the
  CLI/package/directory surface. See `MIGRATION.md`.
- **Verification contract version** (currently `v1.0`, referenced throughout
  `docs/`) — the version of the Layer 1 verification model itself
  (`design.verification.yml` schema, generated artifact set, CLI contract
  behavior). It changes far less often than the package version, and did
  not change in `2.0.0`.

## Documentation Index

This index lists the minimum user-facing documentation required to adopt and use FORGE.

### Introduction

- `forge/README.md` - CLI command reference and generated output summary

### Quickstart

- `docs/MINIMAL_CONSUMER_QUICKSTART.md` - shortest supported adoption path

### Authoring and Contracts

- `docs/PLUGIN_AUTHOR_GUIDE.md` - plugin contract authoring and topology wiring
- `docs/IP_INTERFACE_POLICY.md` - interface contract policy and constraints
- `normalized_signal_families.yaml` - canonical signal family vocabulary

### Runtime and Verification

- `docs/FRAMEWORK_CORE_INTERFACE.md` - Layer 1 runtime and verification interface
- `docs/VERIFY_PLUGIN_AUTHOR_GUIDE.md` - verification integration for plugin authors
- `docs/VERIFY_FRAMEWORK_SETUP.md` - framework-side setup and runnable proof path

### Tooling

- `docs/FRAMEWORK_TOOLING_INTERFACE.md` - Layer 2 topology/HLS tooling interface
- `forge/README.md` - forge CLI command usage and outputs

### Support Boundaries

- `docs/SUPPORT_CLASSIFICATION.md` - supported versus internal surfaces

### Validation Gate

- `run_trigger_demo.sh` - end-to-end release gate (`ci/framework-release.yml`)
- `ci/agnosticism_check.sh` - guard against hardcoded algorithm assumptions

### Project

- `CHANGELOG.md` - notable changes by release
- `CONTRIBUTING.md` - development setup and contribution workflow
- `MIGRATION.md` - breaking-change notes (start here if upgrading from ARC)
- `SECURITY.md` - how to report a security issue
- `LICENSE` - MIT
