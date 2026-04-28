# Trigger Demo Plugin

The **canonical reference plugin** for the ARC framework. Demonstrates every
supported topology pattern and the full three-tier verification workflow
(HLS C-sim → single-module XSIM → full-chip XSIM integration). New plugin
authors should use this as their copy-from starting point.

→ **Quickstart:** [docs/MINIMAL_CONSUMER_QUICKSTART.md](../../docs/MINIMAL_CONSUMER_QUICKSTART.md)
→ **Plugin author guide:** [docs/PLUGIN_AUTHOR_GUIDE.md](../../docs/PLUGIN_AUTHOR_GUIDE.md)
→ **Framework interface:** [docs/FRAMEWORK_CORE_INTERFACE.md](../../docs/FRAMEWORK_CORE_INTERFACE.md)

## Pipeline

```
hit_decoder × 4 ──(gather)──→ hit_collector ──→ trigger_logic
                             ├─(instance_assign)→ decoded_partition_sink
                             └─(contract_wiring)→ trigger_fanout → trigger_contract_sink → trigger_output
```

## Modules

| Module | Kind | Instances | Description |
|--------|------|-----------|-------------|
| `hit_decoder` | HLS | 4 | Decodes packed 32-bit hit; applies φ+1 calibration |
| `hit_collector` | HLS | 1 | Fan-in: counts valid channels, sums φ coordinates |
| `trigger_logic` | HLS | 1 | Majority trigger: accept when ≥2 hits |
| `trigger_output` | HLS | 1 | Packs trigger word `{accept[31], quality[23:16]}` |
| `decoded_partition_sink` | RTL | 1 | Proves `instance_assign` topology groups |
| `trigger_fanout` | RTL | 1 | Proves `contract_wiring` (array producer side) |
| `trigger_contract_sink` | RTL | 1 | Proves `contract_wiring` (array consumer side) |

HLS target: `xcvu9p-flga2104-2L-e`, clock period 4 ns.

## Topology Patterns

This plugin intentionally exercises all five supported topology patterns so
it can serve as a complete reference for each:

| Pattern | Location in `design.yml` | Framework feature |
|---------|--------------------------|-------------------|
| `gather` (fan-in via `role_pairs`) | `dec` → `col` | 4 scalar decoder outputs → collector 4-element arrays |
| `instance_assign` (partitioned groups) | `dec` → `partmon` | instances 0–1 → `lower_pair`, 2–3 → `upper_pair` |
| `contract_wiring` (array wiring via contract) | `tfan` → `tsink` | trigger metadata arrays driven by interface contract roles |
| `port_map` (explicit scalar chain) | `col`→`trig`, `tsink`→`tout` | small control signals connected by name |
| `external_in/out_ports` | `dec`, `tout` | boundary ports exposed from the generated `algo_top` |

## Dataset

4 golden events covering all trigger outcomes:

| Event id | Valid hits | Accept | Note |
|----------|-----------|--------|------|
| 0 | 3 | yes | standard trigger |
| 1 | 1 | no | below threshold (< 2) |
| 2 | 4 | yes | max occupancy |
| 3 | 0 | no | empty event |

Dataset: `verify/schemas/data/trigger_demo_golden.xml`

## Verification Flows

| Flow | Kind | Backend | What it checks |
|------|------|---------|----------------|
| `hit_decoder_csim` | hls_csim | csim | decode of all 4 golden events |
| `hit_collector_csim` | hls_csim | csim | fan-in aggregation, phi sum |
| `trigger_logic_csim` | hls_csim | csim | majority decision on all events |
| `trigger_output_csim` | hls_csim | csim | output word packing |
| `hit_decoder_xsim` | single_module_rtl | xsim | RTL decoded output matches golden |
| `hit_collector_xsim` | single_module_rtl | xsim | RTL collector output matches golden |
| `trigger_logic_xsim` | single_module_rtl | xsim | RTL trigger decision matches golden |
| `trigger_output_xsim` | single_module_rtl | xsim | RTL packed word matches golden |
| `trigger_pipeline_xsim` | full_chip_rtl | xsim | generated `algo_top` end-to-end integration |

## File Structure

