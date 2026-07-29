# RTL Example: passthrough_demo

`plugins/passthrough_demo/` is the minimal FORGE reference plugin: one RTL
module, one clock cycle of registered passthrough, no HLS dependency, and
no algorithm-specific vocabulary anywhere in it. It exists to prove the
full FORGE pipeline — topology generation, contract wiring, verification
flow generation, and real `xsim` simulation — end to end for the smallest
possible generic consumer. For a richer, multi-module HLS+RTL example, see
[the mixed HLS/RTL tutorial](mixed-hls-rtl-example.md) (`trigger_demo`).

## What it does

The entire algorithm, `algo/rtl/passthrough.v`, is a parameterized
(`WIDTH`, default 8) registered passthrough:

```
data_in, data_in_valid ──[registered 1 cycle]──> data_out, data_out_valid
```

On reset (`ap_rst` active-high) both outputs clear to zero; otherwise each
clock edge (`ap_clk`) copies `data_in`/`data_in_valid` into
`data_out`/`data_out_valid` one cycle later. That's the whole design.

## Topology

`forge/modules.yml` registers exactly one module:

```yaml
modules:
- name: passthrough
  kind: rtl
  rtl_lang: verilog
  top: passthrough
  latency_hint: 1
  interface_contract: interfaces/passthrough.interface.yaml
  src: [../algo/rtl/passthrough.v]
```

`forge/designs/design.yml` declares exactly one instance of it, with both
data ports exposed directly at the top level — no connections, no
topology groups, the minimal case:

```yaml
modules:
- name: pt
  ref: passthrough
  instances: 1
  external_in_ports: [data_in, data_in_valid]
  external_out_ports: [data_out, data_out_valid]
```

## Interface contract

`forge/interfaces/passthrough.interface.yaml` declares the module's roles:
`clock_primary` (`ap_clk`), `reset_primary` (`ap_rst`, active-high), and
four plain scalar data roles (`data_in`, `data_in_valid`, `data_out`,
`data_out_valid`) — no arrays, no `wiring_kind`, no cross-module wiring
patterns. This is deliberately the simplest possible contract; see
`plugins/trigger_demo/CANONICAL_PATTERNS.md` (summarized in
[the mixed HLS/RTL tutorial](mixed-hls-rtl-example.md)) for the richer
wiring patterns (`gather`, `instance_assign`, `contract_wiring`,
`port_map`) this plugin doesn't need.

## Verification

`forge/verify/design.verification.yml` declares one golden dataset
(`passthrough_demo_golden`, XML-backed, 2 events) and, as of the current
contract, **four** verification flows over the same generated DUT — all
`full_chip_rtl` (there's only one module, so it *is* the whole design):
`passthrough_xsim` (the primary, inline-stimulus `xsim` flow), a
`verilator` backend of the same DUT/testbench, and `readmemh`-stimulus
variants of both. This proves `full_chip_rtl` genuinely supports multiple
backends and stimulus mechanisms, not just one hardcoded path. The
[golden-path tutorial](golden-path.md) walks the `passthrough_xsim` flow
specifically, since it's the one requiring only `xsim` (already assumed
available in that tutorial).

## Golden events

| Event id | data_in | data_in_valid | Note |
|----------|---------|----------------|------|
| 0 | 0x3A | 1 | normal pass-through |
| 1 | 0x00 | 0 | idle/no input |

Both were verified against a real Vivado xsim run, per the plugin's own
README, not just statically checked.

## Layout

```
plugins/passthrough_demo/
├── algo/rtl/passthrough.v                    ← the entire "algorithm"
├── forge/
│   ├── modules.yml                           ← authored: 1 module, kind=rtl
│   ├── designs/design.yml                    ← authored: 1 instance, no connections
│   ├── interfaces/passthrough.interface.yaml ← authored: port role contract
│   └── verify/
│       ├── design.verification.yml           ← authored: verification flows
│       ├── schemas/data/passthrough_demo_golden.xml  ← authored: golden dataset
│       ├── tools/bootstrap.py                ← authored: plugin registration
│       ├── tools/gen_stimulus.py             ← authored: xsim stimulus generator
│       ├── tests/                            ← authored: contract unit tests
│       └── passthrough_xsim/                 ← generated: verify.flow.yml, tb, wave.tcl
└── README.md
```

For the full run-it-yourself command sequence, see the
[golden-path tutorial](golden-path.md), which uses this exact plugin as
its running example.
