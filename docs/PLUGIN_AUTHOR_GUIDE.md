# Plugin Author Guide — Topology A

**Version:** 2.0
**Date:** 2026-04-06
**Audience:** Developers adding new HLS/HDL modules to the topgen framework

---

## Plugin Directory Layout — The `arc/` Capsule Convention

Every plugin must separate its **implementation sources** (algorithm code, HDL) from
its **ARC integration contract** (module registry, interface descriptions, verification
contract). The ARC contract files are grouped in a single subdirectory called `arc/`:

```
plugins/<your_plugin>/          ← submodule root (git submodule or plain directory)
├── algo/                       ← implementation sources — algo C++, HDL, CMakeLists
│   ├── common/
│   ├── my_module/
│   │   └── my_module.cpp
│   └── rtl/
│       └── my_rtl.v
├── arc/                        ← ARC integration capsule (all arc-facing files)
│   ├── modules.yml             ← module registry  (plugin_root = arc/)
│   ├── designs/
│   │   └── design.yml         ← design topology   (registry: ../modules.yml)
│   ├── interfaces/
│   │   └── my_module.interface.yaml
│   └── verify/
│       ├── design.verification.yml
│       ├── include/            ← DUT adapter headers
│       ├── schemas/
│       │   └── data/           ← dataset XML files
│       ├── src/                ← shared testbench sources
│       ├── tests/              ← per-module testbench cpp
│       └── tools/              ← gen_stimulus.py, etc.
├── docs/                       ← repo-level documentation (optional)
├── README.md
└── CMakeLists.txt
```

### Path anchor rules

All relative paths in `arc/` files are **relative to their own file's directory**:

