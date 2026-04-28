#!/usr/bin/env bash
# run_trigger_demo.sh — Full trigger_demo pipeline from zero.
#
# Runs every stage in order:
#   1. Clean stale build artifacts
#   2. Validate design.yml + modules.yml
#   3. arc topgen gen-top  → gen-top/design_trigger_demo_pipeline/
#   4. arc verify generate → regenerate flow YAML / SV / TCL files
#   5. gen_stimulus.py     → stimulus_current.svh for every xsim flow
#   6. arc verify doctor   → readiness health-check
#   7. arc hls run csim    → C-simulation for all 4 HLS modules
#   8. arc hls run synth   → RTL synthesis for all 4 HLS modules (needed by xsim)
#   9. arc verify run      → all 9 flows (csim × 4, xsim × 4, full-chip × 1)
#
# Usage (from arc-framework repo root):
#   ./run_trigger_demo.sh              # full run
#   ./run_trigger_demo.sh --skip-hls  # skip HLS gen-tcl + csim + synth (use existing build_hls_trigger_demo/)
#   ./run_trigger_demo.sh --no-clean  # skip artifact cleanup
#
# Prerequisites:
#   - arc CLI installed:  pip install -e arc/
#   - Vitis HLS on PATH  (for stages 7–8; skip with --skip-hls)
#   - Vivado xsim on PATH (for stage 9 xsim flows)
#
set -euo pipefail

# ── Defaults ────────────────────────────────────────────────────────────────
SKIP_HLS=0
NO_CLEAN=0
JOBS=4
HLS_BUILD_ROOT="build_hls_trigger_demo"
GEN_TOP_OUT="gen-top/design_trigger_demo_pipeline/algo_top.v"
PLUGIN_ROOT="plugins/trigger_demo"
DESIGN_YML="$PLUGIN_ROOT/designs/design.yml"
MODULES_YML="$PLUGIN_ROOT/modules.yml"
VERIFY_YML="$PLUGIN_ROOT/verify/design.verification.yml"
GEN_STIMULUS="$PLUGIN_ROOT/verify/tools/gen_stimulus.py"

# ── Argument parsing ─────────────────────────────────────────────────────────
for arg in "$@"; do
  case "$arg" in
    --skip-hls)  SKIP_HLS=1 ;;
    --no-clean)  NO_CLEAN=1 ;;
    --jobs=*)    JOBS="${arg#*=}" ;;
    -h|--help)
      sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

# ── Helpers ──────────────────────────────────────────────────────────────────
BOLD=$'\e[1m'; RESET=$'\e[0m'; GREEN=$'\e[32m'; RED=$'\e[31m'; CYAN=$'\e[36m'
step() { echo; echo "${BOLD}${CYAN}══ $* ══${RESET}"; }
ok()   { echo "${GREEN}✔  $*${RESET}"; }
die()  { echo "${RED}✘  $*${RESET}" >&2; exit 1; }

cd "$(dirname "$0")"          # always run from repo root

# ── Guard: arc must be installed ─────────────────────────────────────────────
command -v arc >/dev/null 2>&1 || die "arc not found — run: pip install -e arc/"

# ════════════════════════════════════════════════════════════════════════════
# 1. Clean stale artifacts
# ════════════════════════════════════════════════════════════════════════════
if [[ $NO_CLEAN -eq 0 ]]; then
  step "Clean stale artifacts"
  rm -rf \
    "gen-top/design_trigger_demo_pipeline" \
    "out" \
    "build"
  if [[ $SKIP_HLS -eq 0 ]]; then
    rm -rf "$HLS_BUILD_ROOT"
  fi
  ok "Clean done"
fi

# ════════════════════════════════════════════════════════════════════════════
# 2. Validate topology
# ════════════════════════════════════════════════════════════════════════════
step "Validate design + registry"
arc topgen validate          "$DESIGN_YML"
arc topgen validate-registry "$MODULES_YML"
ok "Validation passed"

# ════════════════════════════════════════════════════════════════════════════
# 2b. Preflight: verify include headers exist before paying HLS time
# ════════════════════════════════════════════════════════════════════════════
step "Preflight: check required HLS include headers and sources"
_MISSING=0
for _HDR in \
  "$PLUGIN_ROOT/verify/include/dut_adapter.h" \
  "$PLUGIN_ROOT/verify/include/transaction_concepts.h" \
  "$PLUGIN_ROOT/verify/include/driver.h" \
  "$PLUGIN_ROOT/algo/common/trigger_types.h" \
  "$PLUGIN_ROOT/verify/src/logging.cpp"
do
  if [[ ! -f "$_HDR" ]]; then
    echo "  MISSING: $_HDR" >&2
    _MISSING=$(( _MISSING + 1 ))
  fi
done
[[ $_MISSING -eq 0 ]] || die "$_MISSING required file(s) missing — fix include paths in $MODULES_YML before running HLS"
ok "All required headers present"

