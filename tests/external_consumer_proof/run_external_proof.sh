#!/usr/bin/env bash
# External Consumer Proof
#
# Validates that a consumer workspace physically outside the framework source
# tree can configure, generate artifacts, and link against the framework package
# using only documented public interfaces (installed CLI + cmake --install prefix).
#
# No step in this proof references topgen.sh or uses PYTHONPATH injection.
#
# Usage:
#   bash tests/external_consumer_proof/run_external_proof.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

echo "=== External Consumer Proof ==="
echo "Repo root : $REPO_ROOT"
echo "Work dir  : $WORK_DIR"
echo ""

# --------------------------------------------------------------------------
echo "[1/6] Build and install framework C++ package"
# --------------------------------------------------------------------------
cmake -S "$REPO_ROOT/framework" -B "$WORK_DIR/fw_build" -DCMAKE_BUILD_TYPE=Release
cmake --build "$WORK_DIR/fw_build"
cmake --install "$WORK_DIR/fw_build" --prefix "$WORK_DIR/fw_install"

# Locate cmake package dir (handles lib vs lib64 on different distros)
FW_CMAKE_INSTALL_DIR="$(find "$WORK_DIR/fw_install" -name "frameworkVerifyConfig.cmake" -exec dirname {} \; | head -1)"
if [[ -z "$FW_CMAKE_INSTALL_DIR" ]]; then
    echo "FAIL: frameworkVerifyConfig.cmake not found under $WORK_DIR/fw_install" >&2
    exit 1
fi
echo "  ✓ Framework C++ package installed to $WORK_DIR/fw_install"
echo "  ✓ cmake package dir: $FW_CMAKE_INSTALL_DIR"

# --------------------------------------------------------------------------
echo "[2/6] Install topgen CLI via pip"
# --------------------------------------------------------------------------
pip install "$REPO_ROOT/framework/topgen/" --quiet
if ! command -v topgen >/dev/null 2>&1; then
    echo "FAIL: topgen not found on PATH after pip install" >&2
    exit 1
fi
TOPGEN_BIN="$(command -v topgen)"
echo "  ✓ topgen installed: $TOPGEN_BIN"

# --------------------------------------------------------------------------
echo "[3/6] Stage consumer design in isolated workspace"
# --------------------------------------------------------------------------
CONSUMER_DIR="$WORK_DIR/my_consumer"
mkdir -p "$CONSUMER_DIR/rtl" "$CONSUMER_DIR/verify"

MINIMAL_SRC="$REPO_ROOT/framework/proof/minimal_consumer"
cp "$MINIMAL_SRC/design.yml"                        "$CONSUMER_DIR/"
cp "$MINIMAL_SRC/rtl/rtl_passthrough.v"             "$CONSUMER_DIR/rtl/"
cp "$MINIMAL_SRC/verify/CMakeLists.txt"             "$CONSUMER_DIR/verify/"
cp "$MINIMAL_SRC/verify/minimal_consumer_smoke.cpp" "$CONSUMER_DIR/verify/"

echo "  ✓ Consumer workspace staged at $CONSUMER_DIR"
echo "  ✓ Working directory is outside repo tree (not under $REPO_ROOT/framework)"

# --------------------------------------------------------------------------
echo "[4/6] Generate artifacts using installed CLI"
# --------------------------------------------------------------------------
mkdir -p "$CONSUMER_DIR/generated"
cd "$CONSUMER_DIR"

"$TOPGEN_BIN" validate design.yml --strict

"$TOPGEN_BIN" gen-top design.yml \
    --mode verilog \
    --consumer-root "$CONSUMER_DIR" \
    --build-dir "$CONSUMER_DIR/build" \
    --hls-build-root "$CONSUMER_DIR/build_hls" \
    --ip-root "$CONSUMER_DIR/ips" \
    --output generated/algo_top.v

echo "  ✓ Artifacts generated"

# --------------------------------------------------------------------------
echo "[5/6] Verify generated artifacts exist"
# --------------------------------------------------------------------------
for artifact in algo_top.v build_manifest.json design_parameters.json \
    ip_info.yaml port_map.yaml probe_map.yaml tb_bindings.svh; do
    if [[ ! -f "$CONSUMER_DIR/generated/$artifact" ]]; then
        echo "FAIL: missing generated artifact: $artifact" >&2
        exit 1
    fi
done
echo "  ✓ All expected artifacts present"

# Verify no OMTF-specific group names appear in generated artifacts
if grep -rl "dt_inputs\|csc_inputs\|rpc_inputs" "$CONSUMER_DIR/generated/" 2>/dev/null | grep -q .; then
    echo "FAIL: OMTF-specific group names found in generated artifacts" >&2
    grep -rl "dt_inputs\|csc_inputs\|rpc_inputs" "$CONSUMER_DIR/generated/" >&2
    exit 1
fi
echo "  ✓ No OMTF-specific group names in generated artifacts"

# --------------------------------------------------------------------------
echo "[6/6] Build and test consumer verification"
# --------------------------------------------------------------------------
cmake -S "$CONSUMER_DIR/verify" -B "$CONSUMER_DIR/verify_build" \
    -DframeworkVerify_DIR="$FW_CMAKE_INSTALL_DIR"
cmake --build "$CONSUMER_DIR/verify_build"
ctest --test-dir "$CONSUMER_DIR/verify_build" --output-on-failure

echo ""
echo "PASS: External consumer proof completed successfully"
