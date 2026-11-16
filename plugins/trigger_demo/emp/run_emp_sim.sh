#!/usr/bin/env bash
# Build trigger_demo with FORGE, wrap it in an EMP payload and play the golden
# events through emp-fwk's testbench in XSim.
#
#   EMP_FWK=<emp-fwk checkout> IPBUS_FW=<ipbus-firmware checkout> [EMP_DEPS="<checkout> ..."] \
#     plugins/trigger_demo/emp/run_emp_sim.sh [WORK_DIR]
#
# emp_payload.vhd is generated for the emp-fwk checkout given (0.9.x or 0.10.x).
# EMP_DEPS are further ipbb packages, each under its directory name: 0.10 needs
# cms-tcds2-firmware and tclink, at the versions its .gitlab-ci.yml names.
# Needs forge and ipbb in the same Python environment, and Vitis HLS and
# Vivado on PATH. WORK_DIR defaults to ./build_emp_trigger_demo.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../../.." && pwd)
work=$(realpath -m "${1:-build_emp_trigger_demo}")
: "${EMP_FWK:?set EMP_FWK to an emp-fwk checkout}"
: "${IPBUS_FW:?set IPBUS_FW to an ipbus-firmware checkout}"
EMP_FWK=$(realpath "$EMP_FWK")
IPBUS_FW=$(realpath "$IPBUS_FW")

plugin=$repo/plugins/trigger_demo
tools="python3 $here/tools/emp_tools.py"
mkdir -p "$work"

echo "== HLS synthesis"
forge dev hls run --registry "$plugin/forge/modules.yml" --modules all \
  --hls-build-root "$work/build_hls" --stages synth

echo "== VHDL top"
forge build "$plugin/forge/designs/design.yml" --mode vhdl --strict \
  --consumer-root "$repo" --contracts-from "$plugin/forge/modules.yml" \
  --build-dir "$work/build_hls" --output "$work/gen/algo_top.vhd"

echo "== ipbb work area"
forge dev framework ipbb-component --manifest "$work/gen/build_manifest.json" \
  --out "$work/trigger_demo_gen/algo"
forge dev framework emp-payload --spec "$here/emp_payload.yml" --emp-fwk "$EMP_FWK" \
  --ir "$work/gen/design.ir.json" --out "$work/trigger_demo_gen/payload/firmware/hdl/emp_payload.vhd"
rm -rf "$work/ipbb"
ipbb init "$work/ipbb"
# Symlinks rather than `ipbb add symlink`, which names a package after its
# directory; the dep files refer to these names.
ln -s "$EMP_FWK" "$work/ipbb/src/emp-fwk"
ln -s "$IPBUS_FW" "$work/ipbb/src/ipbus-firmware"
ln -s "$repo" "$work/ipbb/src/forge"
ln -s "$work/trigger_demo_gen" "$work/ipbb/src/trigger_demo_gen"
for dep in ${EMP_DEPS:-}; do ln -s "$(realpath "$dep")" "$work/ipbb/src/$(basename "$(realpath "$dep")")"; done
(cd "$work/ipbb" && ipbb toolbox check-dep sim forge:plugins/trigger_demo/emp top.dep)

echo "== XSim"
sim=$work/xsim
rm -rf "$sim" && mkdir -p "$sim"
forge dev framework ipbb-xsim --src-dir "$work/ipbb/src" --component forge:plugins/trigger_demo/emp \
  --dep top.dep --out "$sim/compile.sh"
frames=$($tools source --out "$sim/source.txt")
(
  cd "$sim"
  bash compile.sh > compile.log
  xelab -L extras xil_defaultlib.top -s tb \
    -generic_top "sourcefile=source.txt" -generic_top "sinkfile=sink.txt" \
    -generic_top "playlen=$frames" -generic_top "caplen=$((frames + 32))" > elab.log
  printf 'run 5us\nquit\n' > run.tcl
  xsim tb -t run.tcl > sim.log
)
$tools check --sink "$sim/sink.txt"
