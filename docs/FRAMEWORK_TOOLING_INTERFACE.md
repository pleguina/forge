# Framework Tooling Interface

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

- `arc topgen validate`
- `arc topgen gen-top`
- `arc topgen ip-summary`
- `arc topgen match-ports`
- `arc topgen unpack-ips`
- `arc core verify-contract`
- `arc core resources`
- `arc hls gen-tcl`
- `arc hls run`
- `framework/hls/parallel_hls.sh`
- `framework/hls/generate_hls_tcl.py`
- `framework/hls/extract_hls_metrics.py`
- `arc analyze hls-report`
- `arc analyze latency-check`
- `arc analyze runtime-latency`
- `arc analyze plot-results`
- `arc analyze dashboard`

For HLS flows, the preferred public interface is the installed CLI:

- `arc hls gen-tcl`
- `arc hls run`

The standalone script entry points remain supported as compatibility backends for existing automation. They are framework tooling, but they are not the primary first-class interface a new consumer should build around.

## Topology-generation contract owned by Layer 2

For the current public framework surface, Layer 2 standardizes:

- plugin-authored `modules.yml`
- plugin-authored interface contracts under `interfaces/*.interface.yaml`
- plugin-authored topology under `designs/design.yml`
- contract-driven topology groups and role matching
- strict-mode enforcement for Topology A compliance

Generated DUT artifacts include:

- `algo_top.v`
- `build_manifest.json`
- `design_parameters.json`
- `port_map.yaml`
- `probe_map.yaml`
- `tb_bindings.svh`

## Analysis contract owned by Layer 2

Layer 2 standardizes a post-verification analysis surface via `arc analyze`.

Plugin-authored inputs:

- `modules.yml` entries annotated with `latency_hint` or `latency_cycles` per module
- `plugins/<plugin>/verify/plot_config.yml` defining result figures
- a probe CSV in long format (`cycle,signal,value`) produced by the plugin

Framework-provided outputs:

- `out/reports/hls_summary.{csv,md,html}` — synthesis metrics for all HLS modules
- `out/reports/latency_check.md` — static per-path latency balance report
- `out/reports/runtime_latency.md` — HLS-predicted vs simulation-observed comparison
- `out/reports/plots/*.png` — result comparison figures
- `out/dashboard/dashboard.html` — self-contained HTML aggregating all of the above

See `docs/ANALYSIS_GUIDE.md` for the full plugin author reference.

## Stability rule

The following are public Layer 2 behavior for the current release line:

- documented command names and documented flags
- documented contract-driven topology model
- strict-mode enforcement semantics as described in the author guide
- documented generated artifact set
- documented default error-handling behavior and `--debug` traceback opt-in
- `arc analyze` sub-commands and their plugin-owned input contracts
- dashboard HTML structure and report file naming conventions

The following are not public Layer 2 API:

- internal Python package structure under `framework/topgen/topgen/`
- private validation helpers and internal matcher implementation details
- repo-local wrappers that are not support-classified

## Acceptance proof

The current Layer 2 acceptance proof is based on:

- `plugins/trigger_demo/` as the supported non-OMTF proof consumer
- `ci/verify_framework_release.sh`
- `ci/fresh_user_check.sh`
- `ci/fresh_user_path.sh`

If those gates fail, the Layer 2 tooling surface must be treated as regressed until fixed or reclassified.