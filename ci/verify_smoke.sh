#!/usr/bin/env bash
# =============================================================================
# ci/verify_smoke.sh — every reference plugin's verification setup, generated
# from a clean checkout and checked by `forge dev verify doctor`.
#
# The flow files are not committed, so this does what a user does before a
# run: gen-top each design the flows simulate (from the registries' declared
# ports, no HLS build needed), `forge dev verify generate`, then each plugin's
# stimulus generators. No simulator is needed, and none of it is run.
#
# Writes gen-top/ and the plugins' flow directories in place (all ignored by
# git): run it in a CI checkout or a scratch copy.
#
# Usage: bash ci/verify_smoke.sh
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

gen_top() {  # <plugin> <design.yml> <gen-top subdir>
    echo "--- gen-top $1/$2 ---"
    forge dev topgen gen-top "plugins/$1/forge/designs/$2" \
        --mode verilog --consumer-root . \
        --contracts-from "plugins/$1/forge/modules.yml" \
        --output "gen-top/$3/algo_top.v" > /dev/null
}

generate() {  # <plugin>
    echo "--- forge dev verify generate $1 ---"
    forge dev verify generate "plugins/$1/forge/verify/design.verification.yml"
}

doctor() {  # <plugin>
    echo "--- forge dev verify doctor $1 ---"
    forge dev verify doctor "plugins/$1/forge/verify/design.verification.yml"
}

# ── passthrough_demo ─────────────────────────────────────────────────────────
gen_top passthrough_demo design.yml design_passthrough_demo
generate passthrough_demo
for flow in passthrough_xsim passthrough_verilator passthrough_readmemh passthrough_readmemh_verilator; do
    python3 plugins/passthrough_demo/forge/verify/tools/gen_stimulus.py --flow "${flow}" > /dev/null
done
doctor passthrough_demo

# ── trigger_demo ─────────────────────────────────────────────────────────────
# The single-module flows simulate HLS RTL and take their ports from the
# hand-written port_map.yaml next to them; only the pipeline needs gen-top.
gen_top trigger_demo design.yml design_trigger_demo_pipeline
generate trigger_demo
python3 plugins/trigger_demo/forge/verify/tools/gen_stimulus.py > /dev/null
doctor trigger_demo

# ── vision_pipeline_demo ─────────────────────────────────────────────────────
# tutorial.yml names each step's design, its gen-top directory and the
# stimulus command of each flow.
steps="$(mktemp)"
trap 'rm -f "${steps}"' EXIT
python3 - <<'EOF' > "${steps}"
import yaml
for step in yaml.safe_load(open("plugins/vision_pipeline_demo/tutorial.yml"))["steps"]:
    if step["kind"] != "design":
        continue
    print("design", step["design"], step["gen_top_outdir"])
    for flow in step.get("flows", []):
        if flow.get("gen_stimulus"):
            print("stimulus", flow["gen_stimulus"])
EOF
while read -r kind first second; do
    if [[ "${kind}" == design ]]; then
        gen_top vision_pipeline_demo "${first}" "${second}"
    fi
done < "${steps}"
generate vision_pipeline_demo
while read -r kind script args; do
    if [[ "${kind}" == stimulus ]]; then
        # shellcheck disable=SC2086  # args is a word list from tutorial.yml
        python3 "plugins/vision_pipeline_demo/forge/verify/tools/${script}" ${args} > /dev/null
    fi
done < "${steps}"
doctor vision_pipeline_demo

echo "PASS: every reference plugin's flows generate and pass doctor."
