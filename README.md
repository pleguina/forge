# ARC

Algorithm Runtime & Contract framework.

ARC is a plugin-agnostic framework for contract-driven DUT generation,
optional HLS orchestration, and reusable verification runtime flows.

Repository target:

- https://gitlab.cern.ch/pleguina/arc-framework

## Documentation Index

This index lists the minimum user-facing documentation required to adopt and use ARC.

### Introduction

- `framework/README.md` - framework scope, boundaries, and workflow summary

### Quickstart

- `docs/MINIMAL_CONSUMER_QUICKSTART.md` - shortest supported adoption path

### Authoring and Contracts

- `docs/PLUGIN_AUTHOR_GUIDE.md` - plugin contract authoring and topology wiring
- `docs/IP_INTERFACE_POLICY.md` - interface contract policy and constraints
- `framework/normalized_signal_families.yaml` - canonical signal family vocabulary

### Runtime and Verification

- `docs/FRAMEWORK_CORE_INTERFACE.md` - Layer 1 runtime and verification interface
- `docs/VERIFY_PLUGIN_AUTHOR_GUIDE.md` - verification integration for plugin authors
- `docs/VERIFY_FRAMEWORK_SETUP.md` - framework-side setup and runnable proof path
- `framework/verify/README.md` - verification runtime package details

### Tooling

- `docs/FRAMEWORK_TOOLING_INTERFACE.md` - Layer 2 topology/HLS tooling interface
- `framework/topgen/README.md` - topgen command usage and outputs

### Support Boundaries

- `docs/SUPPORT_CLASSIFICATION.md` - supported versus internal surfaces

### Validation Gate

- `ci/verify_framework_release.sh` - standalone framework release gate
