# Golden-Path Tutorial

This tutorial walks the complete path from a fresh install to a passing
simulation, using [`plugins/passthrough_demo/`](rtl-example.md) as the
running example — it's the simplest real plugin in the repository: one
RTL module, one instance, no HLS step. Every command below is copied from
either `plugins/passthrough_demo/README.md` or the live `--help` output of
the command itself (`forge --help`, `forge topgen gen-top --help`,
`forge verify generate --help`, `forge verify run --help`), so nothing
here is aspirational.

If you haven't installed FORGE yet, do that first:
[installation](../getting-started/installation.md).

## 1. Install

```bash
pip install -e forge/
```

## 2. Validate the topology

```bash
forge topgen validate plugins/passthrough_demo/forge/designs/design.yml
```

`passthrough_demo`'s `design.yml` declares one module instance (`pt`, a
single `passthrough` RTL module) with both its data ports
(`data_in`/`data_in_valid` in, `data_out`/`data_out_valid` out) exposed
directly at the top level — no connections, no topology groups. `validate`
checks the file's shape without generating anything.

## 3. Scaffold your own plugin (optional, if starting from scratch)

If you're not using an existing plugin, scaffold the topology side first:

```bash
forge topgen init-plugin <plugin_id>
```

This is the `topgen` counterpart to `forge verify init-plugin` (covered in
the [quickstart](../getting-started/quickstart.md)) — it writes a starter
`modules.yml`, `design.yml`, an interface contract, and an RTL stub. See
[Authoring topology contracts](../how-to/author-topology-contracts.md) for
what to put in each file.

## 4. Generate the algorithm top level

```bash
forge topgen gen-top plugins/passthrough_demo/forge/designs/design.yml \
  --mode verilog \
  --consumer-root . \
  --contracts-from plugins/passthrough_demo/forge/modules.yml \
  --build-dir build_passthrough_demo \
  --hls-build-root build_hls_passthrough_demo \
  --output gen-top/design_passthrough_demo/algo_top.v
```

`--mode verilog` selects structural Verilog generation (the other modes
are `vhdl` and `bd` for a Vivado Block Design TCL script).
`--contracts-from` points at `modules.yml`'s `interface_contract:` entries
and enables contract-driven port wiring instead of best-effort heuristic
matching. `--hls-build-root` is still required by the CLI even though
`passthrough_demo` has no HLS modules — it's simply unused for this
plugin. Add `--strict` once your topology is stable: it fails the build
if any module lacks a contract, any connection falls back to auto-match
or `port_map_ranges`, a declared cardinality is violated, or a connection
crosses clock/reset domains without a declared `cdc:` adapter (see
[clock and reset domains](../concepts/clock-and-reset-domains.md)).

## 5. Generate the verification flow

```bash
forge verify generate plugins/passthrough_demo/forge/verify/design.verification.yml
python3 plugins/passthrough_demo/forge/verify/tools/gen_stimulus.py --flow passthrough_xsim --event-id 0
```

`forge verify generate` reads `design.verification.yml` and writes
`verify.flow.yml`, the generated testbench, and `wave.tcl` into each
declared flow's directory (`passthrough_xsim/` here — `passthrough_demo`
declares exactly one flow, `full_chip_rtl` on the `xsim` backend). It's
idempotent, safe to re-run after any topology or contract change.
`gen_stimulus.py` is plugin-owned, not framework-generated — it writes
`stimulus_current.svh` for the chosen golden event. Both of these outputs
are framework/plugin *generated*: don't hand-edit them; re-run these two
commands instead.

## 6. Health-check before running

```bash
forge verify doctor plugins/passthrough_demo/forge/verify/design.verification.yml
```

At this point `doctor` should report clean — everything `verify generate`
and `gen_stimulus.py` needed to produce now exists.

## 7. Run the simulation

```bash
forge verify run plugins/passthrough_demo/forge/verify/passthrough_xsim/verify.flow.yml \
  --plugin passthrough_demo --consumer-root .
```

This requires Vivado `xsim` on `PATH` — there is no `csim` step for
`passthrough_demo` since it has no HLS module to synthesize. `--plugin`
tells the framework which plugin's `bootstrap.py` to load (only needed
when `flow.plugin` isn't already set in the flow YAML). Useful flags for
iterating: `--event-id <n>` to pick a specific golden event,
`--all-events` to run every event in one simulation, and `--probe-log` to
capture Tier 2 probe CSV data.

A passing run means the RTL's registered one-cycle passthrough matched
both golden events (`data_in=0x3A,valid=1` and the idle
`data_in=0x00,valid=0` case) exactly.

## Next

- [The RTL-only example](rtl-example.md) — a closer look at
  `passthrough_demo`'s topology.
- [The mixed HLS/RTL example](mixed-hls-rtl-example.md) — the same
  pipeline for a real multi-module, HLS+RTL plugin (`trigger_demo`).
- [Authoring topology contracts](../how-to/author-topology-contracts.md)
  — the full `design.yml`/`modules.yml`/interface-contract reference.
- [Using the visual explorer](../how-to/use-visual-explorer.md) — inspect
  the topology you just generated as an interactive graph.
