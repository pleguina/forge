#!/usr/bin/env bash
# run_vision_pipeline_demo.sh — Full vision_pipeline_demo pipeline from zero
# (release-plan Phase 10, slice 10.7D: "clean-user workflow covering all
# real flows", superseding slice 10.1's quickstart-only version).
#
# Runs every stage in order, across EVERY real design/flow this plugin has
# built through slice 10.7C (quickstart, pixel-result, tile-statistics,
# packetizer + its invalid-FIFO-depth negative fixture, full-functional,
# platform wrapper, CDC — plus the invalid_direct_bus_cdc.yml topology-only
# negative fixture):
#   1. Clean stale build artifacts
#   2. Validate design.yml + modules.yml for every real design
#   3. forge hls run csim/synth  -> pixel_normalizer csim (its only C-sim TB);
#                                    all 3 HLS modules synth (every xsim flow
#                                    below needs one or more of them); then
#                                    the invalid_direct_bus_cdc.yml negative
#                                    fixture (gen-top --strict must hard-fail
#                                    with ATG023/ATG024 — no RTL generated;
#                                    plain `validate` only warns, exit 0)
#   4. forge topgen gen-top       -> gen-top/design_vision_pipeline_*/
#   5. forge verify generate      -> regenerate flow YAML / SV / TCL files
#   6. gen_stimulus*.py           -> stimulus_current.svh per flow (golden-
#                                    model-driven where a golden model applies)
#   7. forge verify doctor        -> readiness health-check
#   8. forge verify run           -> all 9 verification flows (the
#                                    invalid_fifo_depth_xsim flow is EXPECTED
#                                    to fail — that's its own evidence, spec
#                                    §17.5)
#
# Usage (from arc-framework repo root):
#   ./run_vision_pipeline_demo.sh              # full run, all designs/flows
#   ./run_vision_pipeline_demo.sh --skip-hls   # skip HLS gen-tcl + csim + synth
#   ./run_vision_pipeline_demo.sh --no-clean   # skip artifact cleanup
#
# Prerequisites:
#   - forge CLI installed:  pip install -e forge/
#   - Vitis HLS on PATH  (for stage 3; skip with --skip-hls)
#   - Vivado xsim on PATH (for stage 8's 8 full-chip flows)
#
set -euo pipefail

# ── Defaults ────────────────────────────────────────────────────────────────
SKIP_HLS=0
NO_CLEAN=0
JOBS=4
HLS_BUILD_ROOT="build_hls_vision_pipeline_demo"
PLUGIN_ROOT="plugins/vision_pipeline_demo"
FORGE_ROOT="$PLUGIN_ROOT/forge"
DESIGNS_DIR="$FORGE_ROOT/designs"
MODULES_YML="$FORGE_ROOT/modules.yml"
VERIFY_YML="$FORGE_ROOT/verify/design.verification.yml"
TOOLS_DIR="$FORGE_ROOT/verify/tools"

# ── Argument parsing ─────────────────────────────────────────────────────────
for arg in "$@"; do
  case "$arg" in
    --skip-hls)  SKIP_HLS=1 ;;
    --no-clean)  NO_CLEAN=1 ;;
    --jobs=*)    JOBS="${arg#*=}" ;;
    -h|--help)
      sed -n '2,35p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

# ── Helpers ──────────────────────────────────────────────────────────────────
BOLD=$'\e[1m'; RESET=$'\e[0m'; GREEN=$'\e[32m'; RED=$'\e[31m'; CYAN=$'\e[36m'; YELLOW=$'\e[33m'
step() { echo; echo "${BOLD}${CYAN}== $* ==${RESET}"; }
ok()   { echo "${GREEN}v  $*${RESET}"; }
warn() { echo "${YELLOW}!  $*${RESET}"; }
die()  { echo "${RED}x  $*${RESET}" >&2; exit 1; }

cd "$(dirname "$0")"          # always run from repo root

command -v forge >/dev/null 2>&1 || die "forge not found - run: pip install -e forge/"

# ── Real designs: name : design-yml : gen-top output dir ────────────────────
# (order matches slice implementation order 10.1 -> 10.7B; every dir name
# matches design.verification.yml's own dut_rtl_source for that design's flow)
DESIGNS=(
  "quickstart:design.yml:design_vision_pipeline_quickstart"
  "pixel_result:design_pixel_result.yml:design_vision_pipeline_pixel_result"
  "tile_stats:design_tile_stats.yml:design_vision_pipeline_tile_stats"
  "packetizer:design_packetizer.yml:design_vision_pipeline_packetizer"
  "invalid_fifo_depth:invalid_fifo_depth_packetizer.yml:design_vision_pipeline_invalid_fifo_depth"
  "full_functional:design_full_functional.yml:design_vision_pipeline_full_functional"
  "platform_wrapper:design_platform_wrapper.yml:design_vision_pipeline_platform_wrapper"
  "cdc:design_cdc.yml:design_vision_pipeline_cdc"
)

