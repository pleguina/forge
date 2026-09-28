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

## 4b. Alternative: generate as a Block Design instead of Verilog

`--mode bd` generates the same design as a Vivado Block Design Tcl script
instead of flat structural Verilog — each module becomes a `create_bd_cell`
instance inside a real Block Design (with a proper IP-Integrator boundary
per instance) rather than a raw `add_files`-and-flatten Verilog top. It's
otherwise the same generator pipeline: the same validation, and the same
`build_manifest.json`/`port_map.yaml`/`port_signature.json`/
`design_parameters.json`/`probe_map.yaml`/`tb_bindings.svh`/
`maturity_report.json`/`design.ir.json` artifacts get written alongside it.

```bash
forge topgen gen-top plugins/passthrough_demo/forge/designs/design.yml \
  --mode bd \
  --bd-name passthrough_bd \
  --consumer-root . \
  --contracts-from plugins/passthrough_demo/forge/modules.yml \
  --build-dir build_passthrough_demo_bd \
  --hls-build-root build_hls_passthrough_demo_bd \
  --output gen-top/design_passthrough_demo_bd/block_design.tcl
```

The generated Tcl creates the BD, instantiates `passthrough_demo`'s one RTL
module, wires `ap_clk`/`ap_rst`, and creates the same four top-level ports
`--mode verilog` would (`pt_data_in`, `pt_data_in_valid`, `pt_data_out`,
`pt_data_out_valid`) — deterministically, by name, rather than leaving
Vivado to choose the names itself:

```tcl
create_bd_port -dir I -from 7 -to 0 pt_data_in
connect_bd_net [get_bd_ports pt_data_in] [get_bd_pins pt/data_in]
create_bd_port -dir I pt_data_in_valid
connect_bd_net [get_bd_ports pt_data_in_valid] [get_bd_pins pt/data_in_valid]
create_bd_port -dir O -from 7 -to 0 pt_data_out
connect_bd_net [get_bd_pins pt/data_out] [get_bd_ports pt_data_out]
create_bd_port -dir O pt_data_out_valid
connect_bd_net [get_bd_pins pt/data_out_valid] [get_bd_ports pt_data_out_valid]
```

`port_map.yaml` and `port_signature.json`'s hash from this run are
identical to `--mode verilog`'s for the same design — both modes resolve
the top-level port set through the same shared function
(`forge.generation.generators._port_resolution.resolve_top_ports`), so
there's exactly one implementation of "which pins are top-level, and what
are they named" to keep correct, not two that can silently drift apart.

Any instance input left with no driver (no connection, no external/global
declaration) is tied to a shared `xilinx.com:ip:xlconstant` IP rather than
left disconnected — a Block Design can't leave a mandatory input pin
genuinely unconnected the way a flat `.v` file's bare `.pin()` can;
`validate_bd_design` flags it.

### Sourcing the Tcl into Vivado

```tcl
create_project demo ./vivado_proj -part <your part> -force
source gen-top/design_passthrough_demo_bd/block_design.tcl
open_bd_design [get_files *.bd]
validate_bd_design
```

`block_design.tcl` itself already ends with
`make_wrapper -files [get_files *.bd] -top -inst_template -import`, so this
also produces a synthesizable HDL wrapper for the Block Design
(`passthrough_bd_wrapper.v`), the same file `generate_target`/`launch_runs`
would need downstream in a real board build.

Plain `register_stages`/`delay_cycles` connections work too: `--mode bd`
instantiates real `RegisterStage`/`signal_delay` cells (the same modules
`--mode verilog` uses) between the source and destination pins. Those
modules ship with forge itself (`forge/rtl/support/`), so a plugin
doesn't need its own copy; the generated Tcl adds them to the project
alongside the design's own RTL.

A `reset_domains.*.sync: reset_sync` domain works too: `--mode bd`
instantiates a real `cdc_reset_sync` cell per domain, clocked by that
domain's own destination clock, and connects its `sync_rst_out` straight
to each member instance's reset pin — no intermediate net to declare,
since a Block Design connects pins directly to pins. The domain's own raw
top-level port is still created but left unconnected, the same convention
`--mode verilog` uses (nothing drives it once a real synchronizer exists
for the domain). This needs `write_bd_tcl`'s `contracts`/`match_report`
parameters (forge's own `gen-top` CLI always passes them) so each
domain's member instances and their clock can be resolved.

A `cdc:` connection works too: `--mode bd` instantiates the matching real
`cdc_sync2ff`/`cdc_pulse_sync`/`cdc_mailbox`/`cdc_async_fifo` cell, wired to
each side's own resolved clock/reset — including fanning a synchronizer's
`dst_rst` from another `reset_sync` domain's own `cdc_reset_sync` cell when
the destination lives in one, not the raw domain net. `async_fifo`'s
`write_enable_pin` is honored when declared; otherwise `wr_en` ties to a
shared constant-1 cell. This needs `contracts`/`match_report` too, the
same as `reset_sync` above.

A `boundary:`-tagged delay works too: `--mode bd` instantiates a real,
protected `slr_crossing_delay` cell instead of plain `signal_delay`/
`RegisterStage` (whichever of `register_stages`/`delay_cycles` is declared
becomes its `DEPTH`), and writes a `block_design.crossings.json` manifest
alongside the Tcl — the same file `blobfish_build.slr_crossings.generate_xdc`
reads to emit real board XDC constraints for a flat-Verilog build. Its
hierarchy prefix is `f"{bd_name}_i/{instance_name}/inst"` — the extra
`/inst` level is Vivado's own convention for a `-type module -reference`
cell, confirmed against real Vivado 2024.1 synthesis (`DEPTH` 1, 2 and >2
all checked directly against a real `synth_design` run's own `get_cells`
results, not just assumed). A board building from a BD payload points its
own `payload_hier_prefix` at wherever `<bd_name>_i` ends up in its parent
hierarchy, the same way it already points at `u_algo_top` for a
flat-Verilog build — no changes needed downstream.

`--mode bd` now reaches full feature parity with `--mode verilog`: every
design.yml feature generates real logic in both modes.

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
