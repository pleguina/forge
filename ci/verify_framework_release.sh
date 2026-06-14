#!/usr/bin/env bash
# CI Gate: Framework Release Readiness
#
# Exercises only framework-owned surfaces and supported non-OMTF proof
# consumers. This is the gate the future standalone framework repository must
# pass without requiring any downstream plugin mount to be present.
#
# Stages:
#   1. Framework standalone validation
#   2. Supported proof consumer pytest suites
#   3. Framework Python module sanity
#   4. design.verification.yml loading for supported proof consumers
#   5. fw_verify generate CLI dry-run smoke
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FW_PYTHON="${REPO_ROOT}/verify/python"

PASS_COLOR='\033[0;32m'
FAIL_COLOR='\033[0;31m'
SECTION_COLOR='\033[0;34m'
NC='\033[0m'

section() { echo -e "\n${SECTION_COLOR}=== $* ===${NC}"; }
pass()    { echo -e "  ${PASS_COLOR}PASS${NC}: $*"; }
fail()    { echo -e "  ${FAIL_COLOR}FAIL${NC}: $*" >&2; exit 1; }

if [[ -f "${REPO_ROOT}/.venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/.venv/bin/activate"
fi

section "Framework standalone validation"
bash "${REPO_ROOT}/validate_framework.sh"
pass "validate_framework.sh"

run_test_suite() {
    local label="$1"
    local suite_dir="$2"
    section "${label} proof consumer tests"
    if python3 -m pytest "${suite_dir}" --tb=short -q --rootdir="${REPO_ROOT}"; then
        pass "${label} pytest suite"
    else
        fail "${label} pytest suite"
    fi
}

run_test_suite \
    "trigger_demo" \
    "${REPO_ROOT}/plugins/trigger_demo/verify/tools/tests"
section "Framework Python module sanity"
if PYTHONPATH="${FW_PYTHON}:${PYTHONPATH:-}" python3 -c "
import fw_verify.backend_registry as br
import fw_verify.design_contract as dc
import fw_verify.flow_loader as fl
import fw_verify.gen_sim as gs
import fw_verify.plugin_registry as pr
import fw_verify.preflight as pf
print('All framework modules import OK')
print(f'  flow kinds: {sorted(fl._ALLOWED_FLOW_KINDS)}')
print(f'  generate_verify_flow_yml: {gs.generate_verify_flow_yml}')
print(f'  find_verify_design: {dc.find_verify_design}')
"; then
    pass "framework module imports"
else
    fail "framework module import sanity"
fi

section "Supported proof-consumer design loading"
for contract_dir in \
    "${REPO_ROOT}/plugins/trigger_demo/verify"
do
    plugin_name="$(basename "$(dirname "$contract_dir")")"
    if PYTHONPATH="${FW_PYTHON}:${PYTHONPATH:-}" python3 -c "
import sys
from pathlib import Path
from fw_verify.design_contract import find_verify_design, load_verify_design
d = Path('${contract_dir}')
p = find_verify_design(d)
if p is None:
    print(f'ERROR: no design.verification.yml found in {d}', file=sys.stderr)
    sys.exit(1)
contract = load_verify_design(p)
print(f'plugin={contract.plugin}, flows={len(contract.flows)}, datasets={len(contract.datasets)}')
"; then
        pass "${plugin_name} design.verification.yml"
    else
        fail "${plugin_name} design.verification.yml load"
    fi
done

section "trigger_demo topology generation smoke"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT
if topgen gen-top \
    "${REPO_ROOT}/plugins/trigger_demo/designs/design.yml" \
    --consumer-root "${REPO_ROOT}" \
    --contracts-from "${REPO_ROOT}/plugins/trigger_demo/modules.yml" \
    --build-dir "${REPO_ROOT}/build_hls_trigger_demo" \
    --strict \
    --mode verilog \
    --output "${TMP_DIR}/algo_top.v" > /dev/null 2>&1; then
    pass "trigger_demo strict gen-top"
else
    fail "trigger_demo strict gen-top"
fi

section "fw_verify generate CLI smoke"
DESIGN_FILE="${REPO_ROOT}/plugins/trigger_demo/verify/design.verification.yml"
if PYTHONPATH="${FW_PYTHON}:${PYTHONPATH:-}" python3 -m fw_verify generate \
    "${DESIGN_FILE}" --flow hit_decoder_csim --dry-run 2>&1 | grep -q "would write"; then
    pass "fw_verify generate dry-run"
else
    fail "fw_verify generate dry-run"
fi

if PYTHONPATH="${FW_PYTHON}:${PYTHONPATH:-}" python3 -m fw_verify generate \
    "${DESIGN_FILE}" --flow trigger_pipeline_xsim --dry-run 2>&1 | grep -q "would write"; then
    pass "fw_verify generate dry-run (trigger_pipeline_xsim)"
else
    fail "fw_verify generate dry-run (trigger_pipeline_xsim)"
fi

echo ""
echo -e "${PASS_COLOR}Framework release gate passed without OMTF dependency.${NC}"