#!/usr/bin/env bash
# ci/vision_pipeline_demo_reference_check.sh — Guard against internal
# phase/slice/spec-section planning references creeping back into
# vision_pipeline_demo's production files and its two public tutorial
# pages.
#
# docs/plan/FORGE_phase10_progressive_tutorial_productization_plan.md §5
# requires production/public code to explain itself in stable, semantic
# terms — not by citing the temporary planning document section that
# caused it to be written. A one-time cleanup pass
# (docs/internal/phase10/tutorial_productization_audit.md §1.13) removed
# every such reference from this scope; this script keeps them from
# coming back.
#
# Scope is intentionally narrow: FORGE core (forge/) still carries this
# same citation style throughout its own docstrings (a separate,
# pre-existing, much larger convention, ~275 files) and is explicitly out
# of scope for this productization pass — see the audit's own §1.13
# recommendation. Widening this check to forge/ would fail immediately on
# unrelated, already-shipped content.
#
# Usage:
#   bash ci/vision_pipeline_demo_reference_check.sh
#
# Exit codes:
#   0 — no internal-plan references found in scope
#   1 — one or more internal-plan references found (printed with file:line)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Scope: this plugin's production files, its two public tutorial pages,
# and its root-level runner script. Explicitly excludes docs/internal/**
# (where this history belongs per plan §5.4) and any generated/gitignored
# output.
SCAN_PATHS=(
    plugins/vision_pipeline_demo
    docs/tutorials/vision-pipeline
    docs/tutorials/vision-pipeline-quickstart.md
    docs/tutorials/vision-pipeline-full-design.md
    run_vision_pipeline_demo.sh
)
EXISTING_PATHS=()
for p in "${SCAN_PATHS[@]}"; do
    [[ -e "$p" ]] && EXISTING_PATHS+=("$p")
done

# Matches "Phase 10", "slice 10.7B", "spec §9.1", a bare "§9.1", a bare
# "10.7A"/"10.5" release-plan sub-slice number even without the word
# "slice" immediately before it (found missing real instances of exactly
# this shape during the T3 documentation pass — e.g. "not 10.7A's
# 16x16"), the literal string "preflight", "release-plan", or
# "FORGE_release_plan".
PATTERN='(Phase[[:space:]]+[0-9]+|phase[[:space:]]+[0-9]+|Slice[[:space:]]+[0-9]+|slice[[:space:]-][0-9]+|\b10\.[0-9]+[A-D]?\b|spec[[:space:]]*§|§[0-9]+|preflight|FORGE_release_plan|release-plan)'

hits="$(grep -rnE "$PATTERN" "${EXISTING_PATHS[@]}" \
    2>/dev/null \
    | grep -v /__pycache__/ \
    | grep -v /xsim_work/ \
    | grep -v '/\(quickstart_pipeline\|pixel_result\|tile_stats\|packetizer\|invalid_fifo_depth\|full_functional\|platform_wrapper\|cdc\)_xsim/tb_algo_top\.sv:' \
    | grep -v '/\(quickstart_pipeline\|pixel_result\|tile_stats\|packetizer\|invalid_fifo_depth\|full_functional\|platform_wrapper\|cdc\)_xsim/stimulus_current\.svh:' \
    || true)"
# The two exclusions above are FORGE-generated files ("DO NOT EDIT —
# regenerate with ..."): tb_algo_top.sv's one flagged line originates
# from forge/verify/gen_sim.py (FORGE core, out of scope for this pass);
# stimulus_current.svh's flagged line originates from this plugin's own
# gen_stimulus_*.py, already cleaned — the checked-in file just predates
# its last regeneration and self-heals on the next `forge verify
# generate` run. Hand-editing generated output here would only be
# overwritten.

if [[ -n "$hits" ]]; then
    echo "FAIL: internal phase/slice/spec-section reference found in vision_pipeline_demo's production files or public tutorial pages" >&2
    echo "$hits" >&2
    echo "" >&2
    echo "Explain the code/doc in stable, semantic terms instead of citing the" >&2
    echo "planning-document section that caused it to be written — see" >&2
    echo "docs/plan/FORGE_phase10_progressive_tutorial_productization_plan.md §5.3" >&2
    echo "for the required replacement pattern. If a decision needs long-term" >&2
    echo "traceability, add or extend a stable ADR under docs/development/adr/" >&2
    echo "instead of citing a phase/slice number." >&2
    exit 1
fi

echo "PASS: no internal phase/slice/spec-section references found in vision_pipeline_demo's production files or public tutorial pages."
