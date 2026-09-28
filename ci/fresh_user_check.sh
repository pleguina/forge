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
section "Stage 6b: adopt a foreign repository (adopt -> check -> next -> build)"
# ─────────────────────────────────────────────────────────────────────────
# The other half of the fresh-user story. Stages 1-4 cover the user who
# starts from nothing (`forge init`); this covers the user who arrives with
# a repository of their own that was never laid out for FORGE. The fixture
# is deliberately un-FORGE-shaped (rtl/, tb/, constraints/, no FORGE config
# of any kind) and must not be reorganised to make adoption easier — the
# moment it is, it stops testing the thing it exists to test.
#
# Needs no vendor tools: adopt, check, next and build are all pure Python.
FOREIGN_SRC="${REPO_ROOT}/forge/tests/fixtures/foreign/simple_pipeline"
FOREIGN_DIR="${SCRATCH_DIR}/simple_pipeline"
cp -r "${FOREIGN_SRC}" "${FOREIGN_DIR}"

forge adopt "${FOREIGN_DIR}" > /dev/null
for f in forge.yml .forge/project/modules.yml .forge/project/design.yml \
         .forge/contracts/decimator.interface.yaml; do
    [[ -f "${FOREIGN_DIR}/${f}" ]] || fail "forge adopt did not create ${f}"
done
pass "forge adopt (foreign repository -> forge.yml + .forge/)"

# Adoption must produce ordinary FORGE configuration, not a dialect only
# the new commands understand.
forge topgen validate "${FOREIGN_DIR}/.forge/project/design.yml" > /dev/null
pass "forge topgen validate (adopted project)"

forge check "${FOREIGN_DIR}" > /dev/null
pass "forge check (adopted project, no blockers)"

# Run the command `forge next` actually prints. A recommendation that does
# not work is worse than none.
NEXT_JSON="${SCRATCH_DIR}/next.json"
forge next "${FOREIGN_DIR}" --json > "${NEXT_JSON}"
NEXT_CMD="$(python3 - "${NEXT_JSON}" <<'EOF'
import json, sys
action = json.load(open(sys.argv[1]))["metrics"]["recommended_action"]
command = action["command"] or ""
if not command.startswith("forge build "):
    print(f"unexpected recommendation: {action}", file=sys.stderr)
    sys.exit(1)
print(command)
EOF
)"
( cd "${FOREIGN_DIR}" && ${NEXT_CMD} > /dev/null )
[[ -f "${FOREIGN_DIR}/.forge/generated/algo_top.v" ]] \
    || fail "the command forge next recommended did not generate a top level"
grep -q "decimator decimator (" "${FOREIGN_DIR}/.forge/generated/algo_top.v" \
    || fail "generated top level does not instantiate the adopted modules"
pass "forge next -> forge build (real structural top level, zero hand-written YAML)"

# Every decision must be explainable, and every explanation must name the
# source it came from (the canonical IR vs source discovery) — an
# explanation that silently presented inference as resolution would be
# worse than none.
EXPLAIN_JSON="${SCRATCH_DIR}/explain.json"
forge explain shaper.shaped --path "${FOREIGN_DIR}" --json > "${EXPLAIN_JSON}"
python3 - "${EXPLAIN_JSON}" <<'EOF'
import json, sys
explanation = json.load(open(sys.argv[1]))["metrics"]["explanation"]
assert explanation["kind"] == "port", explanation["kind"]
assert explanation["source"] == "canonical IR", explanation["source"]
assert explanation["evidence"], "explanation carries no evidence"
headings = {s["heading"] for s in explanation["sections"]}
assert {"HDL", "Contract", "Resolved connection"} <= headings, sorted(headings)
EOF
pass "forge explain (port explained from the canonical IR, with evidence)"

# `forge fix` must apply what the sources prove and nothing else. Deleting
# a generated contract is the cleanest provable case: the module's ports
# are right there in its own RTL.
rm "${FOREIGN_DIR}/.forge/contracts/packer.interface.yaml"
forge fix "${FOREIGN_DIR}" > /dev/null
[[ -f "${FOREIGN_DIR}/.forge/contracts/packer.interface.yaml" ]] \
    && fail "forge fix wrote without --apply"
