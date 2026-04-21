# ARC

Algorithm Runtime & Contract framework.

ARC is a plugin-agnostic hardware framework for contract-driven top generation,
optional HLS orchestration, and reusable verification runtime flows. It is built
to stay stable as plugin implementations evolve independently.

Target repository:

- https://gitlab.cern.ch/pleguina/arc-framework

## Scope

The framework owns generic infrastructure for:

- contract-driven DUT generation
- optional HLS orchestration helpers
- generic verification runtime, flow generation, and simulation launch
- verification/runtime packaging

The framework does not own plugin semantics. A plugin must provide its own module contracts, topology, datasets, stimulus generation, and optional semantic checking under `plugins/<plugin>/`.

## Design rule

Nothing in this tree should encode OMTF-specific types, paths, naming, or verification semantics.

Plugin-specific data is passed explicitly from the consuming plugin tree, for example `plugins/<plugin>/`.

## Documentation

Framework documentation index is embedded in the repository `README.md`.

## Subdirectories

- `topgen/`: generates DUT tops and machine-readable contract artifacts from design YAML and IP metadata
- `hls/`: generic HLS orchestration helpers that consume a plugin-provided HLS catalog
- `verify/`: generic C++ verification runtime and Python CLI/runtime package
- `proof/`: internal proof material used by framework release checks

## Supported proof consumer

The maintained non-OMTF proof consumer for the current public framework path is:

- `plugins/trigger_demo/`

It is the supported in-repo demonstration that the public framework path works for a non-OMTF consumer across topology generation, framework-owned verification flows, and full-chip integration proof.

## Release gate

The framework-only acceptance gate for a standalone release candidate is:

- `ci/verify_framework_release.sh`

That gate is intended to pass without requiring `plugins/omtf/` as a mandatory dependency.

## Typical workflow

1. a plugin provides `modules.yml`, interface contracts, topology YAML, and verification contract files
2. `framework/hls/` optionally builds or exports HLS IPs
3. `framework/topgen/` generates the DUT and contract artifacts
4. `framework/verify/` generates verification flows and launches the generic simulation lifecycle
