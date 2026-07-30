#!/usr/bin/env bash
# run_vision_pipeline_demo.sh — Full vision_pipeline_demo (quickstart
# tier) pipeline from zero (release-plan Phase 10, slice 10.1).
#
# Runs every stage in order:
#   1. Clean stale build artifacts
#   2. Validate design.yml + modules.yml
#   3. forge topgen gen-top  -> gen-top/design_vision_pipeline_quickstart/
#   4. forge verify generate -> regenerate flow YAML / SV / TCL files
#   5. gen_stimulus.py       -> stimulus_current.svh (golden-model-driven)
#   6. forge verify doctor   -> readiness health-check
#   7. forge hls run csim    -> C-simulation for pixel_normalizer
#   8. forge hls run synth   -> RTL synthesis for pixel_normalizer (needed by xsim)
#   9. forge verify run      -> both flows (csim x 1, full-chip x 1)
#
# Usage (from arc-framework repo root):
#   ./run_vision_pipeline_demo.sh              # full run
#   ./run_vision_pipeline_demo.sh --skip-hls   # skip HLS gen-tcl + csim + synth
#   ./run_vision_pipeline_demo.sh --no-clean   # skip artifact cleanup
#
# Prerequisites:
#   - forge CLI installed:  pip install -e forge/
#   - Vitis HLS on PATH  (for stages 7-8; skip with --skip-hls)
#   - Vivado xsim on PATH (for stage 9's full-chip flow)
#
set -euo pipefail

# ── Defaults ────────────────────────────────────────────────────────────────
SKIP_HLS=0
NO_CLEAN=0
JOBS=4
HLS_BUILD_ROOT="build_hls_vision_pipeline_demo"
GEN_TOP_OUT="gen-top/design_vision_pipeline_quickstart/algo_top.v"
PLUGIN_ROOT="plugins/vision_pipeline_demo"
FORGE_ROOT="$PLUGIN_ROOT/forge"
DESIGN_YML="$FORGE_ROOT/designs/design.yml"
MODULES_YML="$FORGE_ROOT/modules.yml"
VERIFY_YML="$FORGE_ROOT/verify/design.verification.yml"
GEN_STIMULUS="$FORGE_ROOT/verify/tools/gen_stimulus.py"

# ── Argument parsing ─────────────────────────────────────────────────────────
for arg in "$@"; do
  case "$arg" in
    --skip-hls)  SKIP_HLS=1 ;;
    --no-clean)  NO_CLEAN=1 ;;
    --jobs=*)    JOBS="${arg#*=}" ;;
    -h|--help)
      sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

# ── Helpers ──────────────────────────────────────────────────────────────────
BOLD=$'\e[1m'; RESET=$'\e[0m'; GREEN=$'\e[32m'; RED=$'\e[31m'; CYAN=$'\e[36m'
step() { echo; echo "${BOLD}${CYAN}== $* ==${RESET}"; }
ok()   { echo "${GREEN}v  $*${RESET}"; }
die()  { echo "${RED}x  $*${RESET}" >&2; exit 1; }

cd "$(dirname "$0")"          # always run from repo root

command -v forge >/dev/null 2>&1 || die "forge not found - run: pip install -e forge/"

# ════════════════════════════════════════════════════════════════════════════
# 1. Clean stale artifacts
# ════════════════════════════════════════════════════════════════════════════
if [[ $NO_CLEAN -eq 0 ]]; then
  step "Clean stale artifacts"
  rm -rf \
    "gen-top/design_vision_pipeline_quickstart" \
    "$FORGE_ROOT/verify/quickstart_pipeline_xsim" \
    "$FORGE_ROOT/designs/build" \
    "$FORGE_ROOT/designs/ips"
  if [[ $SKIP_HLS -eq 0 ]]; then
    rm -rf "$HLS_BUILD_ROOT"
  fi
  ok "Clean done"
fi