forge fix "${FOREIGN_DIR}" --apply > /dev/null
[[ -f "${FOREIGN_DIR}/.forge/contracts/packer.interface.yaml" ]] \
    || fail "forge fix --apply did not regenerate the missing contract"
grep -q "normalization_status: draft" \
    "${FOREIGN_DIR}/.forge/contracts/packer.interface.yaml" \
    || fail "a regenerated contract must be emitted as an unreviewed draft"
pass "forge fix (previews by default, applies only what the sources prove)"

# ─────────────────────────────────────────────────────────────────────────
section "Stage 6c: the rest of the foreign corpus adopts and validates"
# ─────────────────────────────────────────────────────────────────────────
# Project A above is the straight-line baseline. B is an ordinary peripheral
# repository (several source directories, a Makefile and a TCL script, a
# VHDL/Verilog mix, bus naming, a three-clock vendor IP); C is a readout
# chain (an existing structural top with four instances of one module,
# fan-in with per-channel array naming, fan-out to two consumers, a second
# clock domain). Both must adopt into configuration the *existing* validator
# accepts — adoption emitting a dialect only the new commands understand
# would be a silent architectural regression.
for project in peripheral_subsystem daq_readout; do
    src="${REPO_ROOT}/forge/tests/fixtures/foreign/${project}"
    dst="${SCRATCH_DIR}/${project}"
    cp -r "${src}" "${dst}"

    forge adopt "${dst}" > /dev/null
    [[ -f "${dst}/forge.yml" ]] || fail "forge adopt produced no forge.yml for ${project}"
    forge topgen validate "${dst}/.forge/project/design.yml" > /dev/null
    pass "forge adopt + forge topgen validate (${project})"
done

# B's vendor IP has three functional clocks. It must be reported and left
# out, not silently integrated — the regression Project B caught for real.
grep -q "axi_bridge" "${SCRATCH_DIR}/peripheral_subsystem/.forge/project/modules.yml" \
    && fail "a module outside FORGE's clock envelope was written into the registry"
pass "peripheral_subsystem: multi-clock vendor IP excluded from the managed design"

# C's top instantiates channel_decoder four times, and that count is a fact
# its own source proves.
grep -q "instances: 4" "${SCRATCH_DIR}/daq_readout/.forge/project/design.yml" \
    || fail "instance count was not read off the existing structural top"
pass "daq_readout: instance count read off the existing top level"

# Declaring the vendor IP opaque must bring it into the design *and* route
# its extra clock domains to the generated top level. Collapsing three
# functional clocks onto one ap_clk net elaborates cleanly and is silently
# wrong, so this asserts the routing, not just the integration.
PERIPH="${SCRATCH_DIR}/peripheral_subsystem"
printf '\nmodules:\n  axi_bridge:\n    management: opaque\n' >> "${PERIPH}/forge.yml"
forge adopt "${PERIPH}" > /dev/null
grep -q "axi_bridge" "${PERIPH}/.forge/project/modules.yml" \
    || fail "a module declared opaque was still left out of the design"
( cd "${PERIPH}" && forge build .forge/project/design.yml \
    --contracts-from .forge/project/modules.yml --consumer-root . \
    --output .forge/generated/algo_top.v --apply > /dev/null )
for clock in s_axi_aclk m_axi_aclk ref_clk; do
    grep -q "\.${clock}(axi_bridge_${clock})" "${PERIPH}/.forge/generated/algo_top.v" \
        || fail "opaque module's ${clock} was not routed to the top level"
done
grep -q "\.pclk(ap_clk)" "${PERIPH}/.forge/generated/algo_top.v" \
    || fail "the managed modules' own clock is no longer driven by ap_clk"
pass "peripheral_subsystem: opaque module integrated, its clock domains kept apart"

