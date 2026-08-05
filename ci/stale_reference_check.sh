#!/usr/bin/env bash
# ci/stale_reference_check.sh — Guard against dead command/path references
# creeping back into forge/, plugins/, docs/, and root scripts.
#
# This branch spent a long time finding and fixing the same class of bug
# over and over: a CLI help string, error hint, or convenience script
# quietly referencing a command or path that stopped existing at some
# earlier refactor (fw_verify, bare `topgen`, framework/verify/python, the
# pre-forge/-capsule plugin layout) — invisible until someone actually ran
# the code instead of just reading it. This script encodes that class of
# regression as an enforceable check instead of relying on it being
# rediscovered by hand again.
#
# MIGRATION.md and CHANGELOG.md are intentionally excluded — their entire
# purpose is documenting the old, dead names for historical/migration
# reference, not using them live.
#
# Usage:
#   bash ci/stale_reference_check.sh
#
# Exit codes:
#   0 — no dead references found
#   1 — one or more dead references found (printed with file:line)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SCAN_PATHS=(forge plugins docs ci run_trigger_demo.sh run_vision_pipeline_demo.sh validate_framework.sh README.md CONTRIBUTING.md)
# Only scan paths that currently exist (validate_framework.sh was retired
# but a future revert or copy-paste could reintroduce it).
EXISTING_PATHS=()
for p in "${SCAN_PATHS[@]}"; do
    [[ -e "$p" ]] && EXISTING_PATHS+=("$p")
done

FAILED=0

check() {
    local description="$1"
    local pattern="$2"
    local hits
    hits="$(grep -rnP "$pattern" "${EXISTING_PATHS[@]}" \
        --include="*.py" --include="*.sh" --include="*.yml" --include="*.yaml" --include="*.md" \
        2>/dev/null \
        | grep -v /__pycache__/ \
        | grep -v /third_party/ \
        | grep -v '^ci/stale_reference_check.sh:' \
        || true)"
    if [[ -n "$hits" ]]; then
        echo "FAIL: $description" >&2
        echo "$hits" >&2
        echo "" >&2
        FAILED=1
    fi
}

# The framework was fw_verify before it was arc.verify, before it was
# forge.verify. Neither the package nor the CLI command has been named
# that since before this branch existed.
check "dead 'fw_verify' package/command reference" '\bfw_verify\b'

# framework/verify/python hasn't existed since before the arc/ flatten;
# it was the old fw_verify package's source location.
check "dead 'framework/verify/python' path reference" 'framework/verify/python'

# 'topgen <subcommand>' as a bare, unprefixed CLI invocation predates the
# CLI unification into a single 'forge' entry point. Uses a negative
# lookbehind rather than a blanket word search so it doesn't flag the
# correct 'forge topgen ...' form.
check "bare (unprefixed) 'topgen <subcommand>' invocation" \
    '(?<!forge )(?<!forge --debug )\btopgen (validate|gen-top|ip-summary|match-ports|unpack-ips|clean|lint|validate-registry)\b'

if [[ "$FAILED" -eq 1 ]]; then
    echo "One or more dead command/path references found above." >&2
    echo "If this is a genuine new pattern this check doesn't know about yet," >&2
    echo "fix the reference. If it's a false positive, narrow the check in" >&2
    echo "this script rather than removing it." >&2
    exit 1
fi

echo "PASS: no dead command/path references found."
