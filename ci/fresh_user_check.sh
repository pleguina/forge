#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# CI Gate: Fresh-User Onboarding Check
# ═══════════════════════════════════════════════════════════════════════════
#
# Validates the forge package from the perspective of a fresh user, and
# doubles as an agnosticism regression guard: everything through Stage 4b
# runs against a freshly `init-plugin`-scaffolded plugin with a generic,
# non-CMS name — if FORGE core ever regresses to assuming OMTF-specific
# names or structure, this is the first place that would break.
#
#   1-4. The documented quickstart sequence, sourced from
#        ci/quickstart_commands.sh (install, forge --help, init-plugin
#        scaffold, doctor) — the same file docs/getting-started/quickstart.md
#        embeds verbatim, so this script and the published docs can never
#        silently diverge.
#   3b.  init-plugin --dry-run regression check
#   4b.  Scaffolded plugin's doctor --json structural check, plus file/
#        import/load assertions the quickstart itself doesn't need
#   5.   forge topgen validate + forge verify prepare --dry-run on trigger_demo
#   6.   trigger_demo reference plugin passes doctor
#   7.   forge unit tests + trigger_demo plugin tests (no HLS / Vivado required)
#
# Prerequisites:
#   - python3 + pip available on PATH
#   - No existing forge install required (stage 1 installs it)
#
# Usage:
#   bash ci/fresh_user_check.sh
#   bash ci/fresh_user_check.sh --skip-install   # re-use already-installed forge
#
# Returns: 0 if all stages pass, non-zero on first failure.
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRIGGER_PLUGIN="${REPO_ROOT}/plugins/trigger_demo"
TRIGGER_FORGE_ROOT="${TRIGGER_PLUGIN}/forge"
cd "${REPO_ROOT}"

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

# ─────────────────────────────────────────────────────────────────────────
section "Stages 1-4: the quickstart command sequence (ci/quickstart_commands.sh)"
# ─────────────────────────────────────────────────────────────────────────
# Stages 1 (install), 2 (CLI check), 3 (scaffold), and 4 (doctor)'s core
# commands are not duplicated here — they are sourced from
# ci/quickstart_commands.sh, the same file docs/getting-started/quickstart.md
# embeds verbatim, so CI keeps proving the exact documented commands work.
# This script's own value-add stays below: extra pytest/pytest-cov install,
# extra CLI --help coverage, a --dry-run regression check, and the deeper
# structural assertions the quickstart itself doesn't need.
SCRATCH_DIR="$(mktemp -d)"
trap 'rm -rf "${SCRATCH_DIR}"' EXIT

PLUGIN_ID="fresh_test_plugin"
PLUGINS_ROOT="${SCRATCH_DIR}"
# shellcheck source=ci/quickstart_commands.sh
source "${REPO_ROOT}/ci/quickstart_commands.sh"
pass "quickstart command sequence (install, forge --help, init-plugin, doctor)"

if ! $SKIP_INSTALL; then
    pip install -q pytest pytest-cov
fi

python3 -c "import forge; print(f'  forge imported OK (location: {forge.__file__})')"
pass "forge package importable"

forge verify doctor --help > /dev/null
pass "forge verify doctor --help"

forge verify generate --help > /dev/null
pass "forge verify generate --help"

forge verify init-plugin --help > /dev/null
pass "forge verify init-plugin --help"

# ─────────────────────────────────────────────────────────────────────────
section "Stage 3b: init-plugin --dry-run regression check"
# ─────────────────────────────────────────────────────────────────────────
forge verify init-plugin "${PLUGIN_ID}_dry_run_only" \
    --plugins-root "${SCRATCH_DIR}" \
    --dry-run | grep -q "Would create"
pass "forge verify init-plugin --dry-run"

SCAFFOLDED_VERIFY="${SCRATCH_DIR}/${PLUGIN_ID}/forge/verify"

for f in \
    "design.verification.yml" \
    "tools/bootstrap.py" \
    "tools/gen_stimulus.py" \
    "schemas/data/${PLUGIN_ID}_golden.xml"
do
    [[ -f "${SCAFFOLDED_VERIFY}/${f}" ]] \
        || fail "init-plugin did not create: ${f}"
done
pass "init-plugin created all expected files under forge/verify/"

