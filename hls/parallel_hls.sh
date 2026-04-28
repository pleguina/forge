#!/bin/bash
################################################################################
# parallel_hls.sh - Parallel HLS Job Runner
#
# Description:
#   Runs HLS operations (csim, synth, cosim, export) on multiple modules in
#   parallel with a configurable number of parallel jobs.
#
#   NEW FEATURE:
#   - Skips work if the requested step is already complete for a module
#     (e.g. don't re-synth/export something that's already exported).
#
# Usage:
#   ./parallel_hls.sh -j <num_jobs> -c <command> -m "module1 module2 ..."
#   ./parallel_hls.sh -j 4 -c synth -m "module_a module_b module_c"
#   ./parallel_hls.sh -j 2 -c export -m "all"
#
################################################################################

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory (where parallel_hls.sh lives)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER_CWD="$PWD"
BUILD_HLS_DIR="${BUILD_HLS_DIR:-}"
IP_PACK_DIR="${IP_PACK_DIR:-}"
HLS_CONFIG="${HLS_CONFIG:-${HLS_CONFIG_FILE:-}}"



# Default values
NUM_JOBS=1
HLS_COMMAND=""
MODULES=""
VITIS_HLS="vitis_hls"
DESIGN_FILE=""
SYNTH_FLOW="export"  # Can be: export (no synth), syn (RTL synthesis), impl (synth + place & route)

all_modules() {
    if [[ -z "$HLS_CONFIG" ]]; then
        echo "<provide -p <plugin-hls-config.yaml> to list modules>"
        return 0
    fi
    python3 "$SCRIPT_DIR/generate_hls_tcl.py" --config-file "$HLS_CONFIG" --list-modules
}

# Job tracking
declare -A JOB_PIDS
declare -A JOB_STATUS
declare -A JOB_MODULE
JOBS_RUNNING=0
JOBS_COMPLETED=0
JOBS_FAILED=0
TOTAL_JOBS=0

################################################################################
# Functions
################################################################################

usage() {
    local exit_code="${1:-1}"
    cat << EOF
Usage: $0 -j <num_jobs> -c <command> -m "module1 module2 ..." [options]

Options:
  -j NUM      Number of parallel jobs (default: 1)
  -c CMD      HLS command: csim, synth, cosim, export (required)
  -m MODULES  Space-separated list of modules, "all", or "design" (required)
    -d FILE     Design YAML file to extract modules from (used with -m design)
    -f FLOW     Synthesis flow for export: export (default), syn, or impl
    -p FILE     Plugin HLS config file (required unless HLS_CONFIG or HLS_CONFIG_FILE is set)
        -b DIR      HLS build output directory (required unless BUILD_HLS_DIR is set)
        -i DIR      Exported IP package directory (required unless IP_PACK_DIR is set)
  -v PATH     Vitis HLS executable path (default: vitis_hls)
  -h          Show this help message

Module selection:
  -m "module1 module2"  Build specific modules
  -m "all"              Build all available modules
  -m "design"           Build only HLS modules from design file (requires -d)

Flow options (-f):
  export     Export IP only, no synthesis (fastest, no QoR data)
  syn        Export with RTL synthesis (accurate timing, faster than impl)
  impl       Export with synthesis + place & route (most accurate QoR, slowest)

Available modules:
    $(all_modules)

Examples:
  # Export modules from design file (no implementation)
    $0 -j 4 -c synth -m design -d plugins/example/design.yml -p plugins/example/hls_config.yaml
    $0 -j 4 -c export -m design -d plugins/example/design.yml -p plugins/example/hls_config.yaml

  # Export specific modules with 2 jobs
  $0 -j 2 -c export -m "module_a module_b"

  # Synthesize all modules with 4 parallel jobs
  $0 -j 4 -c synth -m "all"

EOF
    exit "$exit_code"
}

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

get_modules_from_design() {
    local design_file="$1"
    
    if [[ ! -f "$design_file" ]]; then
        log_error "Design file not found: $design_file"
        exit 1
    fi
    
    # Don't log here - it pollutes the module list output
    # log_info "Extracting HLS modules from design file: $design_file"
    
    python3 "$SCRIPT_DIR/generate_hls_tcl.py" \
        --config-file "$HLS_CONFIG" \
        --resolve-design-modules "$design_file"
}

