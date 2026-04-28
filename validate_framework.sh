#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRAMEWORK_BUILD_DIR="${1:-$SCRIPT_DIR/build}"
GENERATOR_DIR="$SCRIPT_DIR/topgen"
GENERATOR_EXAMPLE="$GENERATOR_DIR/examples/design.yml"
MINIMAL_CONSUMER_DIR="$SCRIPT_DIR/proof/minimal_consumer"
MINIMAL_CONSUMER_BUILD_DIR="$FRAMEWORK_BUILD_DIR/minimal_consumer"
MINIMAL_CONSUMER_OUTPUT_DIR="$MINIMAL_CONSUMER_BUILD_DIR/generated"
MINIMAL_CONSUMER_PACKAGE_BUILD_DIR="$MINIMAL_CONSUMER_BUILD_DIR/verify"

run_generator_cli() {
    PYTHONPATH="$GENERATOR_DIR${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m topgen.cli.main "$@"
}

echo "[1/8] Configure framework-only CMake build"
cmake -S "$SCRIPT_DIR" -B "$FRAMEWORK_BUILD_DIR"

echo "[2/8] Build framework verification targets"
cmake --build "$FRAMEWORK_BUILD_DIR"

echo "[3/8] Run framework C++ smoke tests"
ctest --test-dir "$FRAMEWORK_BUILD_DIR" --output-on-failure

echo "[4/8] Run framework generator validation"
if [[ -d "$GENERATOR_DIR/tests" ]]; then
    # Ignore repo-local pytest addopts such as coverage flags so this validation
    # only depends on the framework's supported runtime/test dependencies.
    python3 -m pytest -o addopts='' "$GENERATOR_DIR/tests"
else
    run_generator_cli validate "$GENERATOR_EXAMPLE" --strict
fi

echo "[5/8] Validate installed fw_verify package"
FW_VERIFY_PYTHON_DIR="$SCRIPT_DIR/verify/python"
pip install "$FW_VERIFY_PYTHON_DIR" --quiet
if ! command -v fw_verify >/dev/null 2>&1; then
    echo "ERROR: fw_verify not found on PATH after pip install" >&2
    exit 1
fi
fw_verify --help >/dev/null

echo "[6/8] Run split-boundary smoke validation"
mkdir -p "$MINIMAL_CONSUMER_BUILD_DIR"
rm -rf "$MINIMAL_CONSUMER_PACKAGE_BUILD_DIR"

pushd /tmp >/dev/null
run_generator_cli validate "$MINIMAL_CONSUMER_DIR/design.yml" --strict
run_generator_cli gen-top "$MINIMAL_CONSUMER_DIR/design.yml" \
    --mode verilog \
    --consumer-root "$MINIMAL_CONSUMER_BUILD_DIR" \
    --build-dir "$MINIMAL_CONSUMER_BUILD_DIR/build" \
    --hls-build-root "$MINIMAL_CONSUMER_BUILD_DIR/build_hls" \
    --ip-root "$MINIMAL_CONSUMER_BUILD_DIR/ips" \
    --output generated/algo_top.v
popd >/dev/null

for artifact in \
    algo_top.v \
    build_manifest.json \
    design_parameters.json \
    ip_info.yaml \
    port_map.yaml \
    probe_map.yaml \
    tb_bindings.svh; do
    if [[ ! -f "$MINIMAL_CONSUMER_OUTPUT_DIR/$artifact" ]]; then
        echo "ERROR: expected minimal-consumer artifact missing: $MINIMAL_CONSUMER_OUTPUT_DIR/$artifact" >&2
        exit 1
    fi
done

cmake -S "$MINIMAL_CONSUMER_DIR/verify" \
    -B "$MINIMAL_CONSUMER_PACKAGE_BUILD_DIR" \
    -DframeworkVerify_DIR="$FRAMEWORK_BUILD_DIR/verify"
cmake --build "$MINIMAL_CONSUMER_PACKAGE_BUILD_DIR"
ctest --test-dir "$MINIMAL_CONSUMER_PACKAGE_BUILD_DIR" --output-on-failure

echo "[7/8] Install-tree consumption proof"
INSTALL_STAGING="$(mktemp -d)"
trap 'rm -rf "$INSTALL_STAGING"' EXIT

cmake --install "$FRAMEWORK_BUILD_DIR" --prefix "$INSTALL_STAGING/fw_install"

# Locate cmake package dir (handles lib vs lib64 on different distros)
FW_CMAKE_INSTALL_DIR="$(find "$INSTALL_STAGING/fw_install" -name "frameworkVerifyConfig.cmake" -exec dirname {} \; | head -1)"
if [[ -z "$FW_CMAKE_INSTALL_DIR" ]]; then
    echo "ERROR: frameworkVerifyConfig.cmake not found under $INSTALL_STAGING/fw_install" >&2
    exit 1
fi
echo "  install cmake dir: $FW_CMAKE_INSTALL_DIR"

# Verify key installed files exist
for f in \
    frameworkVerifyConfig.cmake \
    frameworkVerifyTargets.cmake; do
    if [[ ! -f "$FW_CMAKE_INSTALL_DIR/$f" ]]; then
        echo "ERROR: installed package file missing: $FW_CMAKE_INSTALL_DIR/$f" >&2
        exit 1
    fi
done

# Build a one-shot consumer against the install prefix
INSTALL_CONSUMER_DIR="$INSTALL_STAGING/install_consumer"
mkdir -p "$INSTALL_CONSUMER_DIR"
cat > "$INSTALL_CONSUMER_DIR/CMakeLists.txt" << 'EOF'
cmake_minimum_required(VERSION 3.10)
project(install_proof LANGUAGES CXX)
find_package(frameworkVerify CONFIG REQUIRED)
add_executable(smoke smoke.cpp)
target_link_libraries(smoke PRIVATE framework::verif_core)
target_compile_features(smoke PRIVATE cxx_std_17)
EOF
cat > "$INSTALL_CONSUMER_DIR/smoke.cpp" << 'EOF'
#include "analysis_port.h"
int main() {
    verif_fw::AnalysisPort<int> port;
    int v = 0;
    port.subscribe([&v](const int& x){ v = x; });
    port.write(1);
    return v == 1 ? 0 : 1;
}
EOF
cmake -S "$INSTALL_CONSUMER_DIR" -B "$INSTALL_CONSUMER_DIR/build" \
    -DframeworkVerify_DIR="$FW_CMAKE_INSTALL_DIR"
cmake --build "$INSTALL_CONSUMER_DIR/build"
"$INSTALL_CONSUMER_DIR/build/smoke"

echo "[8/8] Validate installed topgen CLI and external consumer proof"
pip install "$GENERATOR_DIR" --quiet
if ! command -v topgen >/dev/null 2>&1; then
    echo "ERROR: topgen not found on PATH after pip install" >&2
    exit 1
fi
topgen --help >/dev/null
topgen validate "$MINIMAL_CONSUMER_DIR/design.yml" --strict

EXTERNAL_PROOF="$SCRIPT_DIR/../tests/external_consumer_proof/run_external_proof.sh"
if [[ -x "$EXTERNAL_PROOF" ]]; then
    bash "$EXTERNAL_PROOF"
else
    echo "WARNING: external consumer proof script not found at $EXTERNAL_PROOF — skipping" >&2
fi

echo "Framework validation completed"