# ─────────────────────────────────────────────────────────────────────────
section "Stage 6d: forge migrate brings a legacy-layout project current"
# ─────────────────────────────────────────────────────────────────────────
# The reference plugin in its committed layout, with its schema versions
# stripped the way a project written before that field existed has them.
# Migration must preview without writing, apply, be idempotent, and leave a
# project the *existing* validator still accepts.
MIG_DIR="${SCRATCH_DIR}/migrate_demo"
cp -r "${TRIGGER_PLUGIN}" "${MIG_DIR}"
python3 - "${MIG_DIR}" <<'EOF'
import re, sys
from pathlib import Path
from forge.project.migrate import project_files
for path in project_files(Path(sys.argv[1])):
    text = path.read_text()
    text = re.sub(r"^\s*schema_version:.*\n", "", text, flags=re.M)
    text = re.sub(r"^registry_version:.*\n", "", text, flags=re.M)
    path.write_text(text)
EOF

forge migrate "${MIG_DIR}" > "${SCRATCH_DIR}/migrate_preview.txt"
grep -q "Required changes:" "${SCRATCH_DIR}/migrate_preview.txt" \
    || fail "forge migrate found nothing to do on a project with no schema versions"
grep -q "schema_version" "${MIG_DIR}/forge/designs/design.yml" \
    && fail "forge migrate wrote without --apply"

forge migrate "${MIG_DIR}" --apply > /dev/null
grep -q "schema_version" "${MIG_DIR}/forge/designs/design.yml" \
    || fail "forge migrate --apply did not declare the design schema version"
forge migrate "${MIG_DIR}" --json > "${SCRATCH_DIR}/migrate_after.json"
python3 - "${SCRATCH_DIR}/migrate_after.json" <<'EOF'
import json, sys
metrics = json.load(open(sys.argv[1]))["metrics"]
assert metrics["up_to_date"], f"migration is not idempotent: {metrics['migrations']}"
EOF
forge topgen validate "${MIG_DIR}/forge/designs/design.yml" > /dev/null
pass "forge migrate (preview -> apply -> idempotent -> still validates)"

# ─────────────────────────────────────────────────────────────────────────
section "Stage 6d2: answering the question FORGE refuses to answer"
# ─────────────────────────────────────────────────────────────────────────
# Phase D4/J3: a decision FORGE will not make can be answered three ways —
# an editor, `forge adopt --interactive`, or `forge connect` — and all three
# write the same ordinary declaration to forge.yml, which is then the only
# place the answer lives.
DECIDE_DIR="${SCRATCH_DIR}/decide"
mkdir -p "${DECIDE_DIR}/rtl"
cat > "${DECIDE_DIR}/rtl/producer.v" <<'RTL'
module producer (input clk, input rst, output [15:0] payload_out);
endmodule
RTL
for consumer in consumer_a consumer_b; do
cat > "${DECIDE_DIR}/rtl/${consumer}.v" <<RTL
module ${consumer} (input clk, input rst, input [15:0] payload_in);
endmodule
RTL
done

forge adopt "${DECIDE_DIR}" --json > "${SCRATCH_DIR}/decide_adopt.json"
python3 - "${SCRATCH_DIR}/decide_adopt.json" <<'EOF'
import json, sys
payload = json.load(open(sys.argv[1]))
questions = [d for d in payload["diagnostics"] if "equally valid consumers" in d["message"]]
assert questions, "adoption did not raise the ambiguous-consumer question"
assert payload["metrics"]["unresolved_decisions"] == 1, payload["metrics"]
EOF

forge connect producer.payload_out consumer_b.payload_in --project "${DECIDE_DIR}" > /dev/null
grep -q "from: producer.payload_out" "${DECIDE_DIR}/forge.yml" \
    || fail "forge connect did not record the declaration in forge.yml"

