#!/usr/bin/env bash
# run_omtf_xsim.sh — Verify the OMTF firmware plugin (submodule) with xsim.
#
# The OMTF plugin lives at plugins/omtf_firmware/ (git submodule → ../omtf-firmware).
# Its ARC capsule is plugins/omtf_firmware/arc/ (standard framework layout).
#
# Pipeline stages:
#   1. Ensure omtf_firmware submodule is initialised
#   2. Source plugin env  (sets VERIFY_CONSUMER_ROOT, PYTHONPATH, etc.)
#   3. Validate design registry (modules.yml)
#   4. arc hls run synth  → HLS RTL for all HLS modules (skip with --skip-hls)
#   5. arc topgen gen-top  → out/algorithm/algo_top.v  (skip with --skip-gen-top)
#   6. arc verify generate → verify.flow.yml / tb_*.sv / wave.tcl per flow
#   7. gen_stimulus.py     → stimulus_current.svh per xsim flow
#   8. arc verify doctor   → readiness health-check
#   9. arc verify run      → xsim simulation for selected flow(s)
#
# Usage (from arc-framework repo root):
#   ./run_omtf_xsim.sh                          # full_chip_algo_top_xsim, event 55
#   ./run_omtf_xsim.sh --flow dt_event_xsim     # single named flow
#   ./run_omtf_xsim.sh --all-flows              # all xsim flows
#   ./run_omtf_xsim.sh --event-id 1             # override event id
#   ./run_omtf_xsim.sh --skip-hls               # skip HLS synthesis (reuse existing build_hls/)
#   ./run_omtf_xsim.sh --skip-gen-top           # reuse existing out/algorithm/
#   ./run_omtf_xsim.sh --dry-run                # doctor + generate only (no xsim)
#
# Prerequisites:
#   - arc CLI installed:  pip install -e arc/
#   - Vivado on PATH:     source $XILINX_VIVADO/settings64.sh
#   - Vitis HLS on PATH:  required only for HLS module flows that need synth output
#
set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────
DEFAULT_FLOW="full_chip_algo_top_xsim"
SELECTED_FLOWS=()
ALL_FLOWS=0
DRY_RUN=0
SKIP_HLS=0
SKIP_GEN_TOP=0
JOBS=4
EVENT_ID_ARGS=()

# HLS modules in the OMTF registry that need synthesis before gen-top / xsim
HLS_MODULES=(
  bx_timing dt_interface csc_interface
  concentrator region_filter priority_arbiter
  layermem phi_extrapolation phi_dist_processor
  pdf_lookup best_candidate
  nn_interface regression_nn
)

# All xsim flows declared in design.verification.yml
XSIM_FLOWS=(
  layermem_event_xsim
  dt_event_xsim
  csc_event_xsim
  phi_dist_event_xsim
  pdf_lookup_event_xsim
  best_candidate_event_xsim
  layermem_chain_xsim
  concentrator_chain_xsim
  phi_extrap_chain_xsim
  arbiter_chain_xsim
  best_candidate_chain_xsim
  best_candidate_chain_xsim_v2
  full_chip_algo_top_xsim
)

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --flow)         SELECTED_FLOWS+=("$2"); shift 2 ;;
    --all-flows)    ALL_FLOWS=1; shift ;;
    --event-id)     EVENT_ID_ARGS=(--event-id "$2"); shift 2 ;;
    --skip-hls)     SKIP_HLS=1; shift ;;
    --skip-gen-top) SKIP_GEN_TOP=1; shift ;;
    --dry-run)      DRY_RUN=1; shift ;;
    --jobs=*)       JOBS="${1#*=}"; shift ;;
    -h|--help)
      sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ $ALL_FLOWS -eq 1 ]]; then
  SELECTED_FLOWS=("${XSIM_FLOWS[@]}")
