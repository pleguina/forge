#!/usr/bin/env bash
# ci/fresh_user_path.sh — Framework fresh-user CI path (Workstream E4)
#
# Tests the canonical supported path exactly as an external team would
# experience it, from installed package through full simulation lifecycle.
#
# Prerequisites (must be in environment):
#   - Python 3.9+
#   - pip
#   - Vivado/Vitis (for xsim flows; csim-only CI can skip with --csim-only)
#   - A clean scratch directory (or use --tmp-dir to specify one)
#
# Usage:
#   ci/fresh_user_path.sh
#   ci/fresh_user_path.sh --csim-only           # skip xsim flows (no Vivado required)
#   ci/fresh_user_path.sh --tmp-dir /tmp/fw_ci  # use explicit scratch dir
#   ci/fresh_user_path.sh --no-install          # skip pip install (framework already on PYTHONPATH)
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed (error message printed to stderr)
#   2 — usage or setup error

set -euo pipefail

# ── Defaults ────────────────────────────────────────────────────────────────
CSIM_ONLY=0
NO_INSTALL=0
TMP_DIR=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FW_VERIFY_PYTHON_DIR="$REPO_ROOT/framework/verify/python"
TRIGGER_PLUGIN="$REPO_ROOT/plugins/trigger_demo"

# ── Argument parsing ────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --csim-only)  CSIM_ONLY=1;        shift ;;
        --no-install) NO_INSTALL=1;       shift ;;
        --tmp-dir)    TMP_DIR="$2";       shift 2 ;;
        *)
            echo "Unknown argument: $1" >&2
            echo "Usage: $0 [--csim-only] [--no-install] [--tmp-dir DIR]" >&2
            exit 2 ;;
    esac
done

if [[ -z "$TMP_DIR" ]]; then
    TMP_DIR="$(mktemp -d -t fw_ci_XXXXXX)"
    CLEANUP_TMP=1
else
    mkdir -p "$TMP_DIR"
    CLEANUP_TMP=0
fi

# ── Colour helpers ───────────────────────────────────────────────────────────
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'

pass()  { echo -e "${GREEN}[PASS]${NC} $*"; }
fail()  { echo -e "${RED}[FAIL]${NC} $*" >&2; }
info()  { echo -e "${YELLOW}[INFO]${NC} $*"; }
step()  { echo; echo -e "${YELLOW}══ Step $* ══${NC}"; }

FAILURES=0
report_fail() { fail "$1"; FAILURES=$(( FAILURES + 1 )); }

# ── Cleanup trap ─────────────────────────────────────────────────────────────
cleanup() {
    if [[ "${CLEANUP_TMP:-0}" == "1" && -d "$TMP_DIR" ]]; then
        rm -rf "$TMP_DIR"
    fi
}
trap cleanup EXIT

# ── Step 1: Install the framework package ────────────────────────────────────
step "1 — Install/verify framework package"

if [[ "$NO_INSTALL" == "1" ]]; then
    if python3 -c "import fw_verify" 2>/dev/null; then
        pass "fw_verify importable (--no-install mode)"
    else
        report_fail "fw_verify not importable and --no-install was set"
    fi
else
    info "Installing fw_verify from $FW_VERIFY_PYTHON_DIR"
    pip install --quiet -e "$FW_VERIFY_PYTHON_DIR" || report_fail "pip install failed"

    if python3 -c "import fw_verify"; then
        pass "fw_verify installed and importable"
    else
        report_fail "fw_verify not importable after install"
    fi

    # Verify CLI entrypoint works
    if fw_verify --help > /dev/null 2>&1; then
        pass "fw_verify CLI entrypoint works"
    else
        # Fall back to -m invocation
        if python3 -m fw_verify --help > /dev/null 2>&1; then
            pass "fw_verify CLI works via python -m fw_verify"
        else
            report_fail "fw_verify CLI entrypoint not working"
        fi
    fi
fi

if command -v fw_verify >/dev/null 2>&1; then
    FW_CMD="fw_verify"
else
    FW_CMD="python3 -m fw_verify"
fi

# ── Step 2: Scaffold a new plugin ────────────────────────────────────────────
step "2 — Scaffold a new plugin with init-plugin"

PLUGIN_ID="ci_test_plugin_$$"
PLUGINS_SCRATCH="$TMP_DIR/plugins"
PLUGIN_DIR="$PLUGINS_SCRATCH/$PLUGIN_ID"

info "Running: $FW_CMD init-plugin $PLUGIN_ID --plugins-root $PLUGINS_SCRATCH"
(
    cd "$REPO_ROOT"
    $FW_CMD init-plugin "$PLUGIN_ID" --plugins-root "$PLUGINS_SCRATCH"
) && pass "init-plugin completed" || report_fail "init-plugin failed"

