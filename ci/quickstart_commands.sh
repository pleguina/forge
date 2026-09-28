#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# The Five-Minute Quickstart — single source of truth
# ═══════════════════════════════════════════════════════════════════════════
#
# This is the exact, runnable command sequence for FORGE's five-minute
# quickstart. It is the one place these commands are written down:
#
#   - docs/getting-started/quickstart.md walks through this exact sequence,
#     so the published doc can never drift from commands that actually run.
#   - ci/fresh_user_check.sh sources this file for its early stages, so CI
#     keeps proving these exact commands work on every pipeline run.
#
# Safe to run standalone, from the repo root:
#
#   bash ci/quickstart_commands.sh
#
# Optional environment overrides (fresh_user_check.sh sets these before
# sourcing so the scaffolded plugin lands in its own managed scratch dir):
#
#   PLUGIN_ID       Name for the scaffolded plugin (default: my_plugin)
#   PLUGINS_ROOT    Directory to scaffold it under (default: a fresh temp dir,
#                   removed on exit)
#   SKIP_INSTALL    Set to "true" to skip the pip install step (default: false)
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

: "${PLUGIN_ID:=my_plugin}"
: "${PLUGINS_ROOT:=}"
: "${SKIP_INSTALL:=false}"

if [[ -z "${PLUGINS_ROOT}" ]]; then
    PLUGINS_ROOT="$(mktemp -d)"
    trap 'rm -rf "${PLUGINS_ROOT}"' EXIT
fi

# 1. Install FORGE from source (editable). The `parser` extra enables
#    structured RTL port parsing via pyverilog; a regex fallback is used
#    without it, so it's optional.
if [[ "${SKIP_INSTALL}" != "true" ]]; then
    pip install -q -e "forge[parser]"
fi

# 2. Check the install and the local toolchain. Read-only; tells you which
#    optional tools are missing rather than failing on them.
forge doctor

# 3. Scaffold a plugin and run the whole chain: topology + verification
#    scaffold, validate, generate the top level, simulate, and write a
#    report bundle. One command, no files to edit first.
#
#    The simulation stage is skipped with a note if no simulator (Vivado
#    xsim) is on PATH — everything else still runs, so this works on a
#    machine that has never had an EDA tool installed.
forge init "${PLUGIN_ID}" --plugins-root "${PLUGINS_ROOT}"

# 4. Read back what was generated, straight from the canonical IR.
forge inspect "${PLUGINS_ROOT}/${PLUGIN_ID}/forge/designs/design.yml" \
    --contracts-from "${PLUGINS_ROOT}/${PLUGIN_ID}/forge/modules.yml"
