#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/../tools/trigger_demo_verify_env.sh"
python3 -m forge.verification run "$(dirname "$0")/verify.flow.yml" --plugin trigger_demo --consumer-root "$TRIGGER_DEMO_CONSUMER_ROOT" "$@"
