#!/bin/bash
# generate_algorithm_top.sh — Framework orchestration convenience script
#
# NOT PART OF THE PUBLIC INTERFACE.
# This script is a repo-local convenience wrapper that composes topgen
# and report generation commands. It relies on the co-located framework source
# tree and is not portable across repository boundaries.
#
# External consumers must invoke topgen directly with explicit path
# arguments. See framework/MINIMAL_CONSUMER_QUICKSTART.md.
#
# Generate algorithm top-level with explicit paths (no defaults)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONSUMER_ROOT="${CONSUMER_ROOT:-}"

resolve_path() {
    python3 - <<'PY' "$1" "$2"
from pathlib import Path
import sys
raw = Path(sys.argv[1]).expanduser()
base = Path(sys.argv[2]).expanduser().resolve()
print((raw if raw.is_absolute() else base / raw).resolve())
PY
}

# ============================================================================
# Parse arguments (ALL REQUIRED)
# ============================================================================

usage() {
    cat << EOF
Usage: $0 <design_file> <ip_root> <build_hls_dir> <output_dir>

Generate algorithm top-level RTL from design specification.

Required Arguments:
  design_file      Path to design YAML file (e.g., designs/design.yml)
  ip_root          Directory with extracted IPs (e.g., ips/)
  build_hls_dir    Directory with HLS build outputs (e.g., build_hls/)

Optional Arguments:
  output_name      Output subdirectory name (default: design filename)
                   Results will be in out/<output_name>/

Examples:
  $0 designs/design.yml ips/ build_hls/
    → Outputs to out/design/

  $0 designs/design.yml ips/ build_hls/ my_test
    → Outputs to out/my_test/

Environment Variables (optional):
  IP_PACKAGES_DIR  Directory with IP .zip packages (default: ip_packages/)
                   Used only for automatic unpacking if needed
    CONSUMER_ROOT Base directory used to resolve relative paths (optional)
    TOPGEN_CMD    Path or command name for topgen invocation

EOF
    exit 1
}

# Check argument count (output_name is optional)
if [ $# -lt 3 ]; then
    echo "❌ Error: Missing required arguments"
    echo ""
    usage
fi

RESOLVE_BASE="${CONSUMER_ROOT:-$PWD}"

DESIGN_FILE="$(resolve_path "$1" "$RESOLVE_BASE")"
IP_ROOT="$(resolve_path "$2" "$RESOLVE_BASE")"
BUILD_HLS_DIR="$(resolve_path "$3" "$RESOLVE_BASE")"

# Output dir: if specified, use out/$4; otherwise use out/design_name
if [ -n "$4" ]; then
    OUTPUT_DIR="$(resolve_path "out/$4" "$RESOLVE_BASE")"
else
    OUTPUT_DIR="$(python3 - <<'PY' "$BUILD_HLS_DIR" "$(basename "$DESIGN_FILE" .yml)"
from pathlib import Path
import sys
build_dir = Path(sys.argv[1]).resolve()
design_name = sys.argv[2]
print((build_dir.parent / 'out' / design_name).resolve())
PY
)"
fi

# Optional: IP packages directory (for unpacking)
if [ -n "${IP_PACKAGES_DIR:-}" ]; then
    IP_PACKAGES_DIR="$(resolve_path "$IP_PACKAGES_DIR" "$RESOLVE_BASE")"
else
    IP_PACKAGES_DIR="$(python3 - <<'PY' "$BUILD_HLS_DIR"
from pathlib import Path
import sys
build_dir = Path(sys.argv[1]).resolve()
print((build_dir.parent / 'ip_packages').resolve())
PY
)"
fi

if [ -z "$CONSUMER_ROOT" ]; then
    CONSUMER_ROOT="$(python3 - <<'PY' "$IP_ROOT" "$BUILD_HLS_DIR" "$OUTPUT_DIR"
from pathlib import Path
import os
import sys

paths = [Path(value).resolve() for value in sys.argv[1:] if value]
common = Path(os.path.commonpath([str(path) for path in paths]))
print(common)
PY
)"
fi

if [ -n "${TOPGEN_CMD:-}" ]; then
    :
elif command -v topgen >/dev/null 2>&1; then
    TOPGEN_CMD="$(command -v topgen)"
else
    echo "❌ Error: topgen command not found. Install with: pip install <framework-path>/topgen/" >&2
    echo "   Or set TOPGEN_CMD to the full path of the installed CLI." >&2
    exit 1
fi

echo "WARNING: framework/hls/generate_algorithm_top.sh is deprecated and repo-local only." >&2
echo "WARNING: Prefer 'topgen gen-top' directly in maintained workflows." >&2

echo "==========================================="
echo "Algorithm Top Generation"
echo "==========================================="
echo ""
echo "Design file:     $DESIGN_FILE"
echo "IP root:         $IP_ROOT"
echo "HLS build dir:   $BUILD_HLS_DIR"
echo "Output dir:      $OUTPUT_DIR"
echo "IP packages:     $IP_PACKAGES_DIR (optional)"
echo ""

