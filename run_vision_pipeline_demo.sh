#!/usr/bin/env bash
# run_vision_pipeline_demo.sh — Thin wrapper around
# plugins/vision_pipeline_demo/tutorial/runner.py, the progressive
# tutorial runner for vision_pipeline_demo.
#
# All real logic (which designs/flows exist, what order they run in, how
# HLS/gen-top/verify are invoked) lives in tutorial.yml + runner.py, not
# here — this script only forwards arguments and fails clearly if `forge`
# or `python3` aren't available. See runner.py's own module docstring for
# the full stage sequence.
#
# Usage (from arc-framework repo root):
#   ./run_vision_pipeline_demo.sh                 # every step, all designs/flows
#   ./run_vision_pipeline_demo.sh --list           # list every tutorial step
#   ./run_vision_pipeline_demo.sh --step quickstart          # just one step
#   ./run_vision_pipeline_demo.sh --step quickstart --step cdc  # more than one
#   ./run_vision_pipeline_demo.sh --all             # explicit "every step"
#   ./run_vision_pipeline_demo.sh --skip-hls        # reuse an existing HLS build
#   ./run_vision_pipeline_demo.sh --no-clean        # skip the pre-run artifact wipe
#   ./run_vision_pipeline_demo.sh --jobs=8          # parallel HLS jobs (default 4)
#
# Prerequisites:
#   - forge CLI installed:  pip install -e forge/
#   - Vitis HLS on PATH  (only for steps that use an HLS module; `--step
#     cdc` needs neither Vitis HLS nor a full build)
#   - Vivado xsim on PATH (for every xsim-backed flow)
#
set -euo pipefail
cd "$(dirname "$0")"          # always run from repo root

command -v forge >/dev/null 2>&1 || { echo "forge not found - run: pip install -e forge/" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 not found on PATH" >&2; exit 1; }

# runner.py's own argparse accepts --jobs=N or --jobs N; forward args as-is.
args=()
for arg in "$@"; do
  if [[ "$arg" == --jobs=* ]]; then
    args+=("--jobs" "${arg#*=}")
  else
    args+=("$arg")
  fi
done

exec python3 plugins/vision_pipeline_demo/tutorial/runner.py "${args[@]}"