# ── Real flows: flow-name : gen_stimulus command (empty = none needed) ──────
FLOWS=(
  "pixel_normalizer_csim:"
  "quickstart_pipeline_xsim:gen_stimulus.py --flow quickstart_pipeline_xsim --event-id 0"
  "pixel_result_xsim:gen_stimulus_pixel_result.py --flow pixel_result_xsim"
  "tile_stats_xsim:gen_stimulus_tile_stats.py --flow tile_stats_xsim"
  "packetizer_xsim:gen_stimulus_packetizer.py --flow packetizer_xsim"
  "invalid_fifo_depth_xsim:gen_stimulus_packetizer.py --flow invalid_fifo_depth_xsim"
  "full_functional_xsim:gen_stimulus_full_functional.py --flow full_functional_xsim"
  "platform_wrapper_xsim:gen_stimulus_platform_wrapper.py --flow platform_wrapper_xsim"
  "cdc_xsim:gen_stimulus_cdc.py --flow cdc_xsim"
)

# Flows whose own scoreboard is EXPECTED to report FAIL — that failure is
# itself the fixture's evidence (spec §17.5 "Invalid FIFO fixture"), not a
# broken flow. See design.verification.yml's own header for this flow.
EXPECTED_FAIL_FLOWS=("invalid_fifo_depth_xsim")

is_expected_fail() {
  local flow="$1"
  for f in "${EXPECTED_FAIL_FLOWS[@]}"; do
    [[ "$f" == "$flow" ]] && return 0
  done
  return 1
}

# ════════════════════════════════════════════════════════════════════════════
# 1. Clean stale artifacts
# ════════════════════════════════════════════════════════════════════════════
if [[ $NO_CLEAN -eq 0 ]]; then
  step "Clean stale artifacts"
  for entry in "${DESIGNS[@]}"; do
    IFS=':' read -r _name _yml outdir <<< "$entry"
    rm -rf "gen-top/${outdir}"
  done
  for entry in "${FLOWS[@]}"; do
    IFS=':' read -r flow _cmd <<< "$entry"
    rm -rf "$FORGE_ROOT/verify/${flow}"
  done
  rm -rf "$DESIGNS_DIR/build" "$DESIGNS_DIR/ips"
  if [[ $SKIP_HLS -eq 0 ]]; then
    rm -rf "$HLS_BUILD_ROOT"
  fi
  ok "Clean done"
fi

# ════════════════════════════════════════════════════════════════════════════
# 2. Validate topology — every real design (registry + design.yml). The two
#    negative fixtures are checked separately, where they're REQUIRED to
#    fail (spec §8.5 / §17.5): invalid_direct_bus_cdc.yml after stage 3's
#    HLS build (needs --hls-build-root), invalid_fifo_depth_xsim as part of
#    stage 8's flow runs.
# ════════════════════════════════════════════════════════════════════════════
step "Validate registry"
forge topgen validate-registry "$MODULES_YML"
ok "Registry valid"

step "Validate every real design"
for entry in "${DESIGNS[@]}"; do
  IFS=':' read -r name yml _outdir <<< "$entry"
  forge topgen validate "$DESIGNS_DIR/$yml"
  ok "design ($name): $yml valid"
done

# ════════════════════════════════════════════════════════════════════════════
# 3. HLS build (csim + synth) — must precede gen-top, which needs real
#    synthesized RTL to scan for each HLS module's port metadata.
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
  # pixel_normalizer is the only module with a standalone C-sim testbench
  # (CMakeLists.txt add_csim_tb) — sobel_hls/tile_stats_hls are only
  # exercised through full-chip xsim flows below.
  forge hls run \
    --registry "$MODULES_YML" \
    --hls-build-root "$HLS_BUILD_ROOT" \
    --stages csim \
    --jobs "$JOBS" \
    --modules pixel_normalizer
  ok "HLS csim done"

  step "forge hls run - synth (${JOBS} parallel job(s))"
  # All 3 HLS modules — every real xsim flow below needs one or more of
  # them synthesized to real Verilog before gen-top can scan its ports.
  forge hls run \
    --registry "$MODULES_YML" \
    --hls-build-root "$HLS_BUILD_ROOT" \
    --stages synth \
    --jobs "$JOBS" \
    --modules pixel_normalizer,sobel_hls,tile_stats_hls
  ok "HLS synth done"
fi

