# arc

`arc` is the unified CLI for the ARC framework — topology generation,
HLS orchestration, and verification.

## Inputs

- a plugin design YAML such as `plugins/<plugin>/designs/design.yml`
- unpacked IP metadata under `ips/`
- `ip_info.yaml` generated from the same design and IP set

## Main commands

```bash
arc topgen ip-summary plugins/<plugin>/designs/design.yml \
  --ip-root ips \
  --output out/<design>/ip_info.yaml

arc topgen gen-top plugins/<plugin>/designs/design.yml \
  --mode verilog \
  --ip-root ips \
  --ip-info out/<design>/ip_info.yaml \
  --output out/<design>/algo_top.v

arc topgen clean plugins/<plugin>/designs/design.yml \
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

`topgen clean` removes those generated DUT artifacts for the selected design
and, unless `--no-verify` is passed, also removes framework-generated verification
artifacts under the sibling `verify/` tree such as `verify.flow.yml`, `tb_*.sv`,
`wave.tcl`, `stimulus_current.svh`, `stimulus/`, and `xsim_work/`. It does not
remove `ips/`, `build_hls/`, or `build_hls_trigger_demo/`.

## Examples

The `examples/` directory contains low-level schema samples for `topgen` itself.

Those samples are useful for format-oriented experimentation, but they are not the maintained proof-consumer story for the standalone framework surface. They may demonstrate legacy or compatibility-era constructs that are broader than the current Topology A support path.

For a supported in-repo consumer example, prefer:

- `plugins/trigger_demo/`

Use `framework/topgen/examples/` only when you specifically want a small tool-local sample divorced from the full framework consumer contract.

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
arc --debug topgen clean plugins/<plugin>/designs/design.yml --output out/<design>/algo_top.v
```

This is the intended support model for framework consumers: concise guidance by default, traceback output only on explicit debug opt-in.
