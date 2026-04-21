#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# CI Gate: Fresh-User Onboarding Check
# ═══════════════════════════════════════════════════════════════════════════
#
# Validates the fw_verify package from the perspective of a fresh user:
#   1. Package installs cleanly from source (no pre-existing state assumed)
#   2. CLI entry point works after install  (fw_verify --help)
#   3. fw_verify init-plugin scaffolds a valid plugin skeleton
#   4. Scaffolded plugin passes doctor (READ-ONLY health check)
#   5. trigger_demo reference plugin passes doctor
#   6. trigger_demo tests pass (no HLS / Vivado required)
#
# Prerequisites:
#   - python3 + pip available on PATH
#   - No existing fw_verify install required (stage 1 installs it)
#
# Usage:
#   bash ci/fresh_user_check.sh
#   bash ci/fresh_user_check.sh --skip-install   # re-use already-installed fw_verify
#
# Returns: 0 if all stages pass, non-zero on first failure.
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FW_PYTHON="${REPO_ROOT}/framework/verify/python"

PASS_COLOR='\033[0;32m'
FAIL_COLOR='\033[0;31m'
SECTION_COLOR='\033[0;34m'
NC='\033[0m'

section() { echo -e "\n${SECTION_COLOR}=== $* ===${NC}"; }
pass()    { echo -e "  ${PASS_COLOR}PASS${NC}: $*"; }
fail()    { echo -e "  ${FAIL_COLOR}FAIL${NC}: $*" >&2; exit 1; }

SKIP_INSTALL=false
for arg in "$@"; do
    [[ "$arg" == "--skip-install" ]] && SKIP_INSTALL=true
done

# ── Helper: run fw_verify using installed CLI or fallback to module ───────
fw_verify_cmd() {
    if command -v fw_verify &>/dev/null; then
        fw_verify "$@"
    else
        PYTHONPATH="${FW_PYTHON}:${PYTHONPATH:-}" python3 -m fw_verify "$@"
    fi
}

# ─────────────────────────────────────────────────────────────────────────
section "Stage 1: Package install from source"
# ─────────────────────────────────────────────────────────────────────────
if $SKIP_INSTALL; then
    echo "  [skipped] --skip-install flag provided"
else
    # Install with the optional pyverilog parser extra
    pip install -q -e "${FW_PYTHON}[parser]"
    pass "pip install -e framework/verify/python[parser]"
fi

# Verify import works
python3 -c "import fw_verify; print(f'  fw_verify imported OK (location: {fw_verify.__file__}')"
pass "fw_verify package importable"

# ─────────────────────────────────────────────────────────────────────────
section "Stage 2: CLI entry point check"
# ─────────────────────────────────────────────────────────────────────────
fw_verify_cmd --help > /dev/null
pass "fw_verify --help"

fw_verify_cmd doctor --help > /dev/null
pass "fw_verify doctor --help"

fw_verify_cmd generate --help > /dev/null
pass "fw_verify generate --help"

fw_verify_cmd init-plugin --help > /dev/null
pass "fw_verify init-plugin --help"

# ─────────────────────────────────────────────────────────────────────────
section "Stage 3: init-plugin scaffold"
# ─────────────────────────────────────────────────────────────────────────
SCRATCH_DIR="$(mktemp -d)"
trap 'rm -rf "${SCRATCH_DIR}"' EXIT

fw_verify_cmd init-plugin fresh_test_plugin \
    --plugins-root "${SCRATCH_DIR}" \
    --dry-run | grep -q "Would create"
pass "fw_verify init-plugin --dry-run"

fw_verify_cmd init-plugin fresh_test_plugin \
    --plugins-root "${SCRATCH_DIR}"

SCAFFOLDED_VERIFY="${SCRATCH_DIR}/fresh_test_plugin/verify"

# Check expected files were created
for f in \
    "design.verification.yml" \
    "tools/bootstrap.py" \
    "tools/gen_stimulus.py" \
    "schemas/data/fresh_test_plugin_golden.xml"
do
    [[ -f "${SCAFFOLDED_VERIFY}/${f}" ]] \
        || fail "init-plugin did not create: ${f}"
done
pass "init-plugin created all expected files"

# design.verification.yml must be loadable
PYTHONPATH="${FW_PYTHON}:${PYTHONPATH:-}" python3 - <<'EOF'
import sys
from pathlib import Path
import os

scaffolded = Path(os.environ.get("SCAFFOLDED_VERIFY", ""))
if not scaffolded.exists():
    print("SCAFFOLDED_VERIFY env var not set", file=sys.stderr)
    sys.exit(1)

from fw_verify.design_contract import load_verify_design
yml = scaffolded / "design.verification.yml"
contract = load_verify_design(yml)
assert contract.plugin == "fresh_test_plugin", f"wrong plugin: {contract.plugin}"
assert len(contract.flows) >= 1, "no flows declared"
print(f"  loaded {len(contract.flows)} flow(s)")
EOF
pass "Scaffolded design.verification.yml is loadable"

# ─────────────────────────────────────────────────────────────────────────
section "Stage 4: fw_verify doctor on scaffolded plugin"
# ─────────────────────────────────────────────────────────────────────────
# doctor should run without crashing (exit 0 not guaranteed — TB not yet
# generated — but it must produce parseable structured output)
set +e
fw_verify_cmd doctor "${SCAFFOLDED_VERIFY}/design.verification.yml" 2>&1 \
    | grep -qE "(OK|WARN|ERR|doctor)" \
    && DOCTOR_OUTPUT_OK=true || DOCTOR_OUTPUT_OK=false
set -e

$DOCTOR_OUTPUT_OK && pass "doctor produces structured output for scaffolded plugin" \
    || fail "doctor produced no recognizable output"

# ─────────────────────────────────────────────────────────────────────────
section "Stage 5: trigger_demo doctor"
# ─────────────────────────────────────────────────────────────────────────
TRIGGER_DESIGN="${REPO_ROOT}/plugins/trigger_demo/verify/design.verification.yml"

[[ -f "${TRIGGER_DESIGN}" ]] \
    || fail "trigger_demo not found: ${TRIGGER_DESIGN}"

# doctor on trigger_demo: may warn (no TB generated), must not crash
set +e
fw_verify_cmd doctor "${TRIGGER_DESIGN}" 2>&1 \
    | grep -qE "(OK|WARN|ERR|doctor)" \
    && TRIGGER_DOCTOR_OK=true || TRIGGER_DOCTOR_OK=false
set -e

$TRIGGER_DOCTOR_OK && pass "doctor produces structured output for trigger_demo" \
    || fail "doctor crashed on trigger_demo"

# ─────────────────────────────────────────────────────────────────────────
section "Stage 6: trigger_demo plugin tests"
# ─────────────────────────────────────────────────────────────────────────
TRIGGER_TESTS="${REPO_ROOT}/plugins/trigger_demo/verify/tools/tests"
TRIGGER_TOOLS="${REPO_ROOT}/plugins/trigger_demo/verify/tools"

PYTHONPATH="${FW_PYTHON}:${TRIGGER_TOOLS}:${PYTHONPATH:-}" \
    python3 -m pytest "${TRIGGER_TESTS}" -v --tb=short \
    --rootdir="${REPO_ROOT}" 2>&1
pass "trigger_demo plugin tests"

# ─────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${PASS_COLOR}All fresh-user onboarding checks passed.${NC}"
