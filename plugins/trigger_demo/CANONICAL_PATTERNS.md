# Trigger Demo — Canonical Framework Patterns

This document explains each framework topology and verification pattern
demonstrated by the trigger_demo plugin, with pointers to the exact files
and YAML keys that implement them.

Use this as a copy-from reference when authoring a new plugin.

All paths below are relative to the plugin's `arc/` capsule
(`plugins/trigger_demo/arc/`), matching the path anchor rule in
[`arc/README.md`](../../arc/README.md).

---

## Pattern 1 — Fan-in via `role_pairs` (gather topology)

**What:** Route scalar outputs from N instances of module A into the
corresponding array inputs of a single module B.

**Where:**
- `designs/design.yml` → `topology_groups[name=decoder_to_collector]`
- `interfaces/hit_decoder_ip.interface.yaml` → roles `decoded_hit`, `decoded_valid`
- `interfaces/hit_collector_ip.interface.yaml` → roles `input_hit_array`, `input_valid_array`

**YAML excerpt (`design.yml`):**
```yaml
topology_groups:
  - name: decoder_to_collector
    family: trigger_decoded
    from: dec
    to: col
    role_pairs:
      - [decoded_hit, input_hit_array]
      - [decoded_valid, input_valid_array]
```

**Interface requirement:** The source role is scalar (`array: false`);
the destination role must be an array (`array: true`) with `count` equal to
the number of source instances.

---

## Pattern 2 — Partitioned fan-in via `instance_assign`

**What:** Route subsets of N source instances to distinct partitions of a
single sink module. Each partition receives a contiguous slice of instances.

**Where:**
- `designs/design.yml` → `topology_groups[name=decoder_hits_to_partition_sink]`
  and `topology_groups[name=decoder_valids_to_partition_sink]`
- `interfaces/decoded_partition_sink.interface.yaml` → roles carry `partition:` labels

**YAML excerpt (`design.yml`):**
```yaml
- name: decoder_hits_to_partition_sink
  family: trigger_partition_probe
  from: dec
  to: partmon
  wiring_kind: decoded_hit
  instance_assign:
    - instances: [0, 2]   # half-open range [0, 2) = instances 0 and 1
      partition: lower_pair
    - instances: [2, 4]   # half-open range [2, 4) = instances 2 and 3
      partition: upper_pair
```

**Interface requirement:** The sink interface must declare array roles with a
`partition:` label whose name matches the `partition:` values in
`instance_assign`. Array `count` per partition equals the slice width.

---

## Pattern 3 — Contract-wiring for array data paths

**What:** Move multi-lane arrays between a producer and a consumer module
driven entirely by matching `wiring_kind` labels in their interface contracts,
without enumerating individual signal names.

**Where:**
- `designs/design.yml` → `connections[from=tfan, to=tsink]` with `contract_wiring: true`
- `interfaces/trigger_fanout.interface.yaml` → output array roles with `wiring_kind:`
- `interfaces/trigger_contract_sink.interface.yaml` → matching input array roles

**YAML excerpt (`design.yml`):**
```yaml
- from: tfan
  to: tsink
  contract_wiring: true
```

**Interface requirement:** The producer's output array roles and the consumer's
input array roles must share the same `wiring_kind` label and equal `count`.
The framework matches them automatically — no `port_map` entry is needed.

---

## Pattern 4 — Scalar chain via `port_map`

**What:** Connect a small number of control signals between two modules by
listing `[source_port, dest_port]` pairs explicitly.

**Where:**
- `designs/design.yml` → `connections[from=col, to=trig]` and
  `connections[from=tsink, to=tout]`

**YAML excerpt (`design.yml`):**
```yaml
- from: col
  to: trig
  port_map:
    - [n_hits,           n_hits]
    - [phi_sum,          phi_sum]
    - [collector_valid,  in_valid]
```

**When to use:** `port_map` is appropriate for small sets of signals where
the port names differ or no `wiring_kind` is declared. For bulk array
connections, prefer `contract_wiring` (Pattern 3) or `role_pairs` (Pattern 1).

---

