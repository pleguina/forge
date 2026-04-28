# HLS Tooling

This directory contains the generic HLS orchestration helpers.

The preferred public interface is the `topgen` CLI:

- `topgen hls gen-tcl`
- `topgen hls run`

The shell and standalone Python entry points in this directory remain supported as compatibility backends for existing automation and Makefile-based workflows. They are not the preferred entrypoint for a new framework consumer.

## Core scripts

- `parallel_hls.sh`: main batch runner for `csim`, `synth`, `cosim`, and `export`
- `generate_hls_tcl.py`: emits per-module Vitis HLS TCL from a plugin-owned HLS catalog
- `extract_hls_metrics.py`: summarizes HLS reports
- `visualize_hls_pipeline.py`: generates report plots
- `generate_reports.sh`: convenience wrapper for metrics plus plots
- `generate_algorithm_top.sh`: deprecated repo-local wrapper; keep internal only

## Required plugin input

The orchestrator does not assume a built-in plugin. Pass the plugin HLS catalog explicitly with `-p` or `HLS_CONFIG`.

Preferred example:

```bash
topgen hls run \
  --stage synth \
  --modules design \
  --design plugins/<plugin>/designs/design.yml \
  --hls-config plugins/<plugin>/modules.yml
```

Compatibility backend example:

```bash
./framework/hls/parallel_hls.sh \
	-j 4 \
	-c synth \
	-m design \
	-d plugins/<plugin>/designs/design.yml \
	-p plugins/<plugin>/modules.yml
```

## Typical flow

1. run `synth` for the design modules
2. run `export` to package the resulting IPs
3. unpack IPs under `ips/`
4. call `topgen` on the chosen design YAML