# Scaffolded bootstrap.py must import and run standalone — no leftover
# sys.path surgery, no dependency on anything but the installed forge
# package (regression guard for the init-plugin sys.path-hack bug).
python3 - "${SCAFFOLDED_VERIFY}/tools/bootstrap.py" <<'EOF'
import importlib.util, sys
path = sys.argv[1]
spec = importlib.util.spec_from_file_location("bootstrap", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.bootstrap()
print(f"  bootstrap OK, PLUGIN_ID={mod.PLUGIN_ID}")
EOF
pass "Scaffolded bootstrap.py imports and runs standalone"

# design.verification.yml must be loadable
python3 - "${SCAFFOLDED_VERIFY}/design.verification.yml" "${PLUGIN_ID}" <<'EOF'
import sys
from pathlib import Path
from forge.verification.design_contract import load_verify_design

yml, expected_plugin = sys.argv[1], sys.argv[2]
contract = load_verify_design(Path(yml))
assert contract.plugin == expected_plugin, f"wrong plugin: {contract.plugin}"
assert len(contract.flows) >= 1, "no flows declared"
print(f"  loaded {len(contract.flows)} flow(s)")
EOF
pass "Scaffolded design.verification.yml is loadable"

# ─────────────────────────────────────────────────────────────────────────
section "Stage 4b: doctor --json structural check"
# ─────────────────────────────────────────────────────────────────────────
DOCTOR_JSON="${SCRATCH_DIR}/doctor_out.json"
set +e
forge verify doctor "${SCAFFOLDED_VERIFY}/design.verification.yml" --json > "${DOCTOR_JSON}"
set -e

python3 - "${DOCTOR_JSON}" <<'EOF'
import json, sys
data = json.load(open(sys.argv[1]))
for key in ("status", "metrics", "diagnostics"):
    assert key in data, f"missing key: {key}"
assert "counts" in data["metrics"], "missing key: metrics.counts"
print(f"  status={data['status']} counts={data['metrics']['counts']}")
EOF
pass "doctor --json produces valid structured output for scaffolded plugin"

# ─────────────────────────────────────────────────────────────────────────
section "Stage 5: forge topgen validate + prepare --dry-run on trigger_demo"
# ─────────────────────────────────────────────────────────────────────────
[[ -f "${TRIGGER_FORGE_ROOT}/designs/design.yml" ]] \
    || fail "trigger_demo design.yml not found: ${TRIGGER_FORGE_ROOT}/designs/design.yml"

forge topgen validate "${TRIGGER_FORGE_ROOT}/designs/design.yml" > /dev/null
pass "forge topgen validate (trigger_demo)"

forge verify prepare "${TRIGGER_FORGE_ROOT}/verify/design.verification.yml" --dry-run > /dev/null
pass "forge verify prepare --dry-run (trigger_demo)"

# ─────────────────────────────────────────────────────────────────────────
section "Stage 6: forge verify doctor on trigger_demo (canonical reference)"
# ─────────────────────────────────────────────────────────────────────────
TRIGGER_DESIGN="${TRIGGER_FORGE_ROOT}/verify/design.verification.yml"
TRIGGER_JSON="${SCRATCH_DIR}/trigger_doctor.json"

set +e
forge verify doctor "${TRIGGER_DESIGN}" --json > "${TRIGGER_JSON}"
set -e

python3 - "${TRIGGER_JSON}" <<'EOF'
import json, sys
data = json.load(open(sys.argv[1]))
# Zero-port and missing-tool findings are expected/acceptable without
# Vivado in this job; anything else at error severity is a real problem.
errors = [d for d in data["diagnostics"] if d["severity"] == "error"]
hard_errors = [e for e in errors if e.get("code") not in ("FWV015", "FWV019")]
if hard_errors:
    print(f"Unexpected hard errors: {hard_errors}", file=sys.stderr)
    sys.exit(1)
print(f"  status={data['status']} counts={data['metrics']['counts']}")
EOF
pass "trigger_demo doctor: no unexpected hard errors"

# ─────────────────────────────────────────────────────────────────────────
section "Stage 7: forge unit tests + trigger_demo plugin tests"
# ─────────────────────────────────────────────────────────────────────────
python3 -m pytest "${REPO_ROOT}/forge/tests" -q --tb=short -o addopts=''
pass "forge unit tests"

python3 -m pytest "${TRIGGER_FORGE_ROOT}/verify/tools/tests" -q --tb=short
pass "trigger_demo plugin tests"

# ─────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${PASS_COLOR}All fresh-user onboarding checks passed.${NC}"
