#!/usr/bin/env bash
# trigger_demo_verify_env.sh — Runtime path resolution for the trigger_demo plugin.
#
# Usage:  source plugins/trigger_demo/verify/tools/trigger_demo_verify_env.sh
#
# Appropriate env-var overrides:
#   TRIGGER_DEMO_CONSUMER_ROOT    Override repo root
#   TRIGGER_DEMO_HLS_BUILD_ROOT   Redirect HLS build root

TD_VERIFY_ENV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TD_PLUGIN_VERIFY_DIR="$(cd "$TD_VERIFY_ENV_DIR/.." && pwd)"
TD_PLUGIN_ROOT="$(cd "$TD_PLUGIN_VERIFY_DIR/.." && pwd)"
TRIGGER_DEMO_CONSUMER_ROOT="${TRIGGER_DEMO_CONSUMER_ROOT:-$(cd "$TD_PLUGIN_ROOT/../.." && pwd)}"
TRIGGER_DEMO_CONSUMER_ROOT="$(cd "$TRIGGER_DEMO_CONSUMER_ROOT" && pwd)"

TRIGGER_DEMO_HLS_BUILD_ROOT="${TRIGGER_DEMO_HLS_BUILD_ROOT:-$TRIGGER_DEMO_CONSUMER_ROOT/build_hls_trigger_demo}"

# ── Put framework Python on path ────────────────────────────────────────────
FW_PYTHON="$TRIGGER_DEMO_CONSUMER_ROOT/framework/verify/python"
TD_TOOLS="$TD_VERIFY_ENV_DIR"
export PYTHONPATH="$FW_PYTHON:$TD_TOOLS:${PYTHONPATH:-}"

export TD_PLUGIN_ROOT
export TD_PLUGIN_VERIFY_DIR
export TRIGGER_DEMO_CONSUMER_ROOT
export TRIGGER_DEMO_HLS_BUILD_ROOT
