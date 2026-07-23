# trigger_demo verification

This directory is the **supported proof consumer** for the framework
verification path. It demonstrates a non-OMTF plugin with 9 verification
flows across all three supported kinds: `hls_csim`, `single_module_rtl`,
and `full_chip_rtl`.

See also: [`plugins/trigger_demo/README.md`](../README.md) for the full
end-to-end workflow, and
[`docs/FRAMEWORK_CORE_INTERFACE.md`](../../../docs/FRAMEWORK_CORE_INTERFACE.md)
for the Layer 1 verification contract.

## What it proves

- A second plugin can use the same framework verification contract without
  framework-layer changes
- One plugin can declare `hls_csim`, `single_module_rtl`, and `full_chip_rtl`
  flows in a single authored verification contract
- Framework-owned `csim` and `xsim` backends are reusable beyond the minimal
  demo
- The `full_chip_rtl` kind integrates with `topgen gen-top` output for
  end-to-end integration verification

## Maintained proof surfaces (plugin-authored)

```
design.verification.yml            ← verification contract (9 flows)
schemas/data/trigger_demo_golden.xml ← XML dataset (4 golden events)
src/xml_event_reader.h             ← shared testbench header
tests/tb_*.cpp                     ← HLS C-sim testbench sources
tools/bootstrap.py                 ← arc verify plugin registration
tools/gen_stimulus.py              ← xsim stimulus generator
tools/trigger_demo_verify_env.sh   ← shell PYTHONPATH helper
tools/tests/                       ← unit tests for verify tooling
```

## Framework-generated artifacts (committed as CI baseline)

```
<flow>/verify.flow.yml      ← arc verify generate design.verification.yml
<flow>/tb_*.sv              ← arc verify generate ...
<flow>/wave.tcl             ← arc verify generate ...
<flow>/stimulus_current.svh ← gen_stimulus.py (plugin-owned generator)
```

Regenerate all:

```bash
arc verify generate plugins/trigger_demo/arc/verify/design.verification.yml
python3 plugins/trigger_demo/arc/verify/tools/gen_stimulus.py
```

## Current proof shape

- 4 `hls_csim` flows  (`hit_decoder_csim`, `hit_collector_csim`, `trigger_logic_csim`, `trigger_output_csim`)
- 4 `single_module_rtl` flows (`hit_decoder_xsim`, `hit_collector_xsim`, `trigger_logic_xsim`, `trigger_output_xsim`)
- 1 `full_chip_rtl` flow (`trigger_pipeline_xsim` — generated `algo_top` integration)
- XML-backed dataset in `schemas/data/trigger_demo_golden.xml` (4 events)

## Quick verification path

```bash
# Python unit tests (no Vivado or Vitis HLS required)
python3 -m pytest plugins/trigger_demo/arc/verify/tools/tests -q

# Contract health check
arc verify doctor plugins/trigger_demo/arc/verify/design.verification.yml

# Dry-run generate (validates contract without running simulation)
arc verify generate plugins/trigger_demo/arc/verify/design.verification.yml --dry-run

# Run a specific csim flow (requires Vitis HLS)
arc verify run plugins/trigger_demo/arc/verify/hit_decoder_csim/verify.flow.yml --plugin trigger_demo

# Run a specific xsim flow (requires Vivado / xsim)
arc verify run plugins/trigger_demo/arc/verify/hit_decoder_xsim/verify.flow.yml --plugin trigger_demo

# Full-chip integration (requires gen-top output — see main README Step 2)
arc verify run plugins/trigger_demo/arc/verify/trigger_pipeline_xsim/verify.flow.yml --plugin trigger_demo
```

This consumer is part of the supported non-OMTF proof story and must remain
aligned with the public framework verification contract. See
[`SUPPORT_CLASSIFICATION.md`](../../../docs/SUPPORT_CLASSIFICATION.md).