# ════════════════════════════════════════════════════════════════════════════
# 2. Validate topology
# ════════════════════════════════════════════════════════════════════════════
step "Validate design + registry"
forge topgen validate          "$DESIGN_YML"
forge topgen validate-registry "$MODULES_YML"
ok "Validation passed"

# ════════════════════════════════════════════════════════════════════════════
# 3. HLS build (csim + synth) — must precede gen-top, which needs real
#    synthesized RTL to scan for the HLS module's port metadata.
# ════════════════════════════════════════════════════════════════════════════
if [[ $SKIP_HLS -eq 1 ]]; then
  echo "(--skip-hls: skipping HLS TCL generation + csim + synth)"
else
  step "forge hls gen-tcl - generate Vitis HLS TCL scripts"
  forge hls gen-tcl \
    --hls-config "$MODULES_YML" \
    --output-dir "$HLS_BUILD_ROOT"
  ok "HLS TCL scripts generated"

  step "forge hls run - csim (${JOBS} parallel job(s))"
  forge hls run \
    --registry "$MODULES_YML" \
    --hls-build-root "$HLS_BUILD_ROOT" \
    --stages csim \
    --jobs "$JOBS" \
    --modules pixel_normalizer
  ok "HLS csim done"

  step "forge hls run - synth (${JOBS} parallel job(s))"
  forge hls run \
    --registry "$MODULES_YML" \
    --hls-build-root "$HLS_BUILD_ROOT" \
    --stages synth \
    --jobs "$JOBS" \
    --modules pixel_normalizer
  ok "HLS synth done"
fi

# ════════════════════════════════════════════════════════════════════════════
# 4. Generate algo_top
# ════════════════════════════════════════════════════════════════════════════
step "forge topgen gen-top -> $GEN_TOP_OUT"
forge topgen gen-top "$DESIGN_YML" \
  --mode verilog \
  --consumer-root . \
  --contracts-from "$MODULES_YML" \
  --hls-build-root "$HLS_BUILD_ROOT" \
  --output "$GEN_TOP_OUT"
ok "algo_top generated"

# ════════════════════════════════════════════════════════════════════════════
# 5. Regenerate verification flow files
# ════════════════════════════════════════════════════════════════════════════
step "forge verify generate"
forge verify generate "$VERIFY_YML"
ok "Verify flow files regenerated"

# ════════════════════════════════════════════════════════════════════════════
# 6. Generate stimulus SVH file (golden-model-driven, event 0)
# ════════════════════════════════════════════════════════════════════════════
step "gen_stimulus.py - stimulus_current.svh for quickstart_pipeline_xsim"
python3 "$GEN_STIMULUS" --flow quickstart_pipeline_xsim --event-id 0
ok "Stimulus file generated"

# ════════════════════════════════════════════════════════════════════════════
# 7. Doctor check
# ════════════════════════════════════════════════════════════════════════════
step "forge verify doctor"
forge verify doctor "$VERIFY_YML"
ok "Doctor passed"

# ════════════════════════════════════════════════════════════════════════════
# 8. Run all verification flows
# ════════════════════════════════════════════════════════════════════════════

# Point the csim backend at the Vitis HLS csim.exe binary.
export CSIM_TB_TB_PIXEL_NORMALIZER="$HLS_BUILD_ROOT/pixel_normalizer/solution1/csim/build/csim.exe"

FLOWS=(
  "pixel_normalizer_csim"      # HLS C-sim flow (no xsim / Vivado required)
  "quickstart_pipeline_xsim"   # Full-chip integration (requires algo_top + HLS synth)
)

FAIL_COUNT=0
for flow in "${FLOWS[@]}"; do
  step "forge verify run - $flow"
  flow_yml="$FORGE_ROOT/verify/${flow}/verify.flow.yml"
  if forge verify run "$flow_yml" --plugin vision_pipeline_demo --consumer-root "$(pwd)"; then
    ok "$flow PASSED"
  else
    echo "${RED}x  $flow FAILED${RESET}" >&2
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