# Map command -> TCL script
get_tcl_script() {
    local cmd=$1
    case $cmd in
        csim)   echo "csim.tcl" ;;
        synth)  echo "synth.tcl" ;;
        cosim)  echo "cosim.tcl" ;;
        export) echo "ip_export.tcl" ;;
        *)      echo "" ;;
    esac
}

# Map command -> log filename
get_log_name() {
    local cmd=$1
    case $cmd in
        csim)   echo "csim.log" ;;
        synth)  echo "synth.log" ;;
        cosim)  echo "cosim.log" ;;
        export) echo "ip_export.log" ;;
        *)      echo "unknown.log" ;;
    esac
}

# Check vitis_hls exists
check_vitis_hls() {
    if ! command -v "$VITIS_HLS" &> /dev/null; then
        log_error "Vitis HLS not found: $VITIS_HLS"
        log_error "Please ensure Vitis HLS is in your PATH or use -v to specify the path"
        log_error "You may need to source Vitis HLS settings first:"
        log_error "  source /tools/Xilinx/Vitis_HLS/<version>/settings64.sh"
        exit 1
    fi
    log_info "Using Vitis HLS: $(command -v "$VITIS_HLS")"
}

# Validate module dir exists
validate_module() {
    local module=$1
    if [[ ! -d "$BUILD_HLS_DIR/$module" ]]; then
        log_warning "Module directory not found: $BUILD_HLS_DIR/$module"
        log_warning "Run 'make -f Makefile.hls hls_setup_$module' first"
        return 1
    fi
    return 0
}

# ---- Project / completion detectors -----------------------------------------

# Does solution1 exist (project initialized)?
is_project_initialized() {
    local module=$1
    local project_dir="$BUILD_HLS_DIR/$module"

    # A Vitis HLS project after running project.tcl typically creates:
    #   solution1/solution1.aps
    if [[ -d "$project_dir/solution1" && -f "$project_dir/solution1/solution1.aps" ]]; then
        return 0
    fi
    return 1
}