# ════════════════════════════════════════════════════════════════════════════
# 3. Generate algo_top
# ════════════════════════════════════════════════════════════════════════════
step "arc topgen gen-top → $GEN_TOP_OUT"
arc topgen gen-top "$DESIGN_YML" \
  --mode verilog \
  --consumer-root . \
  --contracts-from "$MODULES_YML" \
  --hls-build-root "$HLS_BUILD_ROOT" \
  --output "$GEN_TOP_OUT"
ok "algo_top generated"

# ════════════════════════════════════════════════════════════════════════════
# 4. Regenerate verification flow files
# ════════════════════════════════════════════════════════════════════════════
step "arc verify generate"
arc verify generate "$VERIFY_YML"
ok "Verify flow files regenerated"

# ════════════════════════════════════════════════════════════════════════════
# 5. Generate stimulus SVH files
# ════════════════════════════════════════════════════════════════════════════
step "gen_stimulus.py — stimulus_current.svh for each xsim flow"
python3 "$GEN_STIMULUS"
ok "Stimulus files generated"

# ════════════════════════════════════════════════════════════════════════════
# 6. Doctor check
# ════════════════════════════════════════════════════════════════════════════
step "arc verify doctor"
arc verify doctor "$VERIFY_YML"
ok "Doctor passed"

# ════════════════════════════════════════════════════════════════════════════
# 7 & 8. HLS builds
# ════════════════════════════════════════════════════════════════════════════
HLS_MODULES="hit_decoder hit_collector trigger_logic trigger_output"

if [[ $SKIP_HLS -eq 1 ]]; then
  echo "(--skip-hls: skipping HLS TCL generation + csim + synth)"
else
  step "arc hls gen-tcl — generate Vitis HLS TCL scripts"
  arc hls gen-tcl \
    --hls-config "$MODULES_YML" \
    --output-dir "$HLS_BUILD_ROOT"
  ok "HLS TCL scripts generated"

  step "arc hls run — csim (${JOBS} parallel job(s))"
  arc hls run \
    --registry "$MODULES_YML" \
    --hls-build-root "$HLS_BUILD_ROOT" \
    --stages csim \
    --jobs "$JOBS" \
    --modules "$HLS_MODULES"
  ok "HLS csim done"

  step "arc hls run — synth (${JOBS} parallel job(s))"
  arc hls run \
    --registry "$MODULES_YML" \
    --hls-build-root "$HLS_BUILD_ROOT" \
    --stages synth \
    --jobs "$JOBS" \
    --modules "$HLS_MODULES"
  ok "HLS synth done"
fi

# ════════════════════════════════════════════════════════════════════════════
# 9. Run all verification flows
# ════════════════════════════════════════════════════════════════════════════

# Point the csim backend at the Vitis HLS csim.exe binaries
export CSIM_TB_TB_HIT_DECODER="$HLS_BUILD_ROOT/hit_decoder/solution1/csim/build/csim.exe"
export CSIM_TB_TB_HIT_COLLECTOR="$HLS_BUILD_ROOT/hit_collector/solution1/csim/build/csim.exe"
export CSIM_TB_TB_TRIGGER_LOGIC="$HLS_BUILD_ROOT/trigger_logic/solution1/csim/build/csim.exe"
export CSIM_TB_TB_TRIGGER_OUTPUT="$HLS_BUILD_ROOT/trigger_output/solution1/csim/build/csim.exe"

FLOWS=(
  # HLS C-sim flows (no xsim / Vivado required)
  "hit_decoder_csim"
  "hit_collector_csim"
  "trigger_logic_csim"
  "trigger_output_csim"
  # Single-module RTL xsim flows (requires Vivado + HLS synth output)
  "hit_decoder_xsim"
  "hit_collector_xsim"
  "trigger_logic_xsim"
  "trigger_output_xsim"
  # Full-chip integration (requires algo_top + all HLS synth)
  "trigger_pipeline_xsim"
)

FAIL_COUNT=0
for flow in "${FLOWS[@]}"; do
  step "arc verify run — $flow"
  flow_yml="$PLUGIN_ROOT/verify/${flow}/verify.flow.yml"
  if arc verify run "$flow_yml" --plugin trigger_demo --consumer-root "$(pwd)"; then
    ok "$flow PASSED"
  else
    echo "${RED}✘  $flow FAILED${RESET}" >&2
    FAIL_COUNT=$(( FAIL_COUNT + 1 ))
  fi
done

# ════════════════════════════════════════════════════════════════════════════
# Summary
# ════════════════════════════════════════════════════════════════════════════
echo
if [[ $FAIL_COUNT -eq 0 ]]; then
  echo "${BOLD}${GREEN}All ${#FLOWS[@]} flows passed.${RESET}"
else
  echo "${BOLD}${RED}${FAIL_COUNT}/${#FLOWS[@]} flow(s) FAILED.${RESET}" >&2
  exit 1
fi
