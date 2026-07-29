# Mixed HLS/RTL Example: trigger_demo

`plugins/trigger_demo/` is FORGE's canonical reference plugin — a
7-module pipeline mixing four HLS modules with three hand-written RTL
modules, exercising every supported topology-wiring pattern and the full
three-tier verification workflow (HLS C-sim → single-module XSIM →
full-chip XSIM integration). It's the plugin new plugin authors are
pointed at as a copy-from starting point. For the simplest possible
plugin instead, see [the RTL-only example](rtl-example.md)
(`passthrough_demo`).

## Pipeline

```
hit_decoder × 4 ──(gather)──→ hit_collector ──→ trigger_logic
                             ├─(instance_assign)→ decoded_partition_sink
                             └─(contract_wiring)→ trigger_fanout → trigger_contract_sink → trigger_output
```

## Modules: HLS-generated vs hand-authored RTL

| Module | Kind | Instances | Description |
|--------|------|-----------|-------------|
| `hit_decoder` | **HLS** | 4 | Decodes a packed 32-bit hit; applies φ+1 calibration |
| `hit_collector` | **HLS** | 1 | Fan-in: counts valid channels, sums φ coordinates |
| `trigger_logic` | **HLS** | 1 | Majority trigger: accept when ≥2 hits |
| `trigger_output` | **HLS** | 1 | Packs the trigger word `{accept[31], quality[23:16]}` |
| `decoded_partition_sink` | **RTL** | 1 | Proves the `instance_assign` topology pattern |
| `trigger_fanout` | **RTL** | 1 | Proves `contract_wiring` (array producer side) |
| `trigger_contract_sink` | **RTL** | 1 | Proves `contract_wiring` (array consumer side) |

The four algorithmic modules are C++ synthesized through Vitis HLS
(`forge hls run`); the three RTL modules exist specifically to prove
framework wiring patterns that don't need HLS at all, and are simulated
directly. HLS target: `xcvu9p-flga2104-2L-e`, clock period 4 ns.

## Topology patterns

`trigger_demo` intentionally exercises all five supported topology
patterns so it can serve as a complete reference for each. The full,
file-and-YAML-key-level walkthrough of each pattern lives in
`plugins/trigger_demo/CANONICAL_PATTERNS.md` — read that document as the
copy-from reference when wiring your own plugin's `design.yml`. In
summary:

| Pattern | Where in this plugin | What it proves |
|---------|--------------------------|-------------------|
| `gather` (fan-in via `role_pairs`) | `dec` → `col` | 4 scalar decoder outputs feed a collector's 4-element arrays |
| `instance_assign` (partitioned groups) | `dec` → `partmon` | instances 0–1 route to `lower_pair`, 2–3 to `upper_pair` |
| `contract_wiring` (array wiring via contract) | `tfan` → `tsink` | trigger metadata arrays wired purely from matching `wiring_kind` labels, no explicit signal list |
| `port_map` (explicit scalar chain) | `col`→`trig`, `tsink`→`tout` | small control signals connected by name |
| `external_in/out_ports` | `dec`, `tout` | boundary ports exposed from the generated `algo_top` |

## Verification: the three-tier pyramid

Every module is verified at up to three increasing integration levels —
9 flows total:

| Tier | Kind | Backend | Flows |
|------|------|---------|-------|
| 1. HLS C-sim | `hls_csim` | `csim` | `hit_decoder_csim`, `hit_collector_csim`, `trigger_logic_csim`, `trigger_output_csim` |
| 2. Single-module RTL | `single_module_rtl` | `xsim` | `hit_decoder_xsim`, `hit_collector_xsim`, `trigger_logic_xsim`, `trigger_output_xsim` |
| 3. Full-chip RTL | `full_chip_rtl` | `xsim` | `trigger_pipeline_xsim` (the generated `algo_top`, end to end) |

The dataset (`forge/verify/schemas/data/trigger_demo_golden.xml`) has 4
golden events covering every trigger outcome (accept/reject, empty event,
max occupancy).

## Running it

The full end-to-end sequence (HLS synthesis → gen-top → verify generate →
stimulus → doctor → run) is documented step by step in
`plugins/trigger_demo/README.md`'s "End-to-end Workflow" section, and
mirrors the shorter [golden-path tutorial](golden-path.md) but with an
added HLS synthesis stage:

```bash
forge hls run \
  --registry plugins/trigger_demo/forge/modules.yml \
  --modules all \
  --hls-build-root build_hls_trigger_demo \
  --stages csim,synth,cosim,export
```

runs before `forge topgen gen-top`, since the four HLS modules must be
synthesized before the RTL top level can be generated around them.
Running the full pipeline requires both Vitis HLS and Vivado `xsim` on
`PATH`.

## Layout

```
plugins/trigger_demo/
├── algo/                             ← algorithm sources (stays at plugin root)
│   ├── common/trigger_types.h        ← shared type definitions
│   ├── hit_decoder/, hit_collector/, trigger_logic/, trigger_output/  ← HLS source + headers
│   └── rtl/                          ← Verilog RTL helpers (3 modules)
├── forge/                              ← FORGE integration capsule
│   ├── modules.yml                   ← authored: module registry (7 modules)
│   ├── designs/design.yml            ← authored: topology (instances, connections, groups)
│   ├── interfaces/                   ← authored: one contract per module
│   └── verify/                       ← authored contract + generated flow artifacts (9 flows)
├── CANONICAL_PATTERNS.md
└── README.md
```

## Next

- `plugins/trigger_demo/CANONICAL_PATTERNS.md` — the authoritative,
  file-level reference for every pattern summarized above.
- [Authoring topology contracts](../how-to/author-topology-contracts.md)
  — the general `design.yml`/`modules.yml`/interface-contract reference.
- [The golden-path tutorial](golden-path.md) — the same install-to-run
  path, using the simpler `passthrough_demo` as its example.
