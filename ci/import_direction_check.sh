#!/usr/bin/env bash
# ci/import_direction_check.sh — cheap dependency-direction linter.
#
# A physical package split (forge-model/sdk/backends/analysis/cli) was
# considered and rejected: the dependency direction it would enforce is
# *already* clean in the current
# forge/{core,contracts,generation,hls,verification,analysis,integration,ir}
# layout — a cheap CI grep achieves the same goal at a fraction of the
# cost of a physical package split, which stays deferred unless
# repo/team growth later justifies it.
#
# Rules enforced (all read-only greps, no AST — same tradeoff as
# ci/agnosticism_check.sh: a real, if necessarily imprecise, regression
# guard, not a proof):
#
#   1. Nothing outside forge/core/cli/ may import forge.core.cli (the CLI
#      layer is a leaf — nothing else should depend on it), with one
#      deliberate exception: forge.core.cli.envelope (the shared
#      CommandEnvelope output shape) is itself
#      CLI-layer shared code, importable from forge/verification/__main__.py — a
#      separate CLI entry point that predates and does not import
#      forge/core/cli/_shared.py — without that being a layering violation.
#   2. forge/contracts/ and forge/generation/ (structural generation) must
#      not import forge/verification/, and vice versa — independent
#      subsystems composed by the CLI layer, not by each other.
#      forge/generation/ *is* allowed to depend on forge/contracts/ (a
#      real, one-directional dependency: generation consumes the
#      contract/matching/CDC semantics contracts/ resolves) — only the
#      verification cross-import is forbidden.
#   3. forge/analysis/ must not import forge.core.cli.
#   3b. forge/project/ (discovery, adoption and project health — the layer
#      the new-user commands rest on) must not import forge.core.cli
#      either: `forge adopt`/`check`/`next` are renderers over it, so the
#      dependency runs CLI -> project, never back.
#
#   4. forge/ir/ (the canonical IR — see forge/ir/model.py) must not import
#      forge.core.cli, forge.generation.generators, or forge.verification —
#      the IR is a read-only consumer of topology/contract/IP-metadata
#      loaders only (see forge/ir/build.py's own module docstring), never
#      of CLI rendering or generator/verification logic. It *is* allowed
#      to depend on forge.contracts (the schema/matching/CDC layer it
#      resolves against).
#
# Note: forge/verify/, forge/analyze/, and forge/topgen/ no longer exist
# in any form (no compatibility shim) — see
# docs/development/adr/0005-package-and-cli-naming.md. Every consumer was
# updated to forge/verification/, forge/analysis/, forge/contracts/, and
# forge/generation/ directly.
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
    forge/contracts forge/generation forge/hls forge/analysis forge/integration forge/ir \
    forge/project

# forge/verification is checked separately: forge.core.cli.envelope is a
# deliberate, documented exception (see comment above) — every other
# forge.core.cli import from forge/verification/ is still a violation.
verify_hits="$(grep -rnE '(from|import)[[:space:]]+[.[:alnum:]]*core\.cli\b' \
    --include='*.py' forge/verification 2>/dev/null \
    | grep -v '/tests/' | grep -v 'core\.cli\.envelope\b' || true)"
if [[ -n "$verify_hits" ]]; then
    echo "❌ no cross-import of forge.core.cli outside forge/core/cli/ (forge/verification)"
    echo "$verify_hits" | sed 's/^/    /'
    violations=$((violations + 1))
else
    echo "✅ no cross-import of forge.core.cli outside forge/core/cli/ (forge/verification, envelope exempted)"
fi

echo "--- Rule 2: forge/contracts, forge/generation, and forge/verification do not cross-import ---"
_check "forge/contracts must not import forge.verification" \
    '(from|import)[[:space:]]+[.[:alnum:]]*verif(y|ication)\b' \
    forge/contracts
_check "forge/generation must not import forge.verification" \
    '(from|import)[[:space:]]+[.[:alnum:]]*verif(y|ication)\b' \
    forge/generation
_check "forge/verification must not import forge.contracts or forge.generation" \
    '(from|import)[[:space:]]+[.[:alnum:]]*(contracts|generation)\b' \
    forge/verification

echo "--- Rule 3: forge/analysis does not import forge.core.cli ---"
_check "forge/analysis must not import forge.core.cli" \
    '(from|import)[[:space:]]+[.[:alnum:]]*core\.cli\b' \
    forge/analysis

echo "--- Rule 4: forge/ir stays read-only (no CLI/generator/verification deps) ---"
_check "forge/ir must not import forge.core.cli" \
    '(from|import)[[:space:]]+[.[:alnum:]]*core\.cli\b' \
    forge/ir
_check "forge/ir must not import forge.generation.generators (no generator mutation)" \
    '(from|import)[[:space:]]+[.[:alnum:]]*generation\.generators\b' \
    forge/ir
_check "forge/ir must not import forge.verification" \
    '(from|import)[[:space:]]+[.[:alnum:]]*verif(y|ication)\b' \
    forge/ir

if [[ "$violations" -gt 0 ]]; then
    echo ""
    echo "❌ $violations dependency-direction violation(s) found."
    exit 1
fi

echo ""
echo "✅ No dependency-direction violations found."
