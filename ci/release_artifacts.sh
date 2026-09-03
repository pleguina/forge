#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# ci/release_artifacts.sh — build one release's immutable artifact set.
#
# Phase F1 of FORGE_new_user_implementation_plan.md: a release is a set of
# files someone can download, verify and install six months from now — not a
# branch that keeps moving. This produces that set, from a clean tree, with
# nothing typed by hand:
#
#   forge-<version>.tar.gz         source archive (sdist)
#   forge-<version>-py3-none-any.whl   wheel
#   SHA256SUMS                     checksums for both
#   RELEASE_NOTES.md               this version's CHANGELOG section, verbatim
#   MIGRATION.md                   migration notes, copied from the repo root
#   support_matrix.md              the generated support matrix
#   tested_environments.md         what this build was actually tested in
#   MANIFEST.json                  the whole set, machine-readable
#
# Everything here is *evidence about this build*. The tested-environment
# list records the tool versions present while the suite ran, not a list of
# platforms we would like to claim: a tool that is absent is recorded as
# absent.
#
# Usage:
#   bash ci/release_artifacts.sh                 # build into dist/
#   bash ci/release_artifacts.sh --output DIR    # elsewhere
#   bash ci/release_artifacts.sh --skip-tests    # artifacts only (CI reruns them)
#
# Exit status is non-zero if any step fails, so a release cannot be cut from
# a tree whose tests do not pass.
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

OUTPUT_DIR="${REPO_ROOT}/dist"
SKIP_TESTS=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --output) OUTPUT_DIR="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"; shift 2 ;;
        --skip-tests) SKIP_TESTS=true; shift ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

PASS_COLOR='\033[0;32m'; FAIL_COLOR='\033[0;31m'; SECTION_COLOR='\033[0;34m'; NC='\033[0m'
section() { echo -e "\n${SECTION_COLOR}=== $* ===${NC}"; }
pass()    { echo -e "  ${PASS_COLOR}PASS${NC}: $*"; }
fail()    { echo -e "  ${FAIL_COLOR}FAIL${NC}: $*" >&2; exit 1; }

VERSION="$(python3 - <<'PY'
import re, pathlib
text = pathlib.Path("forge/pyproject.toml").read_text()
match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
print(match.group(1) if match else "")
PY
)"
[[ -n "${VERSION}" ]] || fail "could not read version from forge/pyproject.toml"

# ─────────────────────────────────────────────────────────────────────────
section "Release ${VERSION} -> ${OUTPUT_DIR}"
# ─────────────────────────────────────────────────────────────────────────

# A release must describe a committed state. A dirty tree would produce
# artifacts nobody can reproduce from the repository.
if git -C "${REPO_ROOT}" rev-parse --git-dir > /dev/null 2>&1; then
    GIT_COMMIT="$(git -C "${REPO_ROOT}" rev-parse HEAD)"
    if [[ -n "$(git -C "${REPO_ROOT}" status --porcelain)" ]]; then
        echo "  WARNING: the working tree has uncommitted changes — these"
        echo "           artifacts will not be reproducible from any commit."
        GIT_COMMIT="${GIT_COMMIT}-dirty"
    fi
else
    GIT_COMMIT="unknown"
fi
pass "source state: ${GIT_COMMIT}"

# ─────────────────────────────────────────────────────────────────────────
section "1. Acceptance: the suite that gates a release"
# ─────────────────────────────────────────────────────────────────────────
if [[ "${SKIP_TESTS}" == "true" ]]; then
    echo "  skipped (--skip-tests)"
    TEST_RESULT="skipped"
else
    python3 -m pytest "${REPO_ROOT}/forge/tests" -q --tb=short \
        || fail "the test suite must pass before a release is cut"
    pass "forge unit tests"
    TEST_RESULT="passed"
fi

# ─────────────────────────────────────────────────────────────────────────
section "2. Build sdist + wheel"
# ─────────────────────────────────────────────────────────────────────────
rm -rf "${OUTPUT_DIR}"
mkdir -p "${OUTPUT_DIR}"
python3 -m build --outdir "${OUTPUT_DIR}" "${REPO_ROOT}/forge" > "${OUTPUT_DIR}/build.log" 2>&1 \
    || { cat "${OUTPUT_DIR}/build.log" >&2; fail "python -m build failed (pip install build)"; }
rm -f "${OUTPUT_DIR}/build.log"
WHEEL="$(ls "${OUTPUT_DIR}"/*.whl 2>/dev/null | head -1)"
SDIST="$(ls "${OUTPUT_DIR}"/*.tar.gz 2>/dev/null | head -1)"
[[ -f "${WHEEL}" ]] || fail "no wheel produced"
[[ -f "${SDIST}" ]] || fail "no source archive produced"
pass "$(basename "${WHEEL}")"
pass "$(basename "${SDIST}")"

# ─────────────────────────────────────────────────────────────────────────
section "3. Install the wheel into a clean venv and smoke-test it"
# ─────────────────────────────────────────────────────────────────────────
# The one check that catches a broken package: a wheel that imports and runs
# in an environment that has never seen this repository.
SMOKE_VENV="$(mktemp -d)/venv"
python3 -m venv "${SMOKE_VENV}"
"${SMOKE_VENV}/bin/pip" install -q --upgrade pip
"${SMOKE_VENV}/bin/pip" install -q "${WHEEL}" || fail "the wheel does not install"
INSTALLED_VERSION="$("${SMOKE_VENV}/bin/forge" --version 2>&1 | tr -d '\n')"
[[ "${INSTALLED_VERSION}" == *"${VERSION}"* ]] \
    || fail "installed wheel reports ${INSTALLED_VERSION}, expected ${VERSION}"