```
plugins/trigger_demo/
├── modules.yml                   ← authored: module registry (7 modules)
├── designs/
│   └── design.yml                ← authored: topology (instances, connections, groups)
├── interfaces/
│   ├── hit_decoder_ip.interface.yaml
│   ├── hit_collector_ip.interface.yaml
│   ├── trigger_logic_ip.interface.yaml
│   ├── trigger_output_ip.interface.yaml
│   ├── decoded_partition_sink.interface.yaml
│   ├── trigger_fanout.interface.yaml
│   └── trigger_contract_sink.interface.yaml
├── algo/
│   ├── common/trigger_types.h    ← authored: shared type definitions
│   ├── hit_decoder/              ← authored: HLS source + header
│   ├── hit_collector/            ← authored: HLS source + header
│   ├── trigger_logic/            ← authored: HLS source + header
│   ├── trigger_output/           ← authored: HLS source + header
│   └── rtl/                      ← authored: Verilog RTL helpers (3 modules)
└── verify/
    ├── design.verification.yml   ← authored: verification contract (9 flows)
    ├── schemas/data/             ← authored: golden XML dataset
    ├── src/                      ← authored: shared testbench headers
    ├── tests/                    ← authored: HLS C-sim testbench sources (tb_*.cpp)
    ├── tools/
    │   ├── bootstrap.py          ← authored: plugin registration with arc verify
    │   ├── gen_stimulus.py       ← authored: xsim stimulus generator
    │   ├── trigger_demo_verify_env.sh ← authored: shell PYTHONPATH helper
    │   └── tests/                ← authored: unit tests for verify tooling
    ├── <flow>/verify.flow.yml    ← generated: arc verify generate design.verification.yml
    ├── <flow>/tb_*.sv            ← generated: arc verify generate ...
    ├── <flow>/wave.tcl           ← generated: arc verify generate ...
    └── <flow>/stimulus_current.svh ← plugin-generated: gen_stimulus.py
```

> **What you author vs what the framework generates:**
> Files under `verify/<flow>/` are framework-generated or plugin-generated — do **not**
> hand-edit them. Regenerate with `arc verify generate` and `gen_stimulus.py`.
> Committed copies serve as the CI baseline.
> See [docs/MINIMAL_CONSUMER_QUICKSTART.md](../../docs/MINIMAL_CONSUMER_QUICKSTART.md).

## End-to-end Workflow

Run all commands from the framework repo root (call it `<repo_root>`):

```bash
cd <repo_root>
export CONSUMER_ROOT="$(pwd)"
```

### Step 1 — HLS synthesis (all modules)

```bash
arc hls run \
  --registry plugins/trigger_demo/modules.yml \
  --modules all \
  --hls-build-root build_hls_trigger_demo \
  --stages csim,synth,cosim,export
```

### Step 2 — Generate Verilog top

```bash
arc topgen gen-top \
  plugins/trigger_demo/designs/design.yml \
  --consumer-root "$CONSUMER_ROOT" \
  --contracts-from plugins/trigger_demo/modules.yml \
  --build-dir build_hls_trigger_demo \
  --mode verilog \
  --strict \
  --output gen-top/design_trigger_demo_pipeline/algo_top.v
```

### Step 3 — Generate verification flow configs

```bash
arc verify generate plugins/trigger_demo/verify/design.verification.yml
```

Writes `verify.flow.yml`, `tb_*.sv`, and `wave.tcl` into each of the 9 flow
directories. Idempotent — safe to re-run.

### Step 4 — Generate stimulus SVH files

```bash
python3 plugins/trigger_demo/verify/tools/gen_stimulus.py
```

Writes `stimulus_current.svh` into every xsim flow directory.
Use `--module <name>` or `--dry-run` for selective runs.

### Step 5 — Contract health check

```bash
arc verify doctor plugins/trigger_demo/verify/design.verification.yml
```

### Step 6 — Run verification flows

```bash
# C-sim (HLS — requires Vitis HLS)
for flow in hit_decoder_csim hit_collector_csim trigger_logic_csim trigger_output_csim; do
  arc verify run plugins/trigger_demo/verify/${flow}/verify.flow.yml --plugin trigger_demo
done

# Single-module RTL (requires Vivado/xsim)
for flow in hit_decoder_xsim hit_collector_xsim trigger_logic_xsim trigger_output_xsim; do
  arc verify run plugins/trigger_demo/verify/${flow}/verify.flow.yml --plugin trigger_demo
done

# Full-chip RTL integration (requires gen-top output from Step 2)
arc verify run \
  plugins/trigger_demo/verify/trigger_pipeline_xsim/verify.flow.yml \
  --plugin trigger_demo
```

### Running the Python unit tests

```bash
python3 -m pytest plugins/trigger_demo/verify/tools/tests -q
```

No Vivado or Vitis HLS required — these tests validate the Python tooling only.

## Selective Rebuild

To re-run a single flow after changing algo source:

```bash
# Re-synthesise one module
topgen hls run \
  --registry plugins/trigger_demo/modules.yml \
  --modules hit_decoder \
  --build-root build_hls_trigger_demo \
  --stages synth

# Re-run one xsim flow
python3 plugins/trigger_demo/verify/tools/gen_stimulus.py --module hit_decoder
fw_verify run \
  plugins/trigger_demo/verify/hit_decoder_xsim/verify.flow.yml \
  --plugin trigger_demo
```
