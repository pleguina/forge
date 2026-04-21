#!/bin/bash
################################################################################
# simple_hls.sh - Simple HLS Build Script
#
# Description:
#   A straightforward HLS build script where you directly specify:
#   - Source files (cpp)
#   - Header include directories
#   - Build commands: -csim, -synth, -cosim, -export
#
# Usage:
#   Edit the configuration section below with your paths
#   Then run: ./simple_hls.sh -csim -synth -cosim -export
#
################################################################################

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

################################################################################
# CONFIGURATION - EDIT THIS SECTION
################################################################################

# Project name and build directory
PROJECT_NAME="my_hls_project"
BUILD_DIR="./build_hls/${PROJECT_NAME}"

# Top function name
TOP_FUNCTION="my_top_function"

# FPGA part
PART_NAME="xcvu13p-fsga2577-1-e"

# Clock period in nanoseconds
CLOCK_PERIOD="2.78"

# Source files (design files to synthesize)
SOURCE_FILES=(
    "./algo/my_module/my_design.cpp"
)

# Testbench files (for C simulation and co-simulation)
TESTBENCH_FILES=(
    "./algo/my_module/my_design.cpp"
    "./verify/tests/tb_my_module.cpp"
)

# Include directories (without -I prefix, just the paths)
INCLUDE_DIRS=(
    "./algo/common"
    "./verify/include"
)

# Additional compiler flags (optional)
EXTRA_CFLAGS="-DHLS_CSIM_BUILD"

# Synthesis flow for export: "export" (no synth), "syn" (RTL synth), or "impl" (full impl)
SYNTH_FLOW="syn"

################################################################################
# END CONFIGURATION
################################################################################

# Script variables
VITIS_HLS="vitis_hls"
RUN_CSIM=0
RUN_SYNTH=0
RUN_COSIM=0
RUN_EXPORT=0

# Log functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $*"
}

usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Options:
  -csim       Run C simulation
  -synth      Run synthesis
  -cosim      Run co-simulation
  -export     Export IP
  -clean      Clean build directory
  -all        Run all steps (csim, synth, cosim, export)
  -h          Show this help message

Flow options (set SYNTH_FLOW variable in script):
  export     Export IP only, no synthesis (fastest)
  syn        Export with RTL synthesis (accurate timing)
  impl       Export with synthesis + place & route (most accurate, slowest)

Examples:
  $0 -csim                # Run only C simulation
  $0 -synth -export       # Run synthesis and export IP
  $0 -all                 # Run everything

EOF
    exit 1
}

# Parse arguments
if [[ $# -eq 0 ]]; then
    usage
fi

while [[ $# -gt 0 ]]; do
    case $1 in
        -csim)
            RUN_CSIM=1
            shift
            ;;
        -synth)
            RUN_SYNTH=1
            shift
            ;;
        -cosim)
            RUN_COSIM=1
            shift
            ;;
        -export)
            RUN_EXPORT=1
            shift
            ;;
        -clean)
            log_info "Cleaning build directory: $BUILD_DIR"
            rm -rf "$BUILD_DIR"
            log_success "Build directory cleaned"
            exit 0
            ;;
        -all)
            RUN_CSIM=1
            RUN_SYNTH=1
            RUN_COSIM=1
            RUN_EXPORT=1
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            ;;
    esac
done

# Check Vitis HLS
if ! command -v "$VITIS_HLS" &> /dev/null; then
    log_error "Vitis HLS not found: $VITIS_HLS"
    log_error "Please source Vitis HLS settings first:"
    log_error "  source /tools/Xilinx/Vitis_HLS/<version>/settings64.sh"
    exit 1
fi

log_info "Using Vitis HLS: $(command -v "$VITIS_HLS")"
log_info "Project: $PROJECT_NAME"
log_info "Build directory: $BUILD_DIR"

# Create build directory
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

################################################################################
# Generate TCL Scripts
################################################################################

