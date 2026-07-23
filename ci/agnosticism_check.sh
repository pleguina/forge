#!/usr/bin/env bash
# ci/agnosticism_check.sh — Guard against hardcoded algorithm/detector
# assumptions creeping back into FORGE core.
#
# FORGE core (everything under forge/, excluding forge/tests/ fixtures and
# per-plugin capsules under plugins/) must not hardcode CMS/OMTF-specific
# vocabulary — detector types, trigger role names, accelerator timing
# constants. Those belong in a plugin's own config (design.yml,
# detector_io.yml, policies YAML) or as an explicit, documented default that
# a plugin can override — see forge/framework/__init__.py and
# forge/topgen/config.py's `reference_period_ns` for the pattern.
#
# This is a real (if necessarily imprecise) regression guard, not a proof of
# agnosticism — a determined author can still write something this grep
# can't catch. Its job is to catch the easy, common regressions: a new
# closed enum of detector type literals, a new hardcoded accelerator
# constant, etc.
#
# Usage:
#   bash ci/agnosticism_check.sh
#
# Exit codes:
#   0 — no disallowed hardcoded terms found
#   1 — one or more disallowed terms found (printed with file:line)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Scope: FORGE core packages only. Plugins (plugins/) are expected to use
# domain-specific vocabulary freely — that's the whole point of a plugin.
# forge/tests/ is excluded because its fixtures intentionally use realistic
# example data (DT/CSC/blobfish) to prove the *generic* mechanism handles
# one concrete example correctly, not because the mechanism is hardcoded.
SCAN_DIRS=(forge/core forge/topgen forge/hls forge/verify forge/analyze forge/framework)

# Whole-word, case-sensitive: catches literal detector/role enum values
# like "DT": ... or name_lower in ['dt', 'csc'], without flagging unrelated
# lowercase tokens (e.g. "dt" as a generic time-delta variable name).
BLOCKLIST='\b(DT|CSC|RPC|GMT|BMT|OMTF)\b'

RAW_HITS="$(grep -rnE "$BLOCKLIST" "${SCAN_DIRS[@]}" --include="*.py" --include="*.yaml" --include="*.yml" 2>/dev/null | grep -v /__pycache__/ || true)"

# Allowlist: lines that mention these terms only to disclaim them (explicit
# "no OMTF-specific assumptions" documentation), not to hardcode them.
FILTERED_HITS="$(echo "$RAW_HITS" | grep -vE 'No OMTF|no OMTF' || true)"

if [[ -n "$FILTERED_HITS" ]]; then
    echo "FAIL: disallowed hardcoded detector/algorithm terms found in FORGE core:" >&2
    echo "" >&2
    echo "$FILTERED_HITS" >&2
    echo "" >&2
    echo "FORGE core must stay algorithm-agnostic. If this is a config default a" >&2
    echo "plugin can override (like forge/topgen/config.py's reference_period_ns" >&2
    echo "or forge/framework/io_resolver.py's detector_input_roles parameter)," >&2
    echo "keep the override mechanism and document the default inline. If it's a" >&2
    echo "genuinely generic term this check misidentified, extend the allowlist" >&2
    echo "in this script with a one-line justification." >&2
    exit 1
fi

echo "PASS: no disallowed hardcoded detector/algorithm terms found in FORGE core."