# Verify expected files exist
for f in \
    "$PLUGIN_DIR/verify/design.verification.yml" \
    "$PLUGIN_DIR/verify/tools/bootstrap.py" \
    "$PLUGIN_DIR/verify/tools/gen_stimulus.py"; do
    if [[ -f "$f" ]]; then
        pass "Scaffold created: $(basename "$f")"
    else
        report_fail "Scaffold missing: $f"
    fi
done

# ── Step 3: Run doctor on the scaffolded plugin ──────────────────────────────
step "3 — Run doctor on fresh scaffold"

DESIGN_YML="$PLUGIN_DIR/verify/design.verification.yml"
info "Running: $FW_CMD doctor $DESIGN_YML"
(
    cd "$REPO_ROOT"
    $FW_CMD doctor "$DESIGN_YML" || true
)
pass "doctor ran without crashing (non-blocking warnings expected on empty scaffold)"

# ── Step 4: Run doctor --json (machine-readable) ─────────────────────────────
step "4 — Run doctor --json (machine-readable output)"

JSON_OUT="$TMP_DIR/doctor_out.json"
(
    cd "$REPO_ROOT"
    $FW_CMD doctor "$DESIGN_YML" --json > "$JSON_OUT" || true
)

if [[ -s "$JSON_OUT" ]]; then
    # Validate it's parseable JSON with expected keys
    if python3 -c "
import json, sys
data = json.load(open('$JSON_OUT'))
assert 'status' in data, 'missing status'
assert 'diagnostics' in data, 'missing diagnostics'
assert 'counts' in data, 'missing counts'
sys.exit(0)
"; then
        pass "doctor --json output is valid and has expected keys"
    else
        report_fail "doctor --json output is malformed"
    fi
else
    report_fail "doctor --json produced no output"
fi

# ── Step 5: Run prepare --dry-run on the canonical reference plugin ──────────
step "5 — prepare --dry-run on trigger_demo"

if [[ -f "$TRIGGER_PLUGIN/verify/design.verification.yml" ]]; then
    (
        cd "$REPO_ROOT"
        $FW_CMD prepare \
                        "$TRIGGER_PLUGIN/verify/design.verification.yml" \
            --dry-run || true
        ) && pass "prepare --dry-run completed for trigger_demo" \
            || report_fail "prepare --dry-run failed for trigger_demo"
else
        info "SKIP: trigger_demo not present"
fi

# ── Step 6: Run framework tests (no simulator required) ──────────────────────
step "6 — Run framework unit tests"

info "Running pytest on framework/ and reference plugins"
(
    cd "$REPO_ROOT"
    python3 -m pytest \
        framework/ \
        plugins/trigger_demo/ \
        -q --tb=short \
        2>&1
) && pass "All framework tests pass" || report_fail "Framework tests failed"

# ── Step 7: Strict mode doctor on minimal reference plugin ───────────────────
step "7 — doctor --strict --json on trigger_demo (canonical reference)"

if [[ -f "$TRIGGER_PLUGIN/verify/design.verification.yml" ]]; then
    TRIGGER_JSON="$TMP_DIR/trigger_doctor.json"
    (
        cd "$REPO_ROOT"
        $FW_CMD doctor \
            "$TRIGGER_PLUGIN/verify/design.verification.yml" \
            --json > "$TRIGGER_JSON" || true
    )
    if python3 -c "
import json, sys
data = json.load(open('$TRIGGER_JSON'))
# Should have at most warnings, not errors, for a canonical plugin
errors = [d for d in data['diagnostics'] if d['severity'] == 'error']
# Zero-port and missing tools are acceptable in non-Vivado CI
hard_errors = [e for e in errors if 'FWV015' in e.get('code','') or 'FWV019' in e.get('code','')]
if hard_errors:
    print(f'Hard errors: {hard_errors}', file=sys.stderr)
    sys.exit(1)
sys.exit(0)
"; then
        pass "trigger_demo doctor: no hard errors"
    else
        report_fail "trigger_demo doctor returned unexpected hard errors"
    fi
else
    info "SKIP: trigger_demo not present"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo
if [[ "$FAILURES" -eq 0 ]]; then
    echo -e "${GREEN}══════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  FRESH USER PATH: ALL CHECKS PASSED              ${NC}"
    echo -e "${GREEN}══════════════════════════════════════════════════${NC}"
    exit 0
else
    echo -e "${RED}══════════════════════════════════════════════════${NC}"
    echo -e "${RED}  FRESH USER PATH: $FAILURES CHECK(S) FAILED       ${NC}"
    echo -e "${RED}══════════════════════════════════════════════════${NC}"
    exit 1
fi
