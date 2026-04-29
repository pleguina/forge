#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/../../tools/trigger_demo_verify_env.sh"
python3 -m fw_verify run "$(dirname "$0")/verify.flow.yml" "$@"
