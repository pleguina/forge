# Framework Verification Core

This directory contains the generic verification runtime used by plugin-owned testbenches and checkers.

## Contents

- `include/`: generic interfaces and reusable components such as drivers, monitors, scoreboards, analysis ports, and adapter interfaces
- `src/`: implementations of the generic runtime pieces
- `third_party/`: vendored dependencies required by the verification build.
  **Not project code** — the pugixml bundle under `third_party/pugixml-1.15/docs/samples/`
  contains ≈27 library sample programs that are not part of the verification library.
  They are excluded from VS Code search by `.vscode/settings.json`.
  Never edit files under `third_party/`; raise a dependency version issue instead.

## Ownership boundary

This tree is intentionally generic. Plugin-specific transactions, XML models, stimulus generation, expected-value generation, and semantic checkers live under each plugin's `verify/` tree.

## Current verification contract

For the current v1.0 framework verification surface, datasets are XML-backed.
This is a framework-standard contract, not an accidental OMTF-only behavior.

That means the framework currently standardizes:

- `dataset.xml` in flow files as the dataset source
- `--xml-input` as the runtime override for the dataset source
- `--event-id`, `--event-list`, and `--all-events` as framework-standard run selectors for XML-backed flows

Plugins remain responsible for translating those XML-backed datasets into plugin-specific stimulus and semantic checks, but the fact that the dataset is XML-backed is currently part of the framework contract.

## How it is used

The OMTF plugin links its own verification layer on top of this runtime for CSIM, COSIM, and XSIM-backed checks.

The supported non-OMTF proof consumer is:

- `plugins/trigger_demo/verify/` as the maintained consumer proving framework-owned csim/xsim/full-chip paths alongside contract-driven topology generation

## CLI error handling

The public `fw_verify` CLI is intended to fail with concise, actionable messages by default.
Normal user errors such as a missing `design.verification.yml`, a malformed `verify.flow.yml`, or an unsupported contract declaration should not print a raw Python traceback.

Instead, the CLI reports:

- an error scope such as `ERROR (design)` or `ERROR (flow)`
- the concrete failure message
- a suggested next action

Example:

```text
ERROR (design): design file not found: /path/to/design.verification.yml
  → Check the design.verification.yml path and re-run fw_verify generate.
```

Use `--debug` when you want the full traceback for an unexpected internal failure:

```bash
python3 -m fw_verify --debug generate plugins/<plugin>/verify/design.verification.yml
```

Framework-internal user-facing failures are increasingly standardized through typed exceptions such as `DesignContractError`, `FlowConfigError`, and related `FwVerifyError` subclasses. The contract for users is simple: default output should be concise and guided, while `--debug` is the opt-in path for Python traceback details.
