# Examples

This directory contains low-level example design configurations for the `topgen` tool itself.

These examples are not the maintained proof-consumer route for the framework release story. They are format samples for tool behavior and may include legacy or compatibility-era constructs that are not the preferred Topology A authoring path.

For the supported end-to-end framework consumer example, use `plugins/trigger_demo/` instead.

## Available Examples

### 1. Generic Pipeline (`generic_pipeline.yaml`)

**Streaming pipeline** - A small example showing mixed HLS and RTL modules.

- **Scale**: 4 modules with 5 instances total
- **Complexity**: Simple staged datapath with:
  - two input adapters
  - one merge stage
  - one configurable delay block
  - one packet formatter
- **Features**:
  - legacy/compatibility-style `port_map_ranges` wiring examples
  - Mixed HLS and RTL modules
  - External I/O ports for framework integration

**Usage:**
```bash
# Generate VHDL top
topgen gen-top generic_pipeline.yaml --mode vhdl --output generic_top.vhd

# With linting
topgen gen-top generic_pipeline.yaml --mode vhdl --lint --fix-lint

# Generate Block Design
topgen gen-top generic_pipeline.yaml --mode bd --output generic_bd.tcl
```

## Design YAML Structure

### Basic Elements

```yaml
# FPGA configuration
part: xcvu13p-fsga2577-1-e
clock_period: 2.77  # ns

# Build settings
max_parallel_jobs: 8
block_protocol: none
connect_clock: true
connect_reset: true

# Module definitions
modules:
  - name: module_name
    top: top_entity_name
    src: [source_files.cpp]
    kind: hls | rtl
    instances: 1
    external_in_ports: [port_list]
    external_out_ports: [port_list]

# Connections
connections:
  - from: source_module
    to: dest_module
    port_map:
      - [src_port, dst_port]
    port_map_ranges:
      - src_prefix: out_
        dst_prefix: in_
        count: 10
```

### Advanced Features

#### 1. Port Map Ranges
Connect multiple sequential ports efficiently:
```yaml
port_map_ranges:
  - src_prefix: data_out
    dst_prefix: data_in
    count: 16
    src_start: 0
    dst_start: 0
```
Generates: `data_out_0 → data_in_0`, `data_out_1 → data_in_1`, ..., `data_out_15 → data_in_15`

#### 2. Template-Based 2D Mapping
For matrix-like port structures:
```yaml
port_map_ranges:
  - src_tpl: "matrix_{0}_{1}"
    dst_tpl: "matrix_{0}_{1}"
    dims: [18, 16]  # 18 rows, 16 columns
    order: [0, 1]   # Row-major order
```
Generates all `matrix_i_j` connections for i∈[0,17], j∈[0,15]

#### 3. Multi-Instance Connections
Connect to specific instances:
```yaml
- from: source
  to: dest
  instance: 0  # Connect to first instance
  port_map: ...
```

#### 4. RTL Module Configuration
```yaml
- name: rtl_module
  kind: rtl
  rtl_lang: vhdl
  vhdl_library: work
  vhdl_version: 2008
  rtl_packages: [package_file.vhd]
```

## Testing Your Design

1. **Validate syntax:**
   ```bash
   topgen match-ports your_design.yaml
   ```

2. **Generate IP summary:**
   ```bash
   topgen ip-summary your_design.yaml
   ```

3. **Generate top-level:**
   ```bash
   topgen gen-top your_design.yaml --mode vhdl --output test.vhd
   ```

4. **Lint the output:**
   ```bash
   topgen lint test.vhd
   ```

## Tips

- Start with a simple 2-3 module design to understand the flow
- For the current supported framework consumer path, prefer contract-driven topology groups over `port_map_ranges`
- Set `external_in_ports`/`external_out_ports` to expose interfaces to the framework
- Use `--lint` flag during generation to catch VHDL style issues early
- The tool auto-generates `ip_info.yaml` if not present

## Common Patterns

### Pattern 1: Pipeline Chain
```yaml
connections:
  - from: stage1
    to: stage2
    port_map:
      - [out_data, in_data]
      - [out_valid, in_valid]
  - from: stage2
    to: stage3
    port_map:
      - [out_data, in_data]
      - [out_valid, in_valid]
```

### Pattern 2: Fanout
```yaml
- from: source
  to: dest_array
  port_map_ranges:
    - src_prefix: out
      dst_prefix: in
      count: 8
```

### Pattern 3: Aggregator
```yaml
connections:
  - from: producer_array
    to: consumer
    port_map_ranges:
      - src_prefix: out_0
        dst_prefix: in
        count: 4
        src_start: 0
```

## See Also

- [Main README](../README.md) - component overview and usage
