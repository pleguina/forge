# trigger_demo verification

This directory is a supported proof consumer for the framework verification path.

It demonstrates a larger non-OMTF plugin with multiple flows on framework-owned backends.

## What it proves

- a second plugin can use the same framework verification contract without framework-layer changes
- one plugin can declare multiple `hls_csim` and `single_module_rtl` flows in one authored verification contract
- framework-owned `csim` and `xsim` backends are reusable beyond the minimal demo

## Maintained proof surfaces

- `design.verification.yml`
- `tools/bootstrap.py`
- `tools/gen_stimulus.py`
- `tools/tests/test_trigger_demo_verify.py`
- `tools/tests/test_gen_stimulus.py`

## Current proof shape

- 4 `hls_csim` flows
- 4 `single_module_rtl` flows
- XML-backed dataset in `schemas/data/trigger_demo_golden.xml`

## Quick verification path

```bash
python3 -m pytest plugins/trigger_demo/verify/tools/tests -q
fw_verify doctor plugins/trigger_demo/verify/design.verification.yml
fw_verify generate plugins/trigger_demo/verify/design.verification.yml --flow hit_decoder_csim --dry-run
```

This consumer is part of the supported non-OMTF proof story and should remain aligned with the public framework verification contract.