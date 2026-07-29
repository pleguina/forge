#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# The Five-Minute Quickstart — single source of truth
# ═══════════════════════════════════════════════════════════════════════════
#
# This is the exact, runnable command sequence for FORGE's five-minute
# quickstart. It is the one place these commands are written down:
#
#   - docs/getting-started/quickstart.md embeds this file's contents
#     verbatim (via forge.docsgen) rather than re-typing the commands, so
#     the published doc can never drift from a sequence that actually runs.
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

# 2. Confirm the CLI is on PATH and healthy.
forge --help > /dev/null

# 3. Scaffold a new plugin. This creates
#    <PLUGINS_ROOT>/<PLUGIN_ID>/forge/verify/ with a starter
#    design.verification.yml, tools/bootstrap.py, tools/gen_stimulus.py,
#    and a golden XML dataset — everything needed for Stage 4 below.
forge verify init-plugin "${PLUGIN_ID}" --plugins-root "${PLUGINS_ROOT}"

# 4. Health-check the scaffolded plugin. This is read-only and safe to run
#    at any time; --json is available for machine-readable output. A fresh
#    scaffold reports FAIL here — that's expected and useful: doctor is
#    telling you what `forge verify generate` still needs to create, not
#    reporting a broken install. `|| true` keeps this script running so you
#    can see that output rather than have it treated as a hard error.
forge verify doctor "${PLUGINS_ROOT}/${PLUGIN_ID}/forge/verify/design.verification.yml" || true