# Build CFLAGS with include directories
CFLAGS=""
for inc in "${INCLUDE_DIRS[@]}"; do
    CFLAGS+=" -I$(cd ../../.. && pwd)/$inc"
done
if [[ -n "$EXTRA_CFLAGS" ]]; then
    CFLAGS+=" $EXTRA_CFLAGS"
fi

# Convert relative paths to absolute paths
ABS_SOURCE_FILES=()
for src in "${SOURCE_FILES[@]}"; do
    ABS_SOURCE_FILES+=("$(cd ../../.. && pwd)/$src")
done

ABS_TB_FILES=()
for tb in "${TESTBENCH_FILES[@]}"; do
    ABS_TB_FILES+=("$(cd ../../.. && pwd)/$tb")
done

# --- Generate project.tcl ---
cat > project.tcl << 'EOF_PROJECT'
# project.tcl - HLS Project Setup

set project_dir   "[pwd]"
set project_name  "PROJECT_NAME_PLACEHOLDER"
set top_function  "TOP_FUNCTION_PLACEHOLDER"
set part_name     "PART_NAME_PLACEHOLDER"
set clock_period  CLOCK_PERIOD_PLACEHOLDER

puts "=========================================="
puts "  PROJECT SETUP - $project_name"
puts "=========================================="

# Create project
open_project -reset $project_dir
set_top $top_function

# Create solution
open_solution -reset "solution1" -flow_target vivado
set_part $part_name
create_clock -period $clock_period -name default

# Add source files
puts "Adding source files..."
SOURCE_FILES_PLACEHOLDER

# Add testbench files
puts "Adding testbench files..."
TB_FILES_PLACEHOLDER

# Set compiler flags
puts "Setting compiler flags..."
CFLAGS_PLACEHOLDER

puts "Project setup complete!"
puts "=========================================="
EOF_PROJECT

# Replace placeholders
sed -i "s|PROJECT_NAME_PLACEHOLDER|$PROJECT_NAME|g" project.tcl
sed -i "s|TOP_FUNCTION_PLACEHOLDER|$TOP_FUNCTION|g" project.tcl
sed -i "s|PART_NAME_PLACEHOLDER|$PART_NAME|g" project.tcl
sed -i "s|CLOCK_PERIOD_PLACEHOLDER|$CLOCK_PERIOD|g" project.tcl

# Build source files list for TCL
SOURCE_LIST=""
for src in "${ABS_SOURCE_FILES[@]}"; do
    SOURCE_LIST+="add_files \"$src\"\n"
done
sed -i "s|SOURCE_FILES_PLACEHOLDER|$SOURCE_LIST|g" project.tcl

# Build testbench files list for TCL
TB_LIST=""
for tb in "${ABS_TB_FILES[@]}"; do
    TB_LIST+="add_files -tb \"$tb\"\n"
done
sed -i "s|TB_FILES_PLACEHOLDER|$TB_LIST|g" project.tcl

# Build cflags for TCL
CFLAGS_CMD="config_compile -name_max_length 256 -pipeline_loops 0"
if [[ -n "$CFLAGS" ]]; then
    CFLAGS_CMD="config_compile -name_max_length 256 -pipeline_loops 0 -cflags \"$CFLAGS\""
fi
sed -i "s|CFLAGS_PLACEHOLDER|$CFLAGS_CMD|g" project.tcl

# --- Generate csim.tcl ---
cat > csim.tcl << 'EOF_CSIM'
# csim.tcl - C Simulation

open_project [pwd]
open_solution "solution1"

puts "=========================================="
puts "  C SIMULATION"
puts "=========================================="

csim_design -clean

puts "C simulation complete!"
EOF_CSIM

# --- Generate synth.tcl ---
cat > synth.tcl << 'EOF_SYNTH'
# synth.tcl - Synthesis

open_project [pwd]
open_solution "solution1"

puts "=========================================="
puts "  SYNTHESIS"
puts "=========================================="

csynth_design

puts "Synthesis complete!"
puts "Report: solution1/syn/report/"
EOF_SYNTH