elif [[ ${#SELECTED_FLOWS[@]} -eq 0 ]]; then
  SELECTED_FLOWS=("$DEFAULT_FLOW")
fi

# ── Paths ─────────────────────────────────────────────────────────────────────
cd "$(dirname "$0")"          # always run from arc-framework root

PLUGIN_SUBMODULE="plugins/omtf_firmware"
PLUGIN_CAPSULE="${PLUGIN_SUBMODULE}/arc"      # ARC capsule inside submodule
VERIFY_YML="${PLUGIN_CAPSULE}/verify/design.verification.yml"
PLUGIN_ENV="${PLUGIN_CAPSULE}/verify/tools/omtf_verify_env.sh"
GEN_STIMULUS="${PLUGIN_CAPSULE}/verify/tools/gen_stimulus.py"
MODULES_YML="${PLUGIN_CAPSULE}/modules.yml"
DESIGN_YML="${PLUGIN_CAPSULE}/designs/design.yml"
# GEN_TOP_OUT is derived from OMTF_CONSUMER_ROOT after sourcing the env script

# ── Helpers ───────────────────────────────────────────────────────────────────
BOLD=$'\e[1m'; RESET=$'\e[0m'; GREEN=$'\e[32m'; RED=$'\e[31m'; CYAN=$'\e[36m'
step() { echo; echo "${BOLD}${CYAN}══ $* ══${RESET}"; }
ok()   { echo "${GREEN}✔  $*${RESET}"; }
die()  { echo "${RED}✘  $*${RESET}" >&2; exit 1; }

# ── Guard: arc must be installed ──────────────────────────────────────────────
command -v arc >/dev/null 2>&1 || die "arc not found — run: pip install -e arc/"

# ═════════════════════════════════════════════════════════════════════════════
# 1. Ensure submodule is checked out
# ═════════════════════════════════════════════════════════════════════════════
step "Ensure omtf_firmware submodule is initialised"
if [[ ! -f "${VERIFY_YML}" ]]; then
  echo "  Submodule not yet checked out — running git submodule update ..."
  git submodule update --init "${PLUGIN_SUBMODULE}"
fi
[[ -f "${VERIFY_YML}" ]] || die "Contract not found after submodule init: ${VERIFY_YML}"
ok "Submodule ready ($(git -C "${PLUGIN_SUBMODULE}" rev-parse --short HEAD 2>/dev/null || echo 'detached'))"

# ═════════════════════════════════════════════════════════════════════════════
# 2. Source plugin env (sets VERIFY_CONSUMER_ROOT, PYTHONPATH, etc.)
# ═════════════════════════════════════════════════════════════════════════════
step "Source plugin env: ${PLUGIN_ENV}"
[[ -f "${PLUGIN_ENV}" ]] || die "Plugin env script not found: ${PLUGIN_ENV}"
# OMTF_CONSUMER_ROOT is auto-detected from the env script's own path (one level
# above the arc/ capsule → plugins/omtf_firmware/).  Pre-set only for portability.
export OMTF_CONSUMER_ROOT
OMTF_CONSUMER_ROOT="$(cd "${PLUGIN_SUBMODULE}" && pwd)"
source "${PLUGIN_ENV}"
# Derived paths that require OMTF_CONSUMER_ROOT to be resolved
GEN_TOP_OUT="${OMTF_CONSUMER_ROOT}/out/algorithm/algo_top.v"
ok "Plugin env sourced  (VERIFY_CONSUMER_ROOT=${VERIFY_CONSUMER_ROOT:-<not set>})"

# ═════════════════════════════════════════════════════════════════════════════
# 3. Vivado guard (skip only in dry-run mode)
# ═════════════════════════════════════════════════════════════════════════════
if [[ $DRY_RUN -eq 0 ]]; then
  step "Check Vivado / xsim tools"
  command -v xvlog >/dev/null 2>&1 || \
    die "xvlog not found — source Vivado settings64.sh first:\n  source \$XILINX_VIVADO/settings64.sh"
  ok "Vivado xsim available ($(xvlog --version 2>&1 | head -1))"
fi

# ═════════════════════════════════════════════════════════════════════════════
# 4. Validate design registry
# ═════════════════════════════════════════════════════════════════════════════
step "arc topgen validate-registry"
arc topgen validate-registry "${MODULES_YML}"
ok "Registry validation passed"

# ═════════════════════════════════════════════════════════════════════════════
# 4. HLS synthesis  (needed by gen-top and all single-module / chain xsim flows)
# ═════════════════════════════════════════════════════════════════════════════
if [[ $SKIP_HLS -eq 1 ]]; then
  echo "(--skip-hls: reusing existing ${OMTF_VERIFY_HLS_BUILD_ROOT}/)"
else
  step "arc hls gen-tcl — generate Vitis HLS TCL scripts (--flow export)"
  command -v vitis_hls >/dev/null 2>&1 || \
    die "vitis_hls not found — source Vitis HLS settings64.sh or pass --skip-hls"
  arc hls gen-tcl \
    --hls-config "${OMTF_CONSUMER_ROOT}/arc/modules.yml" \
    --output-dir "${OMTF_VERIFY_HLS_BUILD_ROOT}" \
    --flow export
  ok "HLS TCL scripts generated"

  step "arc hls run — synth,export (${JOBS} parallel job(s))"
  arc hls run \
    --registry "${OMTF_CONSUMER_ROOT}/arc/modules.yml" \
    --hls-build-root "${OMTF_VERIFY_HLS_BUILD_ROOT}" \
    --stages synth,export \
    --jobs "${JOBS}" \
    --modules "${HLS_MODULES[*]}"
  ok "HLS synthesis + export done"
fi

# ═════════════════════════════════════════════════════════════════════════════
# 5. arc topgen gen-top  (needed for full_chip_algo_top_xsim only)
# ═════════════════════════════════════════════════════════════════════════════
# Determine whether any selected flow requires algo_top.v
_NEED_GEN_TOP=0
for _f in "${SELECTED_FLOWS[@]}"; do
  case "${_f}" in full_chip_*|*_chain_*) _NEED_GEN_TOP=1; break ;; esac
done

if [[ $_NEED_GEN_TOP -eq 0 ]]; then
  echo "(gen-top: skipped — no selected flow requires algo_top)"
elif [[ $SKIP_GEN_TOP -eq 1 ]]; then
  echo "(--skip-gen-top: reusing existing ${GEN_TOP_OUT})"
  [[ -f "${GEN_TOP_OUT}" ]] || \
    die "algo_top.v not found at ${GEN_TOP_OUT} — remove --skip-gen-top to regenerate"
else
  step "arc topgen gen-top → ${GEN_TOP_OUT}"
  arc topgen gen-top "${DESIGN_YML}" \
    --mode verilog \
    --consumer-root "${OMTF_CONSUMER_ROOT}" \
    --contracts-from "${OMTF_CONSUMER_ROOT}/arc/modules.yml" \
    --hls-build-root "${OMTF_VERIFY_HLS_BUILD_ROOT}" \
    --output "${GEN_TOP_OUT}"
  ok "algo_top generated"
fi

# ═════════════════════════════════════════════════════════════════════════════
# 6. Generate verification flow artifacts (verify.flow.yml, tb_*.sv, wave.tcl)
# ═════════════════════════════════════════════════════════════════════════════
step "arc verify generate"
arc verify generate "${VERIFY_YML}"
ok "Flow artifacts regenerated"

# ═════════════════════════════════════════════════════════════════════════════
# 7. Generate stimulus SVH files
# ═════════════════════════════════════════════════════════════════════════════
step "gen_stimulus.py — stimulus_current.svh for each xsim flow"
python3 "${GEN_STIMULUS}"
ok "Stimulus files generated"

# ═════════════════════════════════════════════════════════════════════════════
# 8. Doctor check
# ═════════════════════════════════════════════════════════════════════════════
step "arc verify doctor"
arc verify doctor "${VERIFY_YML}" --strict
ok "Doctor passed"

# ═════════════════════════════════════════════════════════════════════════════
# 9. Run xsim flows
# ═════════════════════════════════════════════════════════════════════════════
if [[ $DRY_RUN -eq 1 ]]; then
  echo
  echo "  (--dry-run: skipping arc verify run)"
  echo "  Flow(s) that would run: ${SELECTED_FLOWS[*]}"
  exit 0
fi

FAIL_COUNT=0
for flow in "${SELECTED_FLOWS[@]}"; do
  step "arc verify run — ${flow}"
  flow_yml="${PLUGIN_CAPSULE}/verify/${flow}/verify.flow.yml"
  if [[ ! -f "${flow_yml}" ]]; then
    echo "${RED}✘  ${flow}: verify.flow.yml not found at ${flow_yml}${RESET}" >&2
    FAIL_COUNT=$(( FAIL_COUNT + 1 ))
    continue
  fi
  if arc verify run "${flow_yml}" \
       --plugin omtf \
       --consumer-root "${OMTF_CONSUMER_ROOT}" \
       "${EVENT_ID_ARGS[@]}"; then
    ok "${flow} PASSED"
  else
    echo "${RED}✘  ${flow} FAILED${RESET}" >&2
    FAIL_COUNT=$(( FAIL_COUNT + 1 ))
  fi
done

# ═════════════════════════════════════════════════════════════════════════════
# Summary
# ═════════════════════════════════════════════════════════════════════════════
echo
if [[ $FAIL_COUNT -eq 0 ]]; then
  echo "${BOLD}${GREEN}All ${#SELECTED_FLOWS[@]} flow(s) passed.${RESET}"
else
  echo "${BOLD}${RED}${FAIL_COUNT}/${#SELECTED_FLOWS[@]} flow(s) FAILED.${RESET}" >&2
  exit 1
fi
