#!/bin/bash
# Extract HLS metrics and generate reports
# This script is called by build_all.sh but can also be run standalone

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONSUMER_ROOT="${CONSUMER_ROOT:-}"
BUILD_HLS_DIR="${BUILD_HLS_DIR:-}"
REPORT_ROOT="${REPORT_ROOT:-}"

if [ -z "$BUILD_HLS_DIR" ]; then
    if [ -n "$CONSUMER_ROOT" ]; then
        BUILD_HLS_DIR="$CONSUMER_ROOT/build_hls"
    else
        echo "Error: BUILD_HLS_DIR is not set. Pass BUILD_HLS_DIR or CONSUMER_ROOT explicitly." >&2
        exit 1
    fi
fi

BUILD_HLS_DIR="$(python3 - <<'PY' "$BUILD_HLS_DIR"
from pathlib import Path
import sys
print(Path(sys.argv[1]).expanduser().resolve())
PY
)"

if [ -z "$REPORT_ROOT" ]; then
    REPORT_ROOT="$(python3 - <<'PY' "$BUILD_HLS_DIR"
from pathlib import Path
import sys
build_dir = Path(sys.argv[1]).resolve()
print((build_dir.parent / 'out' / 'reports').resolve())
PY
)"
fi

mkdir -p "$REPORT_ROOT/plots"

echo "==========================================="
echo "HLS Metrics Extraction & Report Generation"
echo "==========================================="
echo ""

# Check if build_hls directory exists
if [ ! -d "$BUILD_HLS_DIR" ]; then
    echo "Error: build_hls directory not found: $BUILD_HLS_DIR"
    echo "Please run HLS synthesis first:"
    echo "  topgen hls run --jobs 4 --stage synth --modules design --design <design.yml> --hls-config <plugin-hls-config.yaml>"
    exit 1
fi

# Extract HLS metrics
echo "Extracting HLS synthesis metrics..."
if python3 "$SCRIPT_DIR/extract_hls_metrics.py" \
    --build-dir "$BUILD_HLS_DIR" \
    --output "$REPORT_ROOT/hls_metrics.json"; then
    echo "✓ Metrics extracted to: $REPORT_ROOT/hls_metrics.json"
else
    echo "✗ Failed to extract metrics"
    exit 1
fi

# Generate visualization plots
if [ -f "$SCRIPT_DIR/visualize_hls_pipeline.py" ]; then
    echo ""
    echo "Generating visualization plots..."
    if python3 "$SCRIPT_DIR/visualize_hls_pipeline.py" \
        "$REPORT_ROOT/hls_metrics.json" \
        --output "$REPORT_ROOT/plots/"; then
        echo "✓ Plots generated in: $REPORT_ROOT/plots/"
    else
        echo "✗ Failed to generate plots (non-fatal)"
    fi
else
    echo ""
    echo "⚠ Skipping plot generation (visualize_hls_pipeline.py not found)"
fi

echo ""
echo "==========================================="
echo "✓ Report generation complete!"
echo "==========================================="
echo ""
echo "Generated files:"
echo "  - $REPORT_ROOT/hls_metrics.json"
if [ -d "$REPORT_ROOT/plots" ]; then
    PLOT_COUNT=$(ls "$REPORT_ROOT"/plots/*.png 2>/dev/null | wc -l)
    echo "  - $REPORT_ROOT/plots/ ($PLOT_COUNT plots)"
fi
echo ""
echo "View metrics:"
echo "  cat $REPORT_ROOT/hls_metrics.json | python3 -m json.tool"
echo ""
if [ -d "$REPORT_ROOT/plots" ] && [ "$PLOT_COUNT" -gt 0 ]; then
    echo "View plots:"
    echo "  ls $REPORT_ROOT/plots/"
    echo ""
fi