## Pattern 5 — Boundary exposure via `external_in_ports` / `external_out_ports`

**What:** Expose selected ports of inner module instances as top-level ports
of the generated `algo_top`, making them visible to external stimulus
injection or readout logic.

**Where:**
- `designs/design.yml` → `modules[name=dec]` and `modules[name=tout]`

**YAML excerpt (`design.yml`):**
```yaml
modules:
  - name: dec
    ref: hit_decoder
    instances: 4
    external_in_ports: [raw_hit, raw_valid]

  - name: tout
    ref: trigger_output
    instances: 1
    external_out_ports: [trigger_word, out_valid]
```

**Effect:** `topgen gen-top` promotes these to `input`/`output` ports of
`algo_top` (with `dec_<i>_raw_hit`, `dec_<i>_raw_valid`, `tout_trigger_word`,
`tout_out_valid` naming). The full-chip TB (`tb_algo_top.sv`) drives them via
`run_stimulus()`.

---

## Pattern 6 — Three-tier verification pyramid

**What:** Verify each module at three increasing integration levels:
1. **HLS C-sim** (`hls_csim`) — exercise the C++ model with golden inputs
2. **Single-module RTL** (`single_module_rtl`) — exercise the synthesized
   Verilog against golden stimulus SVH
3. **Full-chip RTL** (`full_chip_rtl`) — exercise the generated `algo_top`
   with all modules connected

**Where:**
- `verify/design.verification.yml` — all 9 flows declared here
- `verify/tests/tb_*.cpp` — HLS C-sim testbench sources
- `verify/<flow>/tb_*.sv` — generated RTL testbenches
- `verify/tools/gen_stimulus.py` — writes `stimulus_current.svh` for all xsim flows

**Flow count for this plugin:** 4 csim + 4 single-module xsim + 1 full-chip xsim = **9 flows total**

**Key rule:** `verify.flow.yml`, `tb_*.sv`, `wave.tcl` are **framework-generated**
— do not hand-edit. `stimulus_current.svh` is **plugin-generated** via
`gen_stimulus.py`. Both are committed to the repo as the CI baseline.

---

## Pattern 7 — Interface contract authoring

Each module has one `interfaces/<module>.interface.yaml`. Canonical fields:

```yaml
ip_interface:
  module_name: <module>        # must match HLS top function / Verilog module name
  ip_info_key: <short_key>     # HLS only — matches key in ip_info.yaml
  source_type: hls | rtl
  normalization_status: ready  # ready | pending | skip
  notes: >
    Human-readable description of what this IP does and any important
    implementation notes (II, special port behaviour, etc.)

  roles:
    clock_primary:             # required for all clocked IPs
      raw_port: ap_clk
      direction: input
      width: 1

    reset_primary:             # required for all clocked IPs
      raw_port: ap_rst
      direction: input
      width: 1
      active_level: high

    <role_name>:               # scalar data role
      raw_port: <port_name>
      direction: input | output
      width: <bits>
      wiring_kind: <family>    # required for contract-driven wiring
      notes: "..."

    <array_role_name>:         # array data role
      array: true
      raw_port_prefix: <prefix>_   # port names will be <prefix>_0, <prefix>_1, ...
      count: <N>
      direction: input | output
      width: <bits>
      wiring_kind: <family>        # required for contract_wiring
      partition: <label>           # required for instance_assign
```

---

## Quick reference: which file owns what

| What to change | File to edit |
|----------------|-------------|
| Module HLS/RTL registration | `modules.yml` |
| Module instances and wiring | `designs/design.yml` |
| Port metadata and wiring roles | `interfaces/<module>.interface.yaml` |
| Verification flows and datasets | `verify/design.verification.yml` |
| Plugin registration with arc verify | `verify/tools/bootstrap.py` |
| Golden test data | `verify/schemas/data/trigger_demo_golden.xml` |
| Xsim stimulus logic | `verify/tools/gen_stimulus.py` |
| HLS C-sim testbench | `verify/tests/tb_<module>.cpp` |
| Algo implementation | `algo/<module>/<module>.cpp` |
