#!/usr/bin/env bash
# ci/import_direction_check.sh — cheap dependency-direction linter.
#
# The audit's revision of the original "split into 5 packages
# (forge-model/sdk/backends/analysis/cli)" recommendation (§19): the
# dependency direction that split was meant to enforce is *already* clean
# in the current forge/{core,topgen,hls,verify,analyze,framework,ir}
# layout — a cheap CI grep achieves the same goal at a fraction of the
# cost of a physical package split, which is deferred (see
# docs/development/release-readiness.md) unless repo/team growth later
# justifies it.
#
# Rules enforced (all read-only greps, no AST — same tradeoff as
# ci/agnosticism_check.sh: a real, if necessarily imprecise, regression
# guard, not a proof):
#
#   1. Nothing outside forge/core/cli/ may import forge.core.cli (the CLI
#      layer is a leaf — nothing else should depend on it), with one
#      deliberate exception: forge.core.cli.envelope (the shared
#      CommandEnvelope output shape, release-plan Phase 6 §6.0) is itself
#      CLI-layer shared code, importable from forge/verify/__main__.py — a
#      separate CLI entry point that predates and does not import
#      forge/core/cli/_shared.py — without that being a layering violation.
#   2. forge/topgen/ and forge/verify/ must not cross-import each other
#      (they're independent subsystems composed by the CLI layer, not by
#      each other).
#   3. forge/analyze/ must not import forge.core.cli.
#   4. forge/ir/ (the canonical IR — see forge/ir/model.py) must not import
#      forge.core.cli, forge.topgen.generators, or forge.verify — the IR is
#      a read-only consumer of topology/contract/IP-metadata loaders only
#      (see forge/ir/build.py's own module docstring), never of CLI
#      rendering or generator/verify logic.
#
# forge/tests/ is excluded from all rules — test modules legitimately
# drive the real CLI entry point and cross-subsystem behavior end to end.
#
# Usage:
#   bash ci/import_direction_check.sh
#
# Exit codes:
#   0 — no disallowed cross-imports found
#   1 — one or more violations found (printed with file:line)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

violations=0

# Portable in-place-free "grep and filter" that also strips the file's own
# package directory from consideration where relevant (handled per-rule
# below by choosing distinct SCAN_DIRS instead of excluding matches).
_check() {
    local description="$1"
    local pattern="$2"
    shift 2
    local scan_dirs=("$@")

    local hits
    hits="$(grep -rnE "$pattern" --include='*.py' "${scan_dirs[@]}" 2>/dev/null | grep -v '/tests/' || true)"
    if [[ -n "$hits" ]]; then
        echo "❌ $description"
        echo "$hits" | sed 's/^/    /'
        violations=$((violations + 1))
    else
        echo "✅ $description"
    fi
}

# Note on patterns: source files use a mix of absolute (`forge.core.cli`)
# and relative (`..core.cli`, `.core.cli`) imports, so patterns match on
# the bare dotted module suffix (`core\.cli`, `verify`, `topgen`) preceded
# by any run of dots/word-characters after from/import, rather than
# requiring an absolute `forge.` prefix.

echo "--- Rule 1: nothing outside forge/core/cli/ imports forge.core.cli ---"
_check "no cross-import of forge.core.cli outside forge/core/cli/" \
    '(from|import)[[:space:]]+[.[:alnum:]]*core\.cli\b' \
    forge/topgen forge/hls forge/analyze forge/framework forge/ir

# forge/verify is checked separately: forge.core.cli.envelope is a
# deliberate, documented exception (see comment above) — every other
# forge.core.cli import from forge/verify/ is still a violation.
verify_hits="$(grep -rnE '(from|import)[[:space:]]+[.[:alnum:]]*core\.cli\b' \
    --include='*.py' forge/verify 2>/dev/null \
    | grep -v '/tests/' | grep -v 'core\.cli\.envelope\b' || true)"
if [[ -n "$verify_hits" ]]; then
    echo "❌ no cross-import of forge.core.cli outside forge/core/cli/ (forge/verify)"
    echo "$verify_hits" | sed 's/^/    /'
    violations=$((violations + 1))
else
    echo "✅ no cross-import of forge.core.cli outside forge/core/cli/ (forge/verify, envelope exempted)"
fi

echo "--- Rule 2: forge/topgen and forge/verify do not cross-import ---"
_check "forge/topgen must not import forge.verify" \
    '(from|import)[[:space:]]+[.[:alnum:]]*verify\b' \
    forge/topgen
_check "forge/verify must not import forge.topgen" \
    '(from|import)[[:space:]]+[.[:alnum:]]*topgen\b' \
    forge/verify

echo "--- Rule 3: forge/analyze does not import forge.core.cli ---"
_check "forge/analyze must not import forge.core.cli" \
    '(from|import)[[:space:]]+[.[:alnum:]]*core\.cli\b' \
    forge/analyze

echo "--- Rule 4: forge/ir stays read-only (no CLI/generator/verify deps) ---"
_check "forge/ir must not import forge.core.cli" \
    '(from|import)[[:space:]]+[.[:alnum:]]*core\.cli\b' \
    forge/ir
_check "forge/ir must not import forge.topgen.generators (no generator mutation)" \
    '(from|import)[[:space:]]+[.[:alnum:]]*topgen\.generators\b' \
    forge/ir
_check "forge/ir must not import forge.verify" \
    '(from|import)[[:space:]]+[.[:alnum:]]*verify\b' \
    forge/ir

if [[ "$violations" -gt 0 ]]; then
    echo ""
    echo "❌ $violations dependency-direction violation(s) found."
    exit 1
fi

echo ""
echo "✅ No dependency-direction violations found."