# ============================================================================
# Validate inputs
# ============================================================================

if [ ! -f "$DESIGN_FILE" ]; then
    echo "❌ Error: Design file not found: $DESIGN_FILE"
    exit 1
fi

if [ ! -d "$IP_ROOT" ]; then
    echo "❌ Error: IP root directory not found: $IP_ROOT"
    echo "   Create it or unpack IPs first: topgen unpack-ips"
    exit 1
fi

if [ ! -d "$BUILD_HLS_DIR" ]; then
    echo "❌ Error: HLS build directory not found: $BUILD_HLS_DIR"
    echo "   Run HLS synthesis first: topgen hls run --stage synth ..."
    exit 1
fi

# Create output directory
mkdir -p "$OUTPUT_DIR"

DESIGN_DIR="$(dirname "$DESIGN_FILE")"

# ============================================================================
# Step 1: Check if IPs need to be unpacked
# ============================================================================
echo "Checking IP availability..."

# Count available IPs in packages and extracted
IP_PACKAGES_COUNT=0
if [ -d "$IP_PACKAGES_DIR" ]; then
    IP_PACKAGES_COUNT=$(ls -1 "$IP_PACKAGES_DIR"/*.zip 2>/dev/null | wc -l || echo 0)
fi
IP_EXTRACTED_COUNT=$(find "$IP_ROOT" -name "component.xml" 2>/dev/null | wc -l || echo 0)
BUILD_HLS_COUNT=$(find "$BUILD_HLS_DIR" -name "component.xml" 2>/dev/null | wc -l || echo 0)

echo "  📦 IP packages (.zip):        $IP_PACKAGES_COUNT in $IP_PACKAGES_DIR/"
echo "  📂 Extracted IPs:             $IP_EXTRACTED_COUNT in $IP_ROOT/"
echo "  🔨 Built HLS IPs:             $BUILD_HLS_COUNT in $BUILD_HLS_DIR/"
echo ""

# If we have packages but no extracted IPs, unpack them
if [ "$IP_PACKAGES_COUNT" -gt 0 ] && [ "$IP_EXTRACTED_COUNT" -eq 0 ]; then
    echo "Unpacking IP archives from $IP_PACKAGES_DIR/..."
    "$TOPGEN_CMD" unpack-ips "$IP_PACKAGES_DIR" \
        --consumer-root "$CONSUMER_ROOT" \
        --ip-root "$IP_ROOT" \
        --force
    echo ""
fi

# ============================================================================
# Extract HLS Metrics (for design_parameters.json pipeline info)
# ============================================================================
echo "Extracting HLS metrics..."
echo ""

HLS_METRICS_FILE="$OUTPUT_DIR/hls_metrics.json"

python3 "$SCRIPT_DIR/extract_hls_metrics.py" \
    --build-dir "$BUILD_HLS_DIR" \
    --output "$HLS_METRICS_FILE" \
    --summary-only  # Just show summary, metrics go to JSON

if [ $? -eq 0 ]; then
    echo "✓ HLS metrics extracted to: $HLS_METRICS_FILE"
    echo ""
else
    echo "⚠️  Warning: Failed to extract HLS metrics"
    echo "   Pipeline latency info in design_parameters.json will be empty"
    echo ""
    HLS_METRICS_FILE=""  # Clear the file path if extraction failed
fi

# ============================================================================
# Generate algorithm top-level RTL
# ============================================================================
echo "Generating algorithm top-level..."
echo ""

# Always regenerate IP info by removing old one
rm -f "$OUTPUT_DIR/ip_info.yaml"

# Build topgen command with optional HLS metrics
GEN_TOP_CMD="\"$TOPGEN_CMD\" gen-top \"$DESIGN_FILE\" \
    --mode verilog \
    --consumer-root \"$CONSUMER_ROOT\" \
    --output \"$OUTPUT_DIR/algo_top.v\" \
    --ip-info \"$OUTPUT_DIR/ip_info.yaml\" \
    --ip-root \"$IP_ROOT\" \
    --build-dir \"$BUILD_HLS_DIR\" \
    --hls-build-root \"$BUILD_HLS_DIR\" \
    --src-root \"$DESIGN_DIR\" \
    --gen-testbench"

# Add HLS metrics if available
if [ -n "$HLS_METRICS_FILE" ] && [ -f "$HLS_METRICS_FILE" ]; then
    GEN_TOP_CMD="$GEN_TOP_CMD --hls-metrics \"$HLS_METRICS_FILE\""
fi

# Execute the command
eval $GEN_TOP_CMD

if [ ! -f "$OUTPUT_DIR/algo_top.v" ]; then
    echo "❌ ERROR: Failed to generate algo_top.v"
    exit 1
fi

echo "✓ Generated $OUTPUT_DIR/algo_top.v"

if [ -f "$OUTPUT_DIR/tb_algo_top.sv" ]; then
    echo "✓ Generated $OUTPUT_DIR/tb_algo_top.sv"
    echo "✓ Generated $OUTPUT_DIR/run_vivado_sim.tcl"
fi

echo ""

# ============================================================================
# Optional outputs
# ============================================================================

# Optional: Generate VHDL top as well
if [ "$GENERATE_VHDL" = "1" ]; then
    echo "Generating VHDL top-level..."
    "$TOPGEN_CMD" gen-top "$DESIGN_FILE" \
        --mode vhdl \
        --consumer-root "$CONSUMER_ROOT" \
        --output "$OUTPUT_DIR/algo_top.vhd" \
        --ip-root "$IP_ROOT" \
        --build-dir "$BUILD_HLS_DIR"
    echo "✓ Generated $OUTPUT_DIR/algo_top.vhd"
    echo ""
fi

# Optional: Generate Block Design TCL
if [ "$GENERATE_BD" = "1" ]; then
    echo "Generating Block Design TCL..."
    "$TOPGEN_CMD" gen-top "$DESIGN_FILE" \
        --mode bd \
        --consumer-root "$CONSUMER_ROOT" \
        --output "$OUTPUT_DIR/block_design.tcl" \
        --ip-root "$IP_ROOT" \
        --build-dir "$BUILD_HLS_DIR"
    echo "✓ Generated $OUTPUT_DIR/block_design.tcl"
    echo ""
fi

# ============================================================================
# Summary
# ============================================================================
echo "==========================================="
echo "✅ Algorithm top generation complete!"
echo "==========================================="
echo ""
echo "Generated files:"
echo "  ✓ $OUTPUT_DIR/algo_top.v"
echo "  ✓ $OUTPUT_DIR/ip_info.yaml"
echo "  ✓ $OUTPUT_DIR/build_manifest.json (auto-generated by topgen)"
echo "  ✓ $OUTPUT_DIR/design_parameters.json (with HLS pipeline metrics)"
[ -f "$HLS_METRICS_FILE" ] && echo "  ✓ $HLS_METRICS_FILE"
[ "$GENERATE_VHDL" = "1" ] && echo "  ✓ $OUTPUT_DIR/algo_top.vhd"
[ "$GENERATE_BD" = "1" ] && echo "  ✓ $OUTPUT_DIR/block_design.tcl"

echo ""

# Show stats
LINE_COUNT=$(wc -l < "$OUTPUT_DIR/algo_top.v")
MODULE_COUNT=$(grep -c "^module \|^ *\w\+_\w\+ " "$OUTPUT_DIR/algo_top.v" || echo 0)
REG_STAGE_COUNT=$(grep -c "RegisterStage #" "$OUTPUT_DIR/algo_top.v" || echo 0)

echo "Statistics:"
echo "  - Lines of Verilog: $LINE_COUNT"
echo "  - Module instances: $MODULE_COUNT"
echo "  - Register stage instances: $REG_STAGE_COUNT"

# Show register stage details from design file
if [ "$REG_STAGE_COUNT" -gt 0 ]; then
    echo ""
    echo "Pipeline register stages:"
    python3 -c "
import yaml
import sys

with open('$DESIGN_FILE') as f:
    design = yaml.safe_load(f)

for conn in design.get('connections', []):
    if 'register_stages' in conn:
        from_mod = conn['from']
        to_mod = conn['to']
        stages = conn['register_stages']
        
        # Count signals
        signal_count = 0
        if 'port_map' in conn:
            signal_count += len(conn['port_map'])
        if 'port_map_ranges' in conn:
            for range_map in conn['port_map_ranges']:
                signal_count += range_map.get('count', 1)
        
        print(f'  • {from_mod} → {to_mod}: {stages} pipeline stages × {signal_count} signals = {signal_count} register instances')
"
fi
echo ""

# ============================================================================
# Final Verification - Ensure All Artifacts Exist
# ============================================================================
echo "Verifying all artifacts..."

MISSING_FILES=0

# Check algo_top.v
if [ -f "$OUTPUT_DIR/algo_top.v" ]; then
    echo "  ✓ algo_top.v"
else
    echo "  ✗ algo_top.v - MISSING!"
    MISSING_FILES=$((MISSING_FILES + 1))
fi

# Check ip_info.yaml
if [ -f "$OUTPUT_DIR/ip_info.yaml" ]; then
    echo "  ✓ ip_info.yaml"
else
    echo "  ✗ ip_info.yaml - MISSING!"
    MISSING_FILES=$((MISSING_FILES + 1))
fi

# Check build_manifest.json
if [ -f "$OUTPUT_DIR/build_manifest.json" ]; then
    echo "  ✓ build_manifest.json"
else
    echo "  ✗ build_manifest.json - MISSING!"
    MISSING_FILES=$((MISSING_FILES + 1))
fi

echo ""

if [ $MISSING_FILES -gt 0 ]; then
    echo "⚠️  Warning: $MISSING_FILES artifact(s) missing!"
    echo ""
else
    echo "✅ All algorithm top artifacts successfully generated!"
    echo ""
    echo "Next steps:"
    echo "  1. View manifest:"
    echo "     cat $OUTPUT_DIR/build_manifest.json"
    echo "  2. Run an XSIM flow using the generated tb_algo_top.sv scaffold"
    echo ""
fi
