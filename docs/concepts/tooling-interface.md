# Tooling Interface (Layer 2)

This document defines the current Layer 2 public tooling interface for a standalone framework repository.

Layer 2 covers supported tooling above the minimal verification/runtime contract.

## Purpose

Layer 2 exists to support plugin authors who want the framework to help with:

- topology generation
- contract verification
- optional HLS orchestration
- framework resource discovery

These tools are supported, but a consumer should still treat them as tooling surfaces rather than the minimum runtime contract.

## Public Layer 2 surface

The supported Layer 2 tooling surface is:

- `forge topgen validate`
- `forge topgen gen-top`
- `forge topgen ip-summary`
- `forge topgen match-ports`
- `forge topgen unpack-ips`
- `forge core verify-contract`
- `forge core resources`
- `forge hls gen-tcl`
- `forge hls run`
- `forge/hls/generate_hls_tcl.py`
- `forge/hls/extract_hls_metrics.py`
- `forge analyze hls-report`
- `forge analyze latency-check`
- `forge analyze runtime-latency`
- `forge analyze plot-results`
- `forge analyze dashboard`

For HLS flows, the preferred public interface is the installed CLI:

- `forge hls gen-tcl`
- `forge hls run`

`forge/hls/generate_hls_tcl.py` and `forge/hls/extract_hls_metrics.py` are the implementations behind those commands. They are framework tooling, but they are not the primary first-class interface a new consumer should build around.

## Topology-generation contract owned by Layer 2

For the current public framework surface, Layer 2 standardizes:

- plugin-authored `modules.yml`
- plugin-authored interface contracts under `interfaces/*.interface.yaml`
- plugin-authored topology under `designs/design.yml`
- contract-driven topology groups and role matching
- strict-mode enforcement for Topology A compliance

Generated DUT artifacts include:

- `algo_top.v`
- `design.ir.json` — the canonical IR the rest were generated from
- `provenance.json`
- `build_manifest.json`
- `design_parameters.json`
- `port_map.yaml`
- `probe_map.yaml`
- `tb_bindings.svh`

`design.ir.json` is the resolved design: modules, instances, connections
with their matching evidence, clock and reset domains, the top level's
ports and the pin behind each one, and the verification plan resolved
against it. Everything else in that list is generated from it, and the
reports among them record its content hash — see
[Schema versioning](../development/SCHEMA_VERSIONING.md) for its
compatibility and migration policy.

## Analysis contract owned by Layer 2

Layer 2 standardizes a post-verification analysis surface via `forge analyze`.

Plugin-authored inputs:

- `modules.yml` entries annotated with `latency_hint` or `latency_cycles` per module
- `plugins/<plugin>/forge/verify/plot_config.yml` defining result figures
- a probe CSV in long format (`cycle,signal,value`) produced by the plugin

Framework-provided outputs:

- `out/reports/hls_summary.{csv,md,html}` — synthesis metrics for all HLS modules
- `out/reports/latency_check.md` — static per-path latency balance report
- `out/reports/runtime_latency.md` — HLS-predicted vs simulation-observed comparison
- `out/reports/plots/*.png` — result comparison figures
- `out/dashboard/dashboard.html` — self-contained HTML aggregating all of the above

See [How to Analyze Performance](../how-to/analyze-performance.md) for the full plugin author reference.

## Stability rule

The following are public Layer 2 behavior for the current release line:

- documented command names and documented flags
- documented contract-driven topology model
- strict-mode enforcement semantics as described in the author guide
- documented generated artifact set
- documented default error-handling behavior and `--debug` traceback opt-in
- `forge analyze` sub-commands and their plugin-owned input contracts
- dashboard HTML structure and report file naming conventions

The following are not public Layer 2 API:

- internal Python package structure under `forge/contracts/`/`forge/generation/`
- private validation helpers and internal matcher implementation details
- repo-local wrappers that are not support-classified

## Acceptance proof

The current Layer 2 acceptance proof is based on:

- `plugins/trigger_demo/` as the supported non-OMTF proof consumer
- `ci/verify_framework_release.sh`
- `ci/fresh_user_check.sh`
- `ci/fresh_user_path.sh`

If those gates fail, the Layer 2 tooling surface must be treated as regressed until fixed or reclassified.