# The question is settled everywhere, and the answer became real wiring.
forge adopt "${DECIDE_DIR}" --force --json > "${SCRATCH_DIR}/decide_again.json"
python3 - "${SCRATCH_DIR}/decide_again.json" "${DECIDE_DIR}" <<'EOF'
import json, sys
from pathlib import Path
payload = json.load(open(sys.argv[1]))
assert payload["metrics"]["unresolved_decisions"] == 0, payload["metrics"]
design = (Path(sys.argv[2]) / ".forge/project/design.yml").read_text()
assert "consumer_b" in design and "payload_out" in design, design
EOF
forge check "${DECIDE_DIR}" --json > "${SCRATCH_DIR}/decide_check.json"
python3 - "${SCRATCH_DIR}/decide_check.json" <<'EOF'
import json, sys
payload = json.load(open(sys.argv[1]))
blockers = [d for d in payload["diagnostics"] if d.get("code") == "ATG037"]
assert not blockers, blockers
EOF
pass "forge connect (declared once, honoured by adopt and check)"

# ─────────────────────────────────────────────────────────────────────────
section "Stage 6d3: the HLS predictor's per-version validation"
# ─────────────────────────────────────────────────────────────────────────
# Phase I3: what FORGE claims about a Vitis HLS release must be backed by
# recorded synthesis output from that release — and nothing may be claimed
# about a release nobody tested. Needs no Vitis HLS installed: it replays a
# prediction against output already in the repository.
python3 - <<'EOF'
from forge.hls import tool_matrix

versions = tool_matrix.known_versions()
assert versions, "no Vitis HLS output recorded — the predictor is validated against nothing"
for version in versions:
    report = tool_matrix.validate_version(version)
    assert report.status == tool_matrix.VERIFIED, report.report()
assert tool_matrix.status_for("2099.1") == tool_matrix.UNTESTED
EOF
pass "HLS port prediction validated per Vitis HLS release"

# ─────────────────────────────────────────────────────────────────────────
section "Stage 6e: one generation run, one IR — manifest, plan, and round-trip"
# ─────────────────────────────────────────────────────────────────────────
# Phase G: the build manifest, the verification plan and every generated
# report come off the canonical IR of the run that produced them. Uses
# passthrough_demo, the reference consumer that needs no EDA tools, laid out
# the way its README documents (consumer root = working root, DUT under
# gen-top/<design>/ — which is what attributes a verification flow to a
# design).
IR_DIR="${SCRATCH_DIR}/ir_demo"
mkdir -p "${IR_DIR}"
(
  cd "${IR_DIR}"
  forge topgen gen-top "${REPO_ROOT}/plugins/passthrough_demo/forge/designs/design.yml" \
      --mode verilog \
      --consumer-root . \
      --contracts-from "${REPO_ROOT}/plugins/passthrough_demo/forge/modules.yml" \
      --build-dir build \
      --output gen-top/design_passthrough_demo/algo_top.v > "${SCRATCH_DIR}/ir_gen_top.txt"
)
grep -q "flow(s) target this design" "${SCRATCH_DIR}/ir_gen_top.txt" \
    || fail "gen-top resolved no verification plan for passthrough_demo"

python3 - "${IR_DIR}/gen-top/design_passthrough_demo" <<'EOF'
import json, sys
from pathlib import Path

from forge.ir.deserialize import from_json_dict
from forge.ir.serialize import content_hash

out = Path(sys.argv[1])
project, notes = from_json_dict(json.loads((out / "design.ir.json").read_text()))
assert notes == [], f"a freshly written IR needed migrating: {notes}"

# Every generated report names the resolved design it was built from.
ir_hash = content_hash(project)
manifest = json.loads((out / "build_manifest.json").read_text())
maturity = json.loads((out / "maturity_report.json").read_text())
assert manifest["ir_content_hash"] == ir_hash, "manifest records a different IR"
assert maturity["ir_content_hash"] == ir_hash, "maturity report records a different IR"

# The verification plan resolved this plugin's flows against this design,
# and every stimulus point names the instance pin behind it.
plan = project.design.verification_plan
assert plan.populated, "verification plan not populated"
assert plan.flows and all(f.targets_this_design for f in plan.flows), plan.flows
assert not any(f.unresolved_reason for f in plan.flows), plan.flows
assert plan.stimulus and all(b.instance_id and b.instance_port for b in plan.stimulus)
EOF
pass "gen-top: manifest + maturity + verification plan all read one IR"

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