# Has synthesis finished?
# We consider it done if we can find a csynth.rpt for the top function.
is_synthesis_complete() {
    local module=$1
    local synth_dir="$BUILD_HLS_DIR/$module/solution1/syn"

    if [[ -d "$synth_dir" && -d "$synth_dir/report" ]]; then
        # any *csynth.rpt existing is a good proxy for "synth succeeded"
        if ls "$synth_dir/report"/*csynth.rpt >/dev/null 2>&1; then
            return 0
        fi
    fi
    return 1
}

# Has C simulation completed?
# This is fuzzy because everyone writes csim differently.
# We'll use 2 heuristics:
#   1. existence of solution1/csim/build or solution1/csim/report
#   2. log file says "PASS" (case-insensitive) if present
is_csim_complete() {
    local module=$1
    local mod_dir="$BUILD_HLS_DIR/$module"
    local log_file="$mod_dir/logs/csim.log"

    # Heuristic dirs Vitis HLS tends to create:
    #   solution1/csim/build/
    #   solution1/csim/report/
    if [[ -d "$mod_dir/solution1/csim" ]]; then
        # optional: inspect log for PASS
        if [[ -f "$log_file" ]]; then
            if grep -qi "PASS" "$log_file"; then
                return 0
            fi
        else
            # no log, but csim dir exists => assume done
            return 0
        fi
    fi
    return 1
}

# Has co-simulation completed?
# Similar trick: look for cosim/report and maybe "PASS" in cosim.log
is_cosim_complete() {
    local module=$1
    local mod_dir="$BUILD_HLS_DIR/$module"
    local log_file="$mod_dir/logs/cosim.log"

    if [[ -d "$mod_dir/solution1/sim" || -d "$mod_dir/solution1/cosim" ]]; then
        if [[ -f "$log_file" ]]; then
            if grep -qi "PASS" "$log_file"; then
                return 0
            fi
        else
            # if cosim artifacts exist but no log, we'll be conservative and NOT claim success
            return 1
        fi
    fi
    return 1
}

# Has export completed?
# We'll say export is "complete" if ip_packages/<module> exists and is non-empty.
is_export_complete() {
    local module=$1

    # 1. Case: module exported into its own folder
    local mod_dir="${IP_PACK_DIR}/${module}"
    if [[ -d "$mod_dir" ]]; then
        if [[ -n "$(ls -A "$mod_dir" 2>/dev/null)" ]]; then
            return 0
        fi
    fi

    # 2. Case: module exported as a zip file
    #    Typical names we've seen: csc_interface_ip.zip
    #    We'll accept "<module>*.zip"
    local zips=( "${IP_PACK_DIR}/${module}"*.zip )

    # Bash trick: if the pattern didn't match, you'll literally get the pattern string back,
    # so we also check -e to confirm file exists.
    for z in "${zips[@]}"; do
        if [[ -f "$z" ]]; then
            return 0
        fi
    done

    # If neither directory nor zip exists -> not exported
    return 1
}

# Generic "is step complete?" front-end
is_step_complete() {
    local module=$1
    local command=$2

    case "$command" in
        csim)
            is_csim_complete "$module"
            return $?
            ;;
        synth)
            is_synthesis_complete "$module"
            return $?
            ;;
        cosim)
            is_cosim_complete "$module"
            return $?
            ;;
        export)
            # export is complete if export artifacts exist.
            # Note: We do NOT re-check synth here,
            # because if export artifacts already exist we don't care.
            is_export_complete "$module"
            return $?
            ;;
        *)
            # Unknown command -> not complete
            return 1
            ;;
    esac
}

# -----------------------------------------------------------------------------

# Run HLS command for a module (with skip logic)
run_hls_job() {
    local module=$1
    local command=$2
    local tcl_script
    local log_file

    tcl_script=$(get_tcl_script "$command")
    log_file=$(get_log_name "$command")

    local module_dir="$BUILD_HLS_DIR/$module"
    local log_dir="$module_dir/logs"
    local full_log_path="$log_dir/$log_file"

    # Make sure logs dir exists
    mkdir -p "$log_dir"
    mkdir -p "$IP_PACK_DIR"

    # -----------------------------------------------------------------
    # EARLY EXIT: if this step is already done, skip
    # -----------------------------------------------------------------
    if is_step_complete "$module" "$command"; then
        echo "[SKIP] $command already complete for $module" > "$full_log_path"
        return 0
    fi

    # Special handling for "export":
    # If export is NOT complete, we must ensure synth is complete first,
    # but we must NOT re-run synth if it is already done.
    if [[ "$command" == "export" ]]; then
        # 1. Ensure project initialized (needed before synth or export anyway)
        if ! is_project_initialized "$module"; then
            log_info "Initializing HLS project for $module (pre-export)..."

            local proj_tcl="project.tcl"
            local proj_log="$log_dir/project.log"

            if [[ ! -f "$module_dir/$proj_tcl" ]]; then
                echo "ERROR: Project TCL not found: $module_dir/$proj_tcl" > "$full_log_path"
                echo "Run 'make -f Makefile.hls hls_setup_$module' first" >> "$full_log_path"
                return 1
            fi

            cd "$module_dir"
            "$VITIS_HLS" -f "$proj_tcl" > "$proj_log" 2>&1
            local proj_status=$?
            cd "$RUNNER_CWD"

            if [[ $proj_status -ne 0 ]]; then
                echo "ERROR: Project init failed for $module" > "$full_log_path"
                echo "Check $proj_log for details" >> "$full_log_path"
                return 1
            fi

            log_success "Project initialized for $module"
        fi

        # 2. Make sure synth is done (but don't redo if it's already done)
        if ! is_synthesis_complete "$module"; then
            log_warning "Synthesis not complete for $module, running synthesis first..."

            local synth_tcl="synth.tcl"
            local synth_log="$log_dir/synth.log"

            if [[ ! -f "$module_dir/$synth_tcl" ]]; then
                echo "ERROR: Synthesis TCL not found: $module_dir/$synth_tcl" > "$full_log_path"
                return 1
            fi

            cd "$module_dir"
            "$VITIS_HLS" -f "$synth_tcl" > "$synth_log" 2>&1
            local synth_status=$?
            cd "$RUNNER_CWD"

            if [[ $synth_status -ne 0 ]]; then
                echo "ERROR: Synthesis failed for $module" > "$full_log_path"
                echo "Check $synth_log for details" >> "$full_log_path"
                return 1
            fi

            log_success "Synthesis completed for $module (pre-export)"
        fi

        # At this point synth is guaranteed complete.
        # We fall through to actually run export.tcl.
    else
        # For non-export commands (csim/synth/cosim):
        # Make sure the project exists before running its tcl.
        if ! is_project_initialized "$module"; then
            log_info "Initializing HLS project for $module..."

            local proj_tcl="project.tcl"
            local proj_log="$log_dir/project.log"

            if [[ ! -f "$module_dir/$proj_tcl" ]]; then
                echo "ERROR: Project TCL not found: $module_dir/$proj_tcl" > "$full_log_path"
                echo "Run 'make -f Makefile.hls hls_setup_$module' first" >> "$full_log_path"
                return 1
            fi

            cd "$module_dir"
            "$VITIS_HLS" -f "$proj_tcl" > "$proj_log" 2>&1
            local proj_status=$?
            cd "$RUNNER_CWD"

            if [[ $proj_status -ne 0 ]]; then
                echo "ERROR: Project initialization failed for $module" > "$full_log_path"
                echo "Check $proj_log for details" >> "$full_log_path"
                return 1
            fi

            log_success "Project initialized for $module"
        fi
    fi

    # Now actually run the requested step .tcl
    if [[ ! -f "$module_dir/$tcl_script" ]]; then
        echo "ERROR: TCL script not found: $module_dir/$tcl_script" > "$full_log_path"
        echo "Run 'make -f Makefile.hls hls_setup_$module' first" >> "$full_log_path"
        return 1
    fi

    cd "$module_dir"
    "$VITIS_HLS" -f "$tcl_script" > "$full_log_path" 2>&1
    local status=$?
    cd "$RUNNER_CWD"

    return $status
}

# Background job wrapper
job_wrapper() {
    local module=$1
    local command=$2
    local job_id=$3

    log_info "[Job $job_id] Starting $command for $module"

    if run_hls_job "$module" "$command"; then
        log_success "[Job $job_id] Completed $command for $module"
        return 0
    else
        log_error "[Job $job_id] Failed $command for $module"
        return 1
    fi
}

# Wait for any job to complete (1 finishes => update accounting)
wait_for_job() {
    while true; do
        for pid in "${!JOB_PIDS[@]}"; do
            if ! kill -0 "$pid" 2>/dev/null; then
                wait "$pid"
                local status=$?
                local module="${JOB_MODULE[$pid]}"

                unset JOB_PIDS[$pid]
                unset JOB_MODULE[$pid]
                JOBS_RUNNING=$((JOBS_RUNNING - 1))
                JOBS_COMPLETED=$((JOBS_COMPLETED + 1))

                if [[ $status -eq 0 ]]; then
                    JOB_STATUS[$module]="SUCCESS"
                else
                    JOB_STATUS[$module]="FAILED"
                    JOBS_FAILED=$((JOBS_FAILED + 1))
                fi
                return
            fi
        done
        sleep 0.1
    done
}

# Start job in background
start_job() {
    local module=$1
    local command=$2
    local job_id=$3

    job_wrapper "$module" "$command" "$job_id" &
    local pid=$!

    JOB_PIDS[$pid]=$job_id
    JOB_MODULE[$pid]=$module
    JOBS_RUNNING=$((JOBS_RUNNING + 1))
}

# Pretty progress print
print_progress() {
    local completed=$1
    local total=$2
    local failed=$3

    echo ""
    log_info "Progress: $completed/$total completed, $failed failed, $JOBS_RUNNING running"
    echo ""
}

# Final summary
print_summary() {
    echo ""
    echo "=================================="
    echo "         Job Summary"
    echo "=================================="
    echo ""

    local success_count=0
    local failed_count=0

    for module in "${!JOB_STATUS[@]}"; do
        local status="${JOB_STATUS[$module]}"
        if [[ "$status" == "SUCCESS" ]]; then
            log_success "✓ $module"
            success_count=$((success_count + 1))
        else
            log_error "✗ $module"
            failed_count=$((failed_count + 1))
        fi
    done

    echo ""
    echo "=================================="
    log_info "Total: $TOTAL_JOBS"
    log_success "Successful (including skipped-as-done): $success_count"
    if [[ $failed_count -gt 0 ]]; then
        log_error "Failed: $failed_count"
    else
        log_info "Failed: $failed_count"
    fi
    echo "=================================="
    echo ""

    if [[ $failed_count -gt 0 ]]; then
        log_error "Some jobs failed. Check logs in $BUILD_HLS_DIR/<module>/logs/"
        return 1
    else
        log_success "All jobs completed successfully!"
        return 0
    fi
}

################################################################################
# Main
################################################################################

# Parse arguments
while getopts "j:c:m:v:d:f:p:b:i:h" opt; do
    case $opt in
        j) NUM_JOBS="$OPTARG" ;;
        c) HLS_COMMAND="$OPTARG" ;;
        m) MODULES="$OPTARG" ;;
        v) VITIS_HLS="$OPTARG" ;;
        d) DESIGN_FILE="$OPTARG" ;;
        f) SYNTH_FLOW="$OPTARG" ;;
        p) HLS_CONFIG="$OPTARG" ;;
        b) BUILD_HLS_DIR="$OPTARG" ;;
        i) IP_PACK_DIR="$OPTARG" ;;
        h) usage 0 ;;
        *) usage ;;
    esac
done

resolve_abs() {
    python3 - <<'PY' "$1"
from pathlib import Path
import sys
print(Path(sys.argv[1]).expanduser().resolve())
PY
}

# Validate required arguments
if [[ -z "$HLS_COMMAND" || -z "$MODULES" ]]; then
    log_error "Missing required arguments"
    usage
fi

if [[ -z "$HLS_CONFIG" ]]; then
    log_error "Missing HLS config file"
    log_error "Pass -p <plugin hls config> or set HLS_CONFIG / HLS_CONFIG_FILE"
    exit 1
fi

if [[ -z "$BUILD_HLS_DIR" ]]; then
    log_error "Missing HLS build directory"
    log_error "Pass -b <build_hls dir> or set BUILD_HLS_DIR"
    exit 1
fi

if [[ -z "$IP_PACK_DIR" ]]; then
    log_error "Missing IP package directory"
    log_error "Pass -i <ip_packages dir> or set IP_PACK_DIR"
    exit 1
fi

BUILD_HLS_DIR="$(resolve_abs "$BUILD_HLS_DIR")"
IP_PACK_DIR="$(resolve_abs "$IP_PACK_DIR")"

# Validate flow option
case $SYNTH_FLOW in
    export|syn|impl) ;;
    *)
        log_error "Invalid flow: $SYNTH_FLOW"
        log_error "Valid flows: export, syn, impl"
        exit 1
        ;;
esac

# Validate command
case $HLS_COMMAND in
    csim|synth|cosim|export) ;;
    *)
        log_error "Invalid command: $HLS_COMMAND"
        log_error "Valid commands: csim, synth, cosim, export"
        exit 1
        ;;
esac

# Validate number of jobs
if ! [[ "$NUM_JOBS" =~ ^[0-9]+$ ]] || [[ "$NUM_JOBS" -lt 1 ]]; then
    log_error "Invalid number of jobs: $NUM_JOBS"
    exit 1
fi

# Expand "all" or "design"
if [[ "$MODULES" == "all" ]]; then
    MODULES="$(all_modules)"
elif [[ "$MODULES" == "design" ]]; then
    if [[ -z "$DESIGN_FILE" ]]; then
        log_error "Design file required when using -m design (use -d option)"
        exit 1
    fi
    MODULES=$(get_modules_from_design "$DESIGN_FILE")
    if [[ -z "$MODULES" ]]; then
        log_error "No HLS modules found in design file"
        exit 1
    fi
    log_success "Found HLS modules: $MODULES"
fi

# Convert to array
read -ra MODULE_ARRAY <<< "$MODULES"
TOTAL_JOBS=${#MODULE_ARRAY[@]}

if [[ $TOTAL_JOBS -eq 0 ]]; then
    log_error "No modules specified"
    exit 1
fi

# Print configuration
echo ""
echo "=================================="
echo "  Parallel HLS Job Runner"
echo "=================================="
log_info "Command: $HLS_COMMAND"
log_info "Parallel jobs: $NUM_JOBS"
log_info "HLS config: $HLS_CONFIG"
log_info "HLS build dir: $BUILD_HLS_DIR"
log_info "IP package dir: $IP_PACK_DIR"
log_info "Total modules: $TOTAL_JOBS"
log_info "Modules: ${MODULE_ARRAY[*]}"
echo "=================================="
echo ""

# Check vitis_hls
check_vitis_hls

# Generate TCL files for all modules (creates build directories if needed)
log_info "Generating TCL scripts for all modules..."
for module in "${MODULE_ARRAY[@]}"; do
    # Check if module directory exists, if not, always generate
    if [[ ! -d "$BUILD_HLS_DIR/$module" ]]; then
        log_info "Creating project for $module..."
    fi
    
    # Determine flow parameter for TCL generation
    if [[ "$HLS_COMMAND" == "export" || "$HLS_COMMAND" == "synth" ]]; then
        python3 "$SCRIPT_DIR/generate_hls_tcl.py" "$module" --flow "$SYNTH_FLOW" \
            --config-file "$HLS_CONFIG" \
            --template-dir "$SCRIPT_DIR/templates" --output-dir "$BUILD_HLS_DIR" --ip-packages-dir "$IP_PACK_DIR" || {
            log_error "Failed to generate TCL for $module"
            JOB_STATUS[$module]="FAILED"
            JOBS_FAILED=$((JOBS_FAILED + 1))
            continue
        }
    else
        python3 "$SCRIPT_DIR/generate_hls_tcl.py" "$module" \
            --config-file "$HLS_CONFIG" \
            --template-dir "$SCRIPT_DIR/templates" --output-dir "$BUILD_HLS_DIR" --ip-packages-dir "$IP_PACK_DIR" || {
            log_error "Failed to generate TCL for $module"
            JOB_STATUS[$module]="FAILED"
            JOBS_FAILED=$((JOBS_FAILED + 1))
            continue
        }
    fi
done
log_success "TCL scripts generated"
echo ""

# Validate module dirs (should all exist now after TCL generation)
log_info "Validating modules..."
for module in "${MODULE_ARRAY[@]}"; do
    if ! validate_module "$module"; then
        log_error "Module validation failed: $module"
        JOB_STATUS[$module]="FAILED"
        JOBS_FAILED=$((JOBS_FAILED + 1))
    fi
done
log_success "All modules validated"
echo ""

# Launch jobs
log_info "Starting job queue..."
echo ""

job_counter=0
for module in "${MODULE_ARRAY[@]}"; do
    # Skip modules that failed validation
    if [[ "${JOB_STATUS[$module]:-}" == "FAILED" ]]; then
        continue
    fi
    
    # Respect max parallelism
    while [[ $JOBS_RUNNING -ge $NUM_JOBS ]]; do
        wait_for_job
        print_progress "$JOBS_COMPLETED" "$TOTAL_JOBS" "$JOBS_FAILED"
    done

    job_counter=$((job_counter + 1))
    start_job "$module" "$HLS_COMMAND" "$job_counter"
done

# Drain remaining
log_info "Waiting for remaining jobs to complete..."
while [[ $JOBS_RUNNING -gt 0 ]]; do
    wait_for_job
    print_progress "$JOBS_COMPLETED" "$TOTAL_JOBS" "$JOBS_FAILED"
done

# Final summary
if print_summary; then
    exit 0
else
    exit 1
fi
