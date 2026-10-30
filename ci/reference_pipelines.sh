#!/usr/bin/env bash
# =============================================================================
# ci/reference_pipelines.sh — the runs hosted CI cannot do, with their evidence.
#
# Needs Vivado and Vitis HLS (VIVADO_SETTINGS, VITIS_HLS_SETTINGS: their
# settings64.sh), Verilator, ipbb, and checkouts of emp-fwk, ipbus-firmware,
# cms-tcds2-firmware and tclink (EMP_FWK, IPBUS_FW, EMP_DEPS, as
# plugins/trigger_demo/emp/README.md describes). Writes into OUT (default
# reference-evidence/):
#
#   versions.txt                 tools, emp-fwk release, FORGE commit
#   simple_pipeline/             forge test from input/expected on XSim and
#                                Verilator: evidence JSON, JUnit, logs
#   trigger_demo_emp/            HLS synthesis, forge build, ipbb dep tree,
#                                emp-fwk testbench in XSim: sink pattern file,
#                                check result, logs
#
# Exits non-zero when any run fails; what ran is kept either way.
#
# Usage: bash ci/reference_pipelines.sh [OUT]
# =============================================================================
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$(mkdir -p "${1:-reference-evidence}" && cd "${1:-reference-evidence}" && pwd)"
: "${VIVADO_SETTINGS:?settings64.sh of Vivado}" "${VITIS_HLS_SETTINGS:?settings64.sh of Vitis HLS}"
: "${EMP_FWK:?emp-fwk checkout}" "${IPBUS_FW:?ipbus-firmware checkout}"
# shellcheck disable=SC1090
source "${VIVADO_SETTINGS}" > /dev/null
# shellcheck disable=SC1090
source "${VITIS_HLS_SETTINGS}" > /dev/null
status=0

{
    echo "forge $(git -C "${REPO_ROOT}" rev-parse HEAD)$(git -C "${REPO_ROOT}" diff --quiet || echo ' (modified)')"
    vivado -version | head -1
    vitis_hls -version | head -1
    verilator --version
    echo "emp-fwk $(git -C "${EMP_FWK}" describe --tags --always)"
} > "${OUT}/versions.txt" 2>&1

echo "=== simple_pipeline: forge test on XSim and Verilator ==="
mkdir -p "${OUT}/simple_pipeline"
if bash "${REPO_ROOT}/ci/verification_fresh_user.sh" "${OUT}/simple_pipeline/work" > "${OUT}/simple_pipeline/fresh_user.log" 2>&1 \
   && (cd "${OUT}/simple_pipeline/work/simple_pipeline" \
       && forge test --input input.json --expected expected.json --level design --json > ../../both_backends.json); then
    echo "  passed"
else
    echo "  FAILED (see ${OUT}/simple_pipeline)"
    status=1
fi
cp -r "${OUT}/simple_pipeline/work/simple_pipeline/.forge/reports/test/auto" "${OUT}/simple_pipeline/evidence" 2>/dev/null

echo "=== trigger_demo in emp-fwk's testbench ==="
mkdir -p "${OUT}/trigger_demo_emp"
if bash "${REPO_ROOT}/plugins/trigger_demo/emp/run_emp_sim.sh" "${OUT}/trigger_demo_emp/work" > "${OUT}/trigger_demo_emp/run.log" 2>&1; then
    echo "  passed"
else
    echo "  FAILED (see ${OUT}/trigger_demo_emp/run.log)"
    status=1
fi
grep -E "^(PASS|FAIL)" "${OUT}/trigger_demo_emp/run.log" > "${OUT}/trigger_demo_emp/check.txt" || true

exit "${status}"
