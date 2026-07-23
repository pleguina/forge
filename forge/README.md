# forge

`forge` is the unified CLI for the FORGE framework — topology generation,
HLS orchestration, and verification.

## Inputs

- a plugin design YAML such as `plugins/<plugin>/forge/designs/design.yml`
- unpacked IP metadata under `ips/`
- `ip_info.yaml` generated from the same design and IP set

## Main commands

```bash
forge topgen ip-summary plugins/<plugin>/forge/designs/design.yml \
  --ip-root ips \
  --output out/<design>/ip_info.yaml

forge topgen gen-top plugins/<plugin>/forge/designs/design.yml \
  --mode verilog \
  --ip-root ips \
  --ip-info out/<design>/ip_info.yaml \
  --output out/<design>/algo_top.v

forge topgen clean plugins/<plugin>/forge/designs/design.yml \
  --output out/<design>/algo_top.v
```

## Generated outputs

A normal `gen-top` run emits:

- `algo_top.v`
- `build_manifest.json`
- `design_parameters.json`
- `port_map.yaml`
- `probe_map.yaml`
- `tb_bindings.svh`
- optional generated testbench scaffolding when `--gen-testbench` is used

`forge topgen clean` removes those generated DUT artifacts for the selected design
and, unless `--no-verify` is passed, also removes framework-generated verification
artifacts under the sibling `forge/verify/` tree such as `verify.flow.yml`, `tb_*.sv`,
`wave.tcl`, `stimulus_current.svh`, `stimulus/`, and `xsim_work/`. It does not
remove `ips/`, `build_hls/`, or `build_hls_trigger_demo/`.

## Examples

For a supported in-repo consumer example, prefer:

- `plugins/trigger_demo/`

## CLI error handling

`topgen` now follows the same user-facing rule as the verification CLI: expected user errors should be concise and actionable by default, not raw Python tracebacks.

Typical failures such as a missing `design.yml`, invalid registry input, or generation validation problems should print:

- a short error headline
- the concrete failure message
- a suggested next step

Example:

```text
❌ Design file not found: /path/to/design.yml
  → Check the design.yml path and re-run topgen clean.
```

Use `--debug` if you want traceback details for unexpected failures:

```bash
forge --debug topgen clean plugins/<plugin>/forge/designs/design.yml --output out/<design>/algo_top.v
```

This is the intended support model for framework consumers: concise guidance by default, traceback output only on explicit debug opt-in.