# --- Generate cosim.tcl ---
cat > cosim.tcl << 'EOF_COSIM'
# cosim.tcl - Co-Simulation

open_project [pwd]
open_solution "solution1"

puts "=========================================="
puts "  CO-SIMULATION"
puts "=========================================="

cosim_design -trace_level all -wave_debug

puts "Co-simulation complete!"
puts "Waveform: solution1/sim/verilog/"
EOF_COSIM

# --- Generate export.tcl ---
cat > export.tcl << 'EOF_EXPORT'
# export.tcl - IP Export

open_project [pwd]
open_solution "solution1"

puts "=========================================="
puts "  IP EXPORT"
puts "=========================================="

# Determine flow based on SYNTH_FLOW variable
set flow "SYNTH_FLOW_PLACEHOLDER"

if {$flow eq "impl"} {
    puts "Exporting with full implementation (synth + place & route)..."
    export_design -flow impl -rtl verilog -format ip_catalog
} elseif {$flow eq "syn"} {
    puts "Exporting with RTL synthesis..."
    export_design -flow syn -rtl verilog -format ip_catalog
} else {
    puts "Exporting IP without synthesis..."
    export_design -rtl verilog -format ip_catalog
}

puts "IP export complete!"
puts "Package: solution1/impl/ip/"
EOF_EXPORT

sed -i "s|SYNTH_FLOW_PLACEHOLDER|$SYNTH_FLOW|g" export.tcl

################################################################################
# Run HLS Steps
################################################################################

# Always create/update project first
log_info "Setting up HLS project..."
$VITIS_HLS -f project.tcl 2>&1 | tee project.log
if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
    log_error "Project setup failed. Check project.log"
    exit 1
fi
log_success "Project setup complete"

# Run C Simulation
if [[ $RUN_CSIM -eq 1 ]]; then
    log_info "Running C simulation..."
    $VITIS_HLS -f csim.tcl 2>&1 | tee csim.log
    if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
        log_error "C simulation failed. Check csim.log"
        exit 1
    fi
    log_success "C simulation passed"
fi

# Run Synthesis
if [[ $RUN_SYNTH -eq 1 ]]; then
    log_info "Running synthesis..."
    $VITIS_HLS -f synth.tcl 2>&1 | tee synth.log
    if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
        log_error "Synthesis failed. Check synth.log"
        exit 1
    fi
    log_success "Synthesis complete"
    
    # Show timing report if available
    if [[ -f "solution1/syn/report/${TOP_FUNCTION}_csynth.rpt" ]]; then
        log_info "Synthesis report:"
        grep -A 20 "== Timing ==" "solution1/syn/report/${TOP_FUNCTION}_csynth.rpt" || true
    fi
fi

# Run Co-Simulation
if [[ $RUN_COSIM -eq 1 ]]; then
    if [[ ! -d "solution1/syn" ]]; then
        log_error "Co-simulation requires synthesis to be run first"
        exit 1
    fi
    
    log_info "Running co-simulation (this may take a while)..."
    $VITIS_HLS -f cosim.tcl 2>&1 | tee cosim.log
    if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
        log_error "Co-simulation failed. Check cosim.log"
        exit 1
    fi
    log_success "Co-simulation passed"
fi

# Export IP
if [[ $RUN_EXPORT -eq 1 ]]; then
    if [[ ! -d "solution1/syn" ]]; then
        log_error "IP export requires synthesis to be run first"
        exit 1
    fi
    
    log_info "Exporting IP package (flow: $SYNTH_FLOW)..."
    $VITIS_HLS -f export.tcl 2>&1 | tee export.log
    if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
        log_error "IP export failed. Check export.log"
        exit 1
    fi
    log_success "IP export complete"
    
    if [[ -d "solution1/impl/ip" ]]; then
        log_info "IP package location: $BUILD_DIR/solution1/impl/ip"
    fi
fi

log_success "All requested steps completed successfully!"
log_info "Build directory: $BUILD_DIR"