step "Negative fixture: invalid_direct_bus_cdc.yml (expected to FAIL gen-top --strict)"
# forge topgen validate (non-strict) only WARNS on ATG023/ATG024 (exit 0) —
# the real hard-failure proof is `gen-top --strict`, confirmed via
# preflight.md's 10.4 completion evidence: no RTL is generated.
if forge topgen gen-top "$DESIGNS_DIR/invalid_direct_bus_cdc.yml" \
    --mode verilog \
    --consumer-root . \
    --contracts-from "$MODULES_YML" \
    --hls-build-root "$HLS_BUILD_ROOT" \
    --output "/tmp/vpd_invalid_direct_bus_cdc.v" \
    --strict >/tmp/vpd_invalid_cdc_gen_top.log 2>&1; then
  rm -f /tmp/vpd_invalid_direct_bus_cdc.v
  die "invalid_direct_bus_cdc.yml unexpectedly PASSED gen-top --strict (should reject the undeclared CDC crossing with ATG023/ATG024)"
else
  rm -f /tmp/vpd_invalid_direct_bus_cdc.v
  ok "invalid_direct_bus_cdc.yml correctly rejected by gen-top --strict (undeclared CDC crossing, ATG023/ATG024)"
fi

# ════════════════════════════════════════════════════════════════════════════
# 4. Generate algo_top — every real design (including the invalid-FIFO-depth
#    fixture, which IS gen-top-able; it's the simulated burst that fails).
# ════════════════════════════════════════════════════════════════════════════
for entry in "${DESIGNS[@]}"; do
  IFS=':' read -r name yml outdir <<< "$entry"
  step "forge topgen gen-top ($name) -> gen-top/${outdir}/algo_top.v"
  forge topgen gen-top "$DESIGNS_DIR/$yml" \
    --mode verilog \
    --consumer-root . \
    --contracts-from "$MODULES_YML" \
    --hls-build-root "$HLS_BUILD_ROOT" \
    --output "gen-top/${outdir}/algo_top.v"
  ok "algo_top generated ($name)"
done

# ════════════════════════════════════════════════════════════════════════════
# 5. Regenerate verification flow files (all flows, one call)
# ════════════════════════════════════════════════════════════════════════════
step "forge verify generate"
forge verify generate "$VERIFY_YML"
ok "Verify flow files regenerated"

# ════════════════════════════════════════════════════════════════════════════
# 6. Generate stimulus files (golden-model-driven where applicable)
# ════════════════════════════════════════════════════════════════════════════
for entry in "${FLOWS[@]}"; do
  IFS=':' read -r flow cmd <<< "$entry"
  [[ -z "$cmd" ]] && continue   # csim flows drive their own C++ TB directly
  step "gen_stimulus - $flow"
  # shellcheck disable=SC2086
  set -- $cmd
  script="$1"; shift
  python3 "$TOOLS_DIR/$script" "$@"
  ok "Stimulus generated ($flow)"
done

# ════════════════════════════════════════════════════════════════════════════
# 7. Doctor check (all flows, one call)
# ════════════════════════════════════════════════════════════════════════════
step "forge verify doctor"
forge verify doctor "$VERIFY_YML"
ok "Doctor passed"

# ════════════════════════════════════════════════════════════════════════════
# 8. Run all verification flows
# ════════════════════════════════════════════════════════════════════════════

# Point the csim backend at the Vitis HLS csim.exe binary.
export CSIM_TB_TB_PIXEL_NORMALIZER="$HLS_BUILD_ROOT/pixel_normalizer/solution1/csim/build/csim.exe"

FAIL_COUNT=0
RUN_COUNT=0
for entry in "${FLOWS[@]}"; do
  IFS=':' read -r flow _cmd <<< "$entry"
  RUN_COUNT=$(( RUN_COUNT + 1 ))
  step "forge verify run - $flow"
  flow_yml="$FORGE_ROOT/verify/${flow}/verify.flow.yml"
  if forge verify run "$flow_yml" --plugin vision_pipeline_demo --consumer-root "$(pwd)"; then
    if is_expected_fail "$flow"; then
      echo "${RED}x  $flow PASSED but was expected to FAIL (invalid fixture regression)${RESET}" >&2
      FAIL_COUNT=$(( FAIL_COUNT + 1 ))
    else
      ok "$flow PASSED"
    fi
  else
    if is_expected_fail "$flow"; then
      ok "$flow FAILED as expected (negative fixture evidence)"
    else
      echo "${RED}x  $flow FAILED${RESET}" >&2
      FAIL_COUNT=$(( FAIL_COUNT + 1 ))
    fi
  fi
done

# ════════════════════════════════════════════════════════════════════════════
# Summary
# ════════════════════════════════════════════════════════════════════════════
echo
if [[ $FAIL_COUNT -eq 0 ]]; then
  echo "${BOLD}${GREEN}All ${RUN_COUNT} flows behaved as expected (1 negative fixture correctly failed).${RESET}"
else
  echo "${BOLD}${RED}${FAIL_COUNT}/${RUN_COUNT} flow(s) behaved unexpectedly.${RESET}" >&2
  exit 1
fi
