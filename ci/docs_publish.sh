#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# ci/docs_publish.sh — release-plan Phase 9, slice 9.5
# ═══════════════════════════════════════════════════════════════════════════
#
# Builds the complete, versioned `mike` documentation artifact for the
# current repo state and exports it as a plain directory tree — every
# retained version (the current default branch's docs as "dev", plus the
# most recent release tag's docs, aliased "latest"), not just whichever
# one triggered this job. Never pushes anywhere: public documentation
# publication stays explicitly OPEN, blocked on the canonical-host
# decision (see CONTRIBUTING.md's "Where this lives" — the CERN GitLab
# origin is provisional, the project is migrating to a public host TBD).
#
# Version/alias policy:
#   - "dev"          -> always deployed, from the current commit (HEAD)
#   - "<latest tag>" -> deployed, aliased "latest" and set as the default
#                       version, only if a vMAJOR.MINOR.PATCH tag exists
#
# Each version is built from *its own* commit (a separate isolated git
# worktree per version) — a tagged release's docs reflect that tag's real
# historical source, never the current HEAD's content merely relabeled.
# mike's own git operations (committing to the `gh-pages` branch) happen
# entirely inside these disposable worktrees, so the actual checked-out
# working branch this script is invoked from is never touched, regardless
# of mike's own internals.
#
# Usage:
#   bash ci/docs_publish.sh --output-dir DIR
#
# Returns: 0 on success. The complete versioned site (every version this
# run deployed, with aliases/versions.json) is written to DIR.
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_BRANCH="gh-pages"
OUTPUT_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done
[[ -n "${OUTPUT_DIR}" ]] || { echo "error: --output-dir is required" >&2; exit 2; }

_WORKTREES=()
_cleanup() {
    for wt in "${_WORKTREES[@]:-}"; do
        [[ -n "${wt}" ]] || continue
        git -C "${REPO_ROOT}" worktree remove --force "${wt}" >/dev/null 2>&1 || rm -rf "${wt}"
    done
}
trap _cleanup EXIT

# _deploy_ref REF VERSION [ALIAS] — build REF's own docs and mike-deploy
# them to $DEPLOY_BRANCH as VERSION (with ALIAS, if given), inside a fresh,
# disposable worktree checked out at REF.
_deploy_ref() {
    local ref="$1" version="$2" alias="${3:-}"
    local wt_dir
    wt_dir="$(mktemp -d)"
    _WORKTREES+=("${wt_dir}")

    git -C "${REPO_ROOT}" worktree add --detach "${wt_dir}" "${ref}" >/dev/null

    (
        cd "${wt_dir}"
        if [[ -n "${alias}" ]]; then
            mike deploy --config-file mkdocs.yml --branch "${DEPLOY_BRANCH}" --update-aliases "${version}" "${alias}"
        else
            mike deploy --config-file mkdocs.yml --branch "${DEPLOY_BRANCH}" "${version}"
        fi
    )
}

echo "Deploying 'dev' from the current commit..."
_deploy_ref "HEAD" "dev"

LATEST_TAG="$(git -C "${REPO_ROOT}" tag --list 'v[0-9]*.[0-9]*.[0-9]*' --sort=-v:refname | head -n1 || true)"
if [[ -n "${LATEST_TAG}" ]]; then
    echo "Deploying '${LATEST_TAG}' (alias: latest) from its own tagged commit..."
    _deploy_ref "${LATEST_TAG}" "${LATEST_TAG}" "latest"

    DEFAULT_WT="$(mktemp -d)"
    _WORKTREES+=("${DEFAULT_WT}")
    git -C "${REPO_ROOT}" worktree add --detach "${DEFAULT_WT}" HEAD >/dev/null
    ( cd "${DEFAULT_WT}" && mike set-default --config-file mkdocs.yml --branch "${DEPLOY_BRANCH}" latest )
else
    echo "No vMAJOR.MINOR.PATCH tag found — skipping the 'latest' release version."
fi

# Export the *complete* deploy-branch tree (every version deployed above,
# not just whichever one triggered this job) as a plain directory. No
# --push anywhere in this script — see the module docstring above.
rm -rf "${OUTPUT_DIR}"
mkdir -p "${OUTPUT_DIR}"
git -C "${REPO_ROOT}" --work-tree="${OUTPUT_DIR}" checkout "${DEPLOY_BRANCH}" -- .

echo ""
echo "Wrote complete versioned site artifact to ${OUTPUT_DIR}"
echo "Retained versions:"
python3 - "${OUTPUT_DIR}/versions.json" <<'EOF'
import json, sys
for v in json.load(open(sys.argv[1])):
    aliases = ", ".join(v["aliases"]) or "none"
    print(f"  - {v['version']} (aliases: {aliases})")
EOF