"${SMOKE_VENV}/bin/forge" doctor > /dev/null 2>&1 || true  # optional tools may be absent
pass "clean-venv install + forge --version + forge doctor"

# ─────────────────────────────────────────────────────────────────────────
section "4. Release notes, migration notes, support matrix"
# ─────────────────────────────────────────────────────────────────────────
python3 - "${VERSION}" "${OUTPUT_DIR}" <<'PY'
"""Assemble the human-readable half of the release from what the repo
already maintains — never re-typed, so it cannot drift from CHANGELOG.md."""
import re
import sys
from pathlib import Path

version, out_dir = sys.argv[1], Path(sys.argv[2])
changelog = Path("CHANGELOG.md").read_text()

# The section for this version, or [Unreleased] when the version has not
# been given its own heading yet (cutting a release candidate).
pattern = rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)"
match = re.search(pattern, changelog, re.S | re.M)
heading = f"[{version}]"
if not match:
    match = re.search(r"^## \[Unreleased\][^\n]*\n(.*?)(?=^## \[|\Z)", changelog, re.S | re.M)
    heading = "[Unreleased]"
if not match:
    raise SystemExit("no CHANGELOG section found for this release")

(out_dir / "RELEASE_NOTES.md").write_text(
    f"# FORGE {version}\n\n"
    f"Release notes taken verbatim from `CHANGELOG.md`'s {heading} section.\n\n"
    + match.group(1).strip() + "\n"
)

migration = Path("MIGRATION.md")
if migration.is_file():
    (out_dir / "MIGRATION.md").write_text(migration.read_text())

support = Path("docs/reference/support-matrix.md")
if support.is_file():
    (out_dir / "support_matrix.md").write_text(support.read_text())
PY
pass "RELEASE_NOTES.md / MIGRATION.md / support_matrix.md"

# ─────────────────────────────────────────────────────────────────────────
section "5. Tested-environment list"
# ─────────────────────────────────────────────────────────────────────────
# What this build was actually run against. A tool that is not installed is
# recorded as absent rather than omitted, so the list can never read as a
# claim about an environment nobody tested.
python3 - "${VERSION}" "${OUTPUT_DIR}" "${TEST_RESULT}" "${GIT_COMMIT}" <<'PY'
import json
import platform
import sys
from pathlib import Path

from forge.core.toolchain_versions import DEFAULT_TOOLS, tool_present, tool_version
from forge.hls import tool_matrix

version, out_dir, test_result, commit = sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4]

tools = {}
for tool in DEFAULT_TOOLS:
    tools[tool] = tool_version(tool) if tool_present(tool) else "not installed"
vitis = tool_matrix.installed_version()
tools["vitis_hls"] = vitis or "not installed"

lines = [
    f"# FORGE {version} — tested environment",
    "",
    "The environment this release's artifacts were built and its test suite",
    "run in. This is a record of what was tested, not a claim of support for",
    "anything else: a tool absent here was absent from the run.",
    "",
    f"- **source**: `{commit}`",
    f"- **test suite**: {test_result}",
    f"- **python**: {platform.python_version()} ({platform.python_implementation()})",
    f"- **platform**: {platform.system()} {platform.release()} ({platform.machine()})",
    "",
    "| Tool | Version |",
    "|---|---|",
]
for tool, detail in sorted(tools.items()):
    lines.append(f"| `{tool}` | {detail} |")

if vitis:
    status = tool_matrix.status_for(vitis)
    lines += [
        "",
        f"HLS port prediction against Vitis HLS {vitis}: **{status}** — "
        f"{tool_matrix.describe_status(status)}",
    ]
lines += [
    "",
    "Validated Vitis HLS releases for HLS port prediction (see",
    "`support_matrix.md` for the full table):",
    "",
]
matrix = tool_matrix.support_matrix()
for release, status in sorted(matrix.items()):
    lines.append(f"- `{release}` — {status}")
if not matrix:
    lines.append("- *(none recorded)*")
lines.append("")

(out_dir / "tested_environments.md").write_text("\n".join(lines))
(out_dir / "environment.json").write_text(json.dumps({
    "version": version,
    "source": commit,
    "test_suite": test_result,
    "python": platform.python_version(),
    "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
    "tools": tools,
    "hls_support_matrix": tool_matrix.support_matrix(),
}, indent=2, sort_keys=True) + "\n")
PY
pass "tested_environments.md / environment.json"

# ─────────────────────────────────────────────────────────────────────────
section "6. Checksums and manifest"
# ─────────────────────────────────────────────────────────────────────────
(
    cd "${OUTPUT_DIR}"
    # Sorted, relative paths only — a checksum file that names this
    # machine's directories is not verifiable anywhere else.
    find . -maxdepth 1 -type f ! -name SHA256SUMS ! -name MANIFEST.json -printf '%P\n' \
        | LC_ALL=C sort | xargs sha256sum > SHA256SUMS
)
python3 - "${VERSION}" "${OUTPUT_DIR}" "${GIT_COMMIT}" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

version, out_dir, commit = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
files = []
for path in sorted(out_dir.iterdir()):
    if not path.is_file() or path.name == "MANIFEST.json":
        continue
    files.append({
        "name": path.name,
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    })
(out_dir / "MANIFEST.json").write_text(json.dumps({
    "version": version,
    "source": commit,
    "files": files,
}, indent=2) + "\n")
PY
pass "SHA256SUMS / MANIFEST.json"

# ─────────────────────────────────────────────────────────────────────────
section "Done"
# ─────────────────────────────────────────────────────────────────────────
ls -1 "${OUTPUT_DIR}"
echo
echo "Verify anywhere with:  cd $(basename "${OUTPUT_DIR}") && sha256sum -c SHA256SUMS"
echo "Tag the release with:  git tag -a v${VERSION} -m 'FORGE ${VERSION}'"
