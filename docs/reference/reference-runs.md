# Reference runs and their evidence

Hosted CI runs everything that needs no licensed tool: the unit tests (with
Verilator), and `ci/verification_fresh_user.sh`, which adopts a plain-RTL
design, verifies it from an input and expected output alone, checks that a
wrong expected word fails, and keeps the evidence files as an artifact.

What needs Vivado and Vitis HLS runs on a machine that has them:

```bash
VIVADO_SETTINGS=/opt/Xilinx/Vivado/2024.1/settings64.sh \
VITIS_HLS_SETTINGS=/opt/Xilinx/Vitis_HLS/2024.1/settings64.sh \
EMP_FWK=emp-fwk IPBUS_FW=ipbus-firmware EMP_DEPS="cms-tcds2-firmware tclink" \
  bash ci/reference_pipelines.sh reference-evidence
```

| Output | Content |
|--------|---------|
| `versions.txt` | FORGE commit (and whether the checkout was modified), Vivado, Vitis HLS, Verilator, emp-fwk release |
| `simple_pipeline/` | `forge test --input --expected` on XSim and Verilator: evidence JSON per simulator, JUnit, logs |
| `trigger_demo_emp/` | HLS synthesis, `forge build`, the ipbb dep tree and `emp_payload` generated for the emp-fwk given, emp-fwk's testbench in XSim: the sink pattern file, the check against the golden trigger words, the log |

The script exits non-zero when any run fails and keeps what ran either way.
Run it for each emp-fwk release a change claims to support (0.9.x without
`EMP_DEPS`, 0.10.x with them) and keep the directory with the release.

Board-level runs (a payload in a real board build, playback on hardware)
belong to the board framework's own pipeline (ipbb for EMP, `blobfish_build`
for Blobfish), with the link map and build identity of
[`forge dev framework link-map`](link-map.md) saved next to their results.