| File | Anchor | Example |
|------|--------|---------|
| `arc/modules.yml` — `src:` / `includes:` / `tb_src:` | `arc/` (the file's own directory) | `src: [../algo/my_module/my_module.cpp]` |
| `arc/modules.yml` — `interface_contract:` | `arc/` | `interface_contract: interfaces/my_module.interface.yaml` |
| `arc/modules.yml` — `verify.tb_args:` | `arc/` | `tb_args: verify/schemas/data/events.xml` |
| `arc/designs/design.yml` — `registry:` | `arc/designs/` | `registry: ../modules.yml` |
| `arc/verify/design.verification.yml` — `datasets.xml:` | `arc/verify/` | `xml: schemas/data/events.xml` |

Key point: **`algo/` sources live one level above `arc/`**, so all source paths use `../algo/...`.
Interface contracts and verify artefacts live _inside_ `arc/`, so they do not need `../`.

### Consumer-root paths (build artefacts only)

`dut_rtl_source:` entries in generated `verify.flow.yml` are the only paths that are
relative to the **consumer root** (the framework checkout root). These are build artefacts
produced by `arc hls run synth` and placed under `build_hls_<plugin>/`. Plugins never
hand-author consumer-root-relative paths.

### Mounting the plugin

The framework discovers a plugin by the path to its `arc/modules.yml`.
Pass it explicitly on the command line:

```bash
# From the framework root:
arc topgen validate-registry plugins/<plugin>/arc/modules.yml
arc topgen gen-top  plugins/<plugin>/arc/designs/design.yml --contracts-from plugins/<plugin>/arc/modules.yml
arc verify generate plugins/<plugin>/arc/verify/design.verification.yml
arc verify run      plugins/<plugin>/arc/verify/<flow>/verify.flow.yml --consumer-root .
```

The plugin does not need to know where it is mounted — all paths inside `arc/` resolve
correctly regardless of the consumer's directory structure.

---

## Quick Start — Adding a New Module

### 1. Write and export your IP

Implement your module in HLS (C++) or RTL (VHDL/Verilog). Run through the `ip` stage
to generate an exported IP package in `ips/<module_name>/`.

### 2. Extract port metadata

Run the `ip-summary` extractor to update `ip_info.yaml` with your module's physical ports:

```bash
arc topgen ip-summary plugins/<plugin>/designs/design.yml --ip-root ips --output ip_info.yaml
```

### 3. Write the interface contract

Create `plugins/<plugin>/arc/interfaces/<module_name>.interface.yaml`:

```yaml
ip_interface:
  module_name: my_module
  ip_info_key: my_module
  source_type: hls
  roles:
    clock_primary:
      raw_port: ap_clk
      direction: input
      width: 1
    reset_primary:
      raw_port: ap_rst_n
      direction: input
      width: 1
    # Data roles — see "Contract Authoring" below
    data_output:
      raw_port_prefix: out_data_
      count: 8
      direction: output
      width: 32
      wiring_kind: processed_data        # required for contract-driven wiring
      partition: group_a                  # optional: for partitioned consumers
```

### 4. Register the module

Add to `plugins/<plugin>/arc/modules.yml`:

```yaml
- name: my_module
  kind: hls
  top: my_module
  src: [../algo/my_module/my_module.cpp]       # relative to arc/ — uses ../algo/ to reach sources
  interface_contract: interfaces/my_module.interface.yaml   # relative to arc/
```

### 5. Add to design topology

In `plugins/<plugin>/arc/designs/design.yml`:

**Module instance:**
```yaml
modules:
  - ref: my_module
    instances: 8
```

**Topology group (contract-driven bulk wiring):**
```yaml
topology_groups:
  - name: upstream_to_my_module
    family: processed_data
    from: upstream
    to: my_module
    wiring_kind: processed_data
```

**Scalar connection (small control signals):**
```yaml
connections:
  - from: upstream
    to: my_module
    port_map:
      - [ctrl_out, ctrl_in]
```

### 6. Verify and generate

```bash
# Verify contract against ip_info
arc core verify-contract --ip-info ip_info.yaml \
    --contract plugins/<plugin>/arc/interfaces/my_module.interface.yaml

# Generate structural Verilog
arc topgen gen-top plugins/<plugin>/arc/designs/design.yml \
    --mode verilog --consumer-root . --build-dir build \
    --hls-build-root build_hls --ip-root ips \
    --contracts-from plugins/<plugin>/arc/modules.yml \
    --output algo_top.v

# Strict mode (CI grade — rejects port_map_ranges, auto-match, missing contracts)
arc topgen gen-top ... --strict
```

---

## The Topology A Workflow

Topology A means all structured wiring is contract-driven. The workflow is:

```
Write module → Export IP → Extract ip_info → Write contract
    → Register in modules.yml → Add topology edge in design.yml
    → Verify contract → Generate with --strict
```

### What goes where

| Artifact | Contains | Does NOT contain |
|----------|----------|-----------------|
| `design.yml` | Module instances, topology graph, connection family selectors | Raw port names, index arithmetic, prefix patterns |
| `*.interface.yaml` | Semantic roles, physical port mapping, wiring_kind, partitions | Instance counts, connection topology |
| `modules.yml` | Module identity, contract references, build metadata | Wiring rules |
| `ip_info.yaml` | Extracted port metadata (generated artifact) | Anything hand-authored |

---

## Contract Authoring — Three Role Shapes

### Shape 1: Scalar

A single port.

```yaml
clock_primary:
  raw_port: ap_clk
  direction: input
  width: 1
```

### Shape 2: Prefix-Array

A numbered sequence of ports sharing a common prefix.

```yaml
dt_mb1_input:
  raw_port_prefix: in_dt_mb1_
  count: 5
  direction: input
  width: 27
  wiring_kind: dt_processed_stub
  partition: mb1
```

Ports: `in_dt_mb1_0` through `in_dt_mb1_4`.

### Shape 3: N-D Template

A multi-dimensional grid of ports.

```yaml
gen_nd_out_matrix:
  raw_port_tpl: "out_matrix{0}_{1}"
  dims: [18, 8]
  direction: output
  width: 15
  wiring_kind: plugin_matrix
```

### Key fields for contract-driven wiring

| Field | Purpose |
|-------|---------|
| `wiring_kind` | Matching key — roles with the same `wiring_kind` are paired |
| `partition` | Disambiguator — when multiple roles share a `wiring_kind`, `partition` selects the right one |

Both are required for topology_groups. Roles without `wiring_kind` cannot participate
in auto-match or instance_assign, but can still be referenced in explicit `role_pairs`.

---

## Topology Groups — Contract-Driven Connections

Topology groups replace the old `port_map_ranges` for structured data-path wiring.
They live under `topology_groups:` in `design.yml`.

### Strategy 1: Auto-match (partition-aware)

When source and destination contracts share the same `wiring_kind` + `partition` labels,
no explicit mapping is needed:

```yaml
- name: subdet_to_rgf_dt
  family: detector_processed
  from: subdet
  to: rgf
  wiring_kind: dt_processed_stub
```

The deriver matches roles by `(wiring_kind, partition)` pairs.

### Strategy 2: Instance-assign (multi-instance → partitioned consumer)

When multiple producer instances feed partitioned consumer inputs:

```yaml
- name: dt_to_concentrator
  family: detector_processed
  from: dt
  to: subdet
  wiring_kind: dt_processed_stub
  instance_assign:
    - instances: [0, 5]
      partition: mb1
    - instances: [5, 10]
      partition: mb2
    - instances: [10, 15]
      partition: mb3
```

### Strategy 3: Explicit role_pairs

When role names differ or modules lack `wiring_kind`:

```yaml
- name: cfg_to_dt
  family: configuration
  from: cfg
  to: dt
  role_pairs:
    - [cfg_word_out, cfg_word]
    - [cfg_valid_out, cfg_valid]
```

### Strategy 4: Scatter / Gather

When a prefix_array fans out to individual instances (scatter) or instances
feed back into a prefix_array (gather):

```yaml
# Scatter: subdet's array → 5 delay instances
- name: subdet_to_delay
  family: rpc_delay
  from: subdet
  to: rpc_rb1_in_dly
  role_pairs:
    - [output_stream_array_rpc_rb1_in, input_stream_0]

# Gather: 5 delay instances → rgf's array
- name: delay_to_rgf
  family: rpc_delay
  from: rpc_rb1_in_dly
  to: rgf
  role_pairs:
    - [output_stream_0, input_stream_array_rpc_rb1_in]
```

### Instance offset for shifted diagonal mapping

When producer instance 0 doesn't map to consumer instance 0:

```yaml
- name: cfg_to_csc
  family: configuration
  from: cfg
  to: csc
  src_instance_offset: 15    # cfg instance 15 → csc instance 0
  role_pairs:
    - [cfg_word_out, cfg_word]
```

---

## Scalar Connections (Permitted Exceptions)

Small control/status signals that are truly terminal (not part of the main data pipeline)
use plain `port_map` under `connections:`:

```yaml
connections:
  - from: best_sel
    to: out_muon_packer
    port_map:
      - [out_best_constr, in_best_constr]
      - [out_ref_hit, in_ref_hit]
```

These are the only permitted explicit wiring in Topology A. All structured (array/N-D)
data-path wiring must go through topology_groups.

---

## Strict Mode (`--strict`)

Strict mode enforces full Topology A compliance. It rejects:

| Condition | Action |
|-----------|--------|
| Module without a contract | Fails with exit 1 |
| Connection using auto-match heuristic | Fails with exit 1 |
| Connection using `port_map_ranges` | Fails with exit 1 |
| Topology group verification errors | Fails with exit 1 |
| Unaccounted open outputs | Fails with exit 1 |
| Unaccounted tied inputs | Fails with exit 1 |

**CI should always run with `--strict`.** Development can omit it temporarily.

---

## Intentionally Unconnected Ports

If your module has outputs that are intentionally left open:

```yaml
allowed_unconnected:
  open_outputs:
    - "my_module.debug_*"
  tied_inputs:
    - "my_module.unused_cfg"
```

---

## Troubleshooting

### `verify-contract` fails: "ip_info_key not found"

Your `ip_info_key` doesn't match any key in `ip_info.yaml`.
Check: `grep "^ *my_module:" ip_info.yaml`.

### `--strict` fails: "modules have no interface contract"

Pass `--contracts-from plugins/<plugin>/arc/modules.yml` and ensure your module's
`arc/modules.yml` entry has `interface_contract:` pointing to the contract file.

### `--strict` fails: "connection(s) still use port_map_ranges"

Migrate the connection to a `topology_group` entry. See topology group strategies above.

### `--strict` fails: "topology group verification error(s)"

Check that partition labels in `instance_assign` match the consumer contract,
role names in `role_pairs` exist in the contracts, and `wiring_kind` matches roles
in both source and destination contracts.

### `--strict` fails: "unaccounted open output(s)"

Add `allowed_unconnected` patterns in `design.yml`.

---

## Reference

| Artifact | Location | Purpose |
|----------|----------|---------|
| Target Architecture | `docs/TOPOLOGY_A_TARGET_ARCHITECTURE.md` | Final Topology A spec |
| Topology Groups Schema | `docs/TOPOLOGY_GROUPS_SCHEMA.md` | Schema for topology_groups |
| IP Interface Policy | `framework/IP_INTERFACE_POLICY.md` | Contract rules |
| Signal Families | `framework/normalized_signal_families.yaml` | Semantic family vocabulary |
| Interface contracts | `<plugin>/arc/interfaces/*.interface.yaml` | Per-module contracts |
| Module registry | `<plugin>/arc/modules.yml` | Module identity + contract refs |
| Design topology | `<plugin>/arc/designs/design.yml` | Instances, connections, topology_groups |

---

## Latency and Performance Analysis

After topology generation and verification pass, use `arc analyze` to measure latency,
check pipeline balance, and produce plots and an HTML dashboard.

**Plugin requirements for full analysis:**

### `modules.yml` — annotate every module with a latency value

```yaml
modules:

  - name: hit_decoder
    kind: hls
    latency_hint: 0          # combinational (ap_ctrl_none, clock_free)

  - name: trigger_logic
    kind: hls
    latency_hint: 3          # HLS LATENCY min=3 max=3; overridden by
                              # hls_report once synth runs

  - name: trigger_fanout
    kind: rtl
    latency_hint: 0          # combinational passthrough
```

Resolution order: `latency_cycles` (explicit) → HLS report (`--hls-build-root`) → `latency_hint` → unknown.

### `design.yml` — annotate timing-alignment connections

```yaml
connections:
  - from: col
    to: trig
    register_stages: 2       # inserts RegisterStage instances in algo_top.v

  - from: trig
    to: tfan
    delay_cycles: 3          # inserts signal_delay instances in algo_top.v

  - from: slr1_block
    to: slr2_block
    boundary: slr1_to_slr2
    delay_cycles: 2          # emits a named slr_crossing_delay boundary helper
```

Both fields are reflected in the generated `algo_top.v` and in the static latency
check report. Use `boundary` when the inserted registers are not just local
alignment but an implementation boundary that downstream build tooling may want
to constrain, floorplan, report, or include as a named crossing source. The
consumer build must compile the generated boundary helper RTL together with the
top-level manifest.

### `plugins/<plugin>/arc/verify/plot_config.yml` — define result plots

```yaml
plots:
  - name: out_valid_timeline
    kind: line
    x: cycle
    y: tout_out_valid
    title: "Output Valid Timeline"
    xlabel: "Clock Cycle"
    ylabel: "tout_out_valid"

  - name: trigger_quality
    kind: scatter
    x: cycle
    y: trigger_quality
    title: "Trigger Quality per Event"
```

See `docs/ANALYSIS_GUIDE.md` for the full command reference and probe CSV format.
