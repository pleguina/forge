# Trigger Demo Plugin

Minimal trigger detection pipeline demonstrating the public framework
contract surfaces beyond single-module verification.

## Pipeline

```
hit_decoder × 4 ──(gather)──→ hit_collector ──→ trigger_logic
                             ├─(instance_assign)→ decoded_partition_sink
                             └─(contract_wiring)→ trigger_fanout → trigger_contract_sink → trigger_output
```

### Modules

| Module | Instances | Description |
|--------|-----------|-------------|
| `hit_decoder` | 4 | Decodes packed 32-bit hit; applies phi+1 calibration |
| `hit_collector` | 1 | Fan-in: counts valid channels, sums phi coordinates |
| `trigger_logic` | 1 | Majority trigger: accept when ≥2 hits |
| `decoded_partition_sink` | 1 | RTL partitioned consumer proving `instance_assign` topology groups |
| `trigger_fanout` | 1 | RTL array producer used to prove `contract_wiring` |
| `trigger_contract_sink` | 1 | RTL array consumer that reconstructs trigger fields |
| `trigger_output` | 1 | Packs trigger word `{accept[31], quality[23:16]}` |

### Topology

- **Fan-in (gather):** 4 decoder scalar outputs → collector's 4-element arrays via `role_pairs` topology group
- **Partitioned topology:** decoder instances 0..1 and 2..3 feed separate consumer partitions via `instance_assign`
- **Contract wiring:** trigger metadata arrays move from `trigger_fanout` to `trigger_contract_sink` via `contract_wiring: true`
- **Chain (scalar):** collector → trigger_logic and contract sink → trigger_output via `port_map`

### Verification

Each stage has a C-sim testbench driven by the golden XML stimulus, and the
plugin also declares a full-chip XSIM flow over the generated `algo_top`.

```
verify/schemas/data/trigger_demo_golden.xml
```

4 events test all trigger outcomes: accept (3/4 hits), reject (1 hit),
max occupancy (4 hits), empty (0 hits).

## Running

```bash
cd /path/to/omtf_v2

# Generate Verilog top (strict mode)
topgen gen-top \
  plugins/trigger_demo/designs/design.yml \
  --consumer-root "$(pwd)" \
  --contracts-from "$(pwd)/plugins/trigger_demo/modules.yml" \
  --build-dir "$(pwd)/build_hls_trigger_demo" \
  --mode verilog \
  --strict \
  --output gen-top/design_trigger_demo_pipeline/algo_top.v
```

Then generate the verification artifacts for the integration flow:

```bash
fw_verify generate plugins/trigger_demo/verify/design.verification.yml --flow trigger_pipeline_xsim
python3 plugins/trigger_demo/verify/tools/gen_stimulus.py --module trigger_pipeline
```

## HLS Build

Each module supports the standard Vitis HLS flow:

```bash
# C-sim example (hit_decoder)
vitis_hls -f run_csim.tcl  # with tb_args: verify/schemas/data/trigger_demo_golden.xml
```

See `modules.yml` for per-module `build:` and `verify:` sections.
