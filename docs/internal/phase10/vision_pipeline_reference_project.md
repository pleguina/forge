# FORGE Vision Pipeline Reference Project

**Project name:** `vision_pipeline_demo`  
**Role:** Canonical non-trigger, non-DAQ reference project and end-to-end release acceptance design for FORGE  
**Base toolchain:** AMD/Xilinx Vivado, Vitis HLS, and XSim  
**Extension policy:** The complete RTL/HLS design is implemented first. A small hls4ml CNN is added later as an optional extension.

---

## 1. Purpose

`vision_pipeline_demo` demonstrates that FORGE can describe, generate, validate, verify, analyse, and explain a realistic multi-module FPGA system without relying on CMS, trigger, detector, or DAQ concepts.

The example must exercise:

- hand-written RTL;
- conventional Vitis HLS;
- semantic interface contracts;
- scalar, prefix-array, and N-dimensional physical bindings;
- grouped logical-interface members;
- structured coordinates and topology groups;
- cardinality;
- fan-out, gather, and explicit control connections;
- fixed, bounded, and elastic timing;
- initiation interval, throughput, buffering, occupancy, and backpressure;
- exact-cycle and transaction-order alignment;
- multiple clock/reset domains;
- level, pulse, coherent-control, stream, and reset CDC;
- explicit transformation objects;
- deterministic generation plans and plan hashes;
- static and runtime latency comparison;
- content-hash provenance;
- valid and deliberately invalid configurations;
- generated SVG and interactive HTML reports;
- external platform-wrapper generation.

The image algorithm is intentionally small and deterministic. Its purpose is to test integration, not image quality.

---

## 2. Design principles

### 2.1 Core acceptance must not depend on ML

The mandatory project uses only:

- example-local RTL;
- example-local Vitis HLS;
- FORGE-approved CDC/adapters;
- small deterministic datasets.

TensorFlow, PyTorch, ONNX, and hls4ml must not be required for the base installation, quickstart, or mandatory CI.

### 2.2 Progressive complexity

Provide three levels:

1. `quickstart.yml`: one clock, one RTL module, one HLS module.
2. `full_acceptance.yml`: all mandatory FORGE capabilities.
3. `full_acceptance_hls4ml.yml`: optional third-party generated-HLS integration.

### 2.3 Positive and negative evidence

The project must include both:

- valid designs that generate and simulate successfully;
- invalid designs that fail one specific rule with a stable diagnostic.

### 2.4 No invisible transformations

Every inserted register, delay, adapter, CDC, FIFO, fan-out, gather, tie-off, or constant must appear in:

- the canonical IR;
- the generation plan;
- timing/throughput analysis;
- provenance;
- graphical reports.

---

## 3. Required design variants

```text
plugins/vision_pipeline_demo/forge/designs/
  quickstart.yml
  full_acceptance.yml
  full_acceptance_backpressure.yml
  full_acceptance_resource_variant.yml

  invalid/
    invalid_direct_bus_cdc.yml
    invalid_bus_scalar_sync.yml
    invalid_missing_reset_sync.yml
    invalid_protocol.yml
    invalid_cardinality.yml
    invalid_ambiguous_match.yml
    invalid_latency_alignment.yml
    invalid_throughput.yml
    invalid_fifo_depth.yml
    invalid_plan_hash.yml

  optional/
    full_acceptance_hls4ml.yml
    full_acceptance_hls4ml_resource.yml
```

### `quickstart.yml`

```text
external pixel stream
        |
pixel_normalizer_hls
        |
threshold_rtl
        |
external result stream
```

It demonstrates:

- one ready/valid stream;
- one HLS block;
- one RTL block;
- fixed latency;
- one dataset;
- one verification flow;
- top generation;
- one topology diagram.

### `full_acceptance.yml`

The complete multi-clock system described below.

### `full_acceptance_backpressure.yml`

Same functional design, with deterministic sink stalls and FIFO occupancy checks.

### `full_acceptance_resource_variant.yml`

Uses a slower/lower-resource module implementation to change II, bottleneck, reports, and plan/provenance hashes.

---

## 4. Data model

Each input pixel transaction carries:

| Member | Width |
|---|---:|
| `pixel` | 8 |
| `x` | 12 |
| `y` | 12 |
| `frame_id` | 16 |
| `tile_id` | 16 |
| `end_of_line` | 1 |
| `end_of_frame` | 1 |
| `valid` | 1 |
| `ready` | 1 |

The pipeline produces:

- normalized intensity;
- gradient magnitude;
- threshold mask;
- tile statistics;
- packetized result records;
- frame-complete status.

---

## 5. Full topology

```text
                                clk_control
                                     |
                         +-------------------------+
                         | control_registers_rtl   |
                         +-------------------------+
                           |       |        |
                     enable    apply     config bundle
                           |       |        |
                    [level CDC] [pulse CDC] [mailbox CDC]
                           |       |        |
                           +-------+--------+
                                   |
                                   v
clk_pixel                  pixel_normalizer_hls
                                   |
                               [fan-out]
                              /    |     \
                             /     |      \
                            v      v       v
                      sobel_hls threshold  sample_decimator
                       L=8 II=1  RTL L=2         |
                            |      |             v
                            |  [delay +6]    tile_stats_hls
                            |      |          L=6..10 II=2
                            +------+              |
                                   v              |
                          edge_mask_merge         |
                            L=1 II=1               |
                                   +-------+-------+
                                           v
                                  metadata_join_rtl
                                  elastic, tile-tagged
                                           |
                         [async FIFO: clk_pixel -> clk_output]
                                           |
                                      clk_output
                                           |
                                    packetizer_rtl
                                    L=3, 2 records/beat
                                           |
                                    external output

packetizer.frame_done --[pulse CDC]--> clk_control
packetizer.error      --[level CDC]--> clk_control

external asynchronous reset
  -> reset synchronizer for each destination domain
```

---

## 6. Clock domains

| Domain | Frequency | Purpose |
|---|---:|---|
| `control` | 50 MHz | Configuration and status |
| `pixel` | 200 MHz | Main processing pipeline |
| `output` | 125 MHz | Packetized output |
| `ml` | 250 MHz | Optional hls4ml extension only |

Representative declaration:

```yaml
clock_domains:
  control:
    source: external
    raw_clock: clk_control
    frequency_hz: 50000000

  pixel:
    source: external
    raw_clock: clk_pixel
    frequency_hz: 200000000

  output:
    source: external
    raw_clock: clk_output
    frequency_hz: 125000000
```

### Reusable local domain slots

Contracts should declare local slots:

```yaml
domains:
  processing:
    clock_role: clock_primary
    reset_role: reset_primary
```

The design maps each instance:

```yaml
instances:
  - name: sobel
    module: sobel_hls
    domain_map:
      processing:
        clock: pixel
        reset: pixel_reset
```

The quickstart may use the implicit `default` domain. The full acceptance design must use explicit domains.

---

## 7. Reset domains

One external reset is asynchronously asserted and synchronously released in every destination domain.

```yaml
reset_domains:
  control_reset:
    source: rst_n_external
    clock_domain: control
    polarity: active_low
    assertion: asynchronous
    deassertion: synchronous

  pixel_reset:
    source: rst_n_external
    clock_domain: pixel
    polarity: active_low
    assertion: asynchronous
    deassertion: synchronous

  output_reset:
    source: rst_n_external
    clock_domain: output
    polarity: active_low
    assertion: asynchronous
    deassertion: synchronous
```

Each domain uses an explicit `reset-synchronizer` transformation.

Direct asynchronous reset release must fail strict validation.

---

## 8. CDC coverage

### 8.1 Level synchronization

```text
pipeline_enable: control -> pixel
error_level:     output  -> control
```

Transformation:

```text
cdc-level-synchronizer
```

Rules:

- only stable single-bit levels;
- destination observes eventual state;
- synchronization-stage count declared;
- reset behavior declared.

### 8.2 Pulse synchronization

```text
apply_configuration: control -> pixel
frame_done:          output  -> control
```

Transformation:

```text
cdc-pulse-synchronizer
```

Rules:

- one destination pulse per legal source event;
- supported minimum source-event spacing documented;
- no duplication.

### 8.3 Coherent multi-bit control transfer

Bundle:

```text
threshold_value[7:0]
kernel_mode[1:0]
frame_limit[15:0]
```

Direction:

```text
control -> pixel
```

Transformation:

```text
cdc-mailbox
```

or another request/acknowledge coherent transfer.

The bundle must update atomically. Independent bit synchronizers are forbidden.

### 8.4 Streaming multi-bit CDC

```text
processed_result_stream: pixel -> output
```

Transformation:

```text
async-fifo
```

Required properties:

- ready/valid semantics;
- depth 64 in the reference configuration;
- write/read domains;
- occupancy and high-water metrics;
- preserved order;
- full/empty handling;
- elastic or destination-domain-bounded latency.

### 8.5 Invalid CDC designs

`invalid_direct_bus_cdc.yml`:

```text
32-bit direct wire from pixel to output
```

`invalid_bus_scalar_sync.yml`:

```text
scalar synchronizer selected for a multi-bit coherent bus
```

`invalid_missing_reset_sync.yml`:

```text
external asynchronous reset released directly in a destination domain
```

Each must fail before RTL application with one primary stable diagnostic.

---

## 9. Interface vocabulary

### 9.1 Pixel stream

```yaml
type: forge.pixel_stream.v1
protocol: ready-valid
members:
  data:         {width: 8}
  x:            {width: 12}
  y:            {width: 12}
  frame_id:     {width: 16}
  tile_id:      {width: 16}
  end_of_line:  {width: 1}
  end_of_frame: {width: 1}
  valid:        {width: 1}
  ready:        {width: 1}
```

### 9.2 Edge/mask stream

```yaml
type: forge.edge_mask_stream.v1
protocol: ready-valid
members:
  normalized_pixel:  {width: 8}
  gradient_magnitude: {width: 12}
  threshold_mask:    {width: 1}
  x:                 {width: 12}
  y:                 {width: 12}
  frame_id:          {width: 16}
  tile_id:           {width: 16}
  valid:             {width: 1}
  ready:             {width: 1}
```

### 9.3 Tile-statistics stream

```yaml
type: forge.tile_statistics.v1
protocol: ready-valid
members:
  tile_id:  {width: 16}
  minimum:  {width: 8}
  maximum:  {width: 8}
  mean:     {width: 16}
  variance: {width: 24}
  valid:    {width: 1}
  ready:    {width: 1}
```

### 9.4 Configuration mailbox

```yaml
type: forge.configuration_mailbox.v1
protocol: request-acknowledge
members:
  threshold:  {width: 8}
  kernel_mode: {width: 2}
  frame_limit: {width: 16}
  request:    {width: 1}
  acknowledge: {width: 1}
```

### 9.5 Packet stream

```yaml
type: forge.packet_stream.v1
protocol: ready-valid
members:
  data:  {width: 64}
  keep:  {width: 8}
  last:  {width: 1}
  valid: {width: 1}
  ready: {width: 1}
```

---

## 10. Physical binding coverage

The project must exercise:

### Scalar

Used for clocks, resets, enable, pulse, and status.

```yaml
pipeline_enable:
  raw_port: pipeline_enable
```

### Prefix array

Used for a small status-counter bank.

```yaml
status_counters:
  raw_port_prefix: status_counter_
  count: 4
```

### N-dimensional template

Used for a 3×3 processing window or equivalent matrix interface.

```yaml
window_pixels:
  raw_port_tpl: "window_{row}_{column}"
  dims: [3, 3]
```

### Grouped members

```yaml
pixel_data:
  interface: pixels_in
  member: data

pixel_valid:
  interface: pixels_in
  member: valid

pixel_ready:
  interface: pixels_in
  member: ready
```

The reverse-direction `ready` signal remains physically correct while belonging to the same logical interface.

---

## 11. Module catalogue

### `control_registers_rtl`

- RTL;
- `control` domain;
- produces enable, apply pulse, and configuration bundle;
- consumes frame-done and error status.

### `pixel_source_rtl`

- RTL;
- `pixel` domain;
- deterministic simulation patterns:
  - constant;
  - ramp;
  - checkerboard;
  - horizontal edge;
  - vertical edge;
  - corner;
  - deterministic pseudo-random;
- one accepted pixel per cycle when not backpressured.

### `pixel_normalizer_hls`

Operation:

```text
normalized = clamp(scale * pixel + offset)
```

Reference contract:

```yaml
latency:
  kind: fixed
  cycles: 3
initiation_interval: 1
```

Real checked-in values must agree with the HLS report.

### `sobel_hls`

- 3×3 Sobel-like gradient;
- deterministic border policy;
- fixed reference latency 8 cycles;
- II=1.

```yaml
latency:
  kind: fixed
  cycles: 8
initiation_interval: 1
```

### `threshold_rtl`

```text
mask = normalized_pixel >= threshold
```

```yaml
latency:
  kind: fixed
  cycles: 2
initiation_interval: 1
```

### Alignment delay

The threshold path receives six `pixel` cycles:

```text
2 + 6 = 8
```

Transformation kind:

```text
alignment-delay
```

### `edge_mask_merge_rtl`

- merges Sobel and threshold branches;
- checks x, y, frame, and tile tags;
- requires exact-cycle alignment;
- fixed latency 1 cycle;
- II=1.

```yaml
alignment:
  kind: exact-cycle
```

### `sample_decimator_rtl`

- forwards every second accepted sample to the statistics branch;
- input capacity: 1/cycle;
- output rate: 0.5/cycle.

### `tile_stats_hls`

- computes tile minimum, maximum, mean, and variance;
- bounded latency;
- II=2.

```yaml
latency:
  kind: bounded
  min_cycles: 6
  max_cycles: 10
initiation_interval: 2
```

### `metadata_join_rtl`

- joins edge/mask data with statistics using `tile_id`;
- preserves transaction order;
- bounded buffering;
- elastic latency.

```yaml
alignment:
  kind: transaction-order
latency:
  kind: elastic
```

### Async output FIFO

- `pixel` to `output`;
- depth 64;
- occupancy/high-water reporting;
- preserved order;
- full/empty protection.

### `packetizer_rtl`

- `output` domain;
- packs two processed records into one 64-bit beat;
- fixed latency 3 cycles;
- II=1;
- output capacity: 2 records per 125 MHz cycle = 250 Mrecord/s.

---

## 12. Coordinates and topology groups

Use structured coordinates for lightweight replicated lanes or status banks:

```yaml
instances:
  - name: threshold_0
    module: threshold_rtl
    coordinates: {lane: 0}

  - name: threshold_1
    module: threshold_rtl
    coordinates: {lane: 1}
```

```yaml
topology_groups:
  - name: threshold_lanes
    producer_kind: normalized_pixel
    consumer_kind: threshold_input
    match_on: [lane]
```

Requirements:

- duplicate coordinates fail;
- invalid ranges fail;
- missing required coverage is diagnosed;
- matching evidence records the coordinate decision.

If duplicating a heavy HLS path is too expensive, coordinate coverage may use lightweight format/status modules.

---

## 13. Cardinality coverage

The valid design must exercise:

### Exactly one producer

```yaml
cardinality:
  producers: {min: 1, max: 1}
  consumers: {min: 1, max: 1}
```

### Fan-out

Normalized pixels feed Sobel, threshold, and decimator:

```yaml
cardinality:
  producers: {min: 1, max: 1}
  consumers: {min: 2, max: 3}
  fanout: allowed
```

### Optional debug consumer

An optional probe/status consumer may be absent.

### Invalid fixture

`invalid_cardinality.yml` contains either:

- two producers driving a non-gather sink; or
- one required consumer with no producer.

The diagnostic must identify all endpoints involved.

---

## 14. Transformation coverage

| Transformation | Example use |
|---|---|
| `pipeline-register` | Optional timing stage |
| `alignment-delay` | Six-cycle threshold compensation |
| `physical-boundary` | Optional SLR fixture |
| `width-adapter` | 8-bit pixel to wider compute word |
| `protocol-adapter` | Valid-only to ready/valid test |
| `fanout` | Normalizer to three branches |
| `gather` | Edge/mask/statistics merge |
| `scatter` | Optional replicated-lane distribution |
| `cdc-level-synchronizer` | Enable and error status |
| `cdc-pulse-synchronizer` | Apply and frame-done |
| `cdc-mailbox` | Configuration bundle |
| `async-fifo` | Pixel-to-output stream |
| `reset-synchronizer` | One per destination domain |
| `tie-off` | Optional unused input |
| `constant-source` | Default mode/configuration |

Explicit declarations are acceptable; FORGE must never infer unsafe CDC from names alone.

---

## 15. Latency model

### 15.1 Fixed pixel-domain segment

```text
normalizer:          3 cycles
sobel branch:        8 cycles
threshold branch:    2 + 6 delay = 8 cycles
edge/mask merge:     1 cycle
--------------------------------
fixed segment:      12 pixel cycles
```

### 15.2 Bounded branch

`tile_stats_hls`:

```text
6..10 pixel cycles
```

measured from a clearly documented accepted event, such as final tile sample.

### 15.3 Elastic join

`metadata_join_rtl` depends on:

- statistics arrival;
- buffering;
- downstream readiness.

It must not be reported as fixed.

### 15.4 CDC segment

The async FIFO is reported in destination-domain terms:

```yaml
latency:
  kind: bounded
  min_cycles: 2
  max_cycles: 5
  measured_in: output
```

or `elastic` if the implementation/backpressure model cannot guarantee a useful maximum.

### 15.5 End-to-end report

Do not collapse asynchronous domains into one false scalar.

```text
Pixel fixed segment:      12 clk_pixel cycles
Metadata association:     elastic, tile-tagged
Pixel->output CDC:         bounded/elastic in clk_output
Packetizer:                3 clk_output cycles
Wall-clock total:          observed per transaction
```

---

## 16. Runtime latency probes

Probe:

- normalizer input/output;
- Sobel input/output;
- threshold input/output;
- merge input/output;
- tile-statistics boundary/output;
- metadata-join input/output;
- FIFO write/read acceptance;
- packetizer input/output;
- frame-done source/destination.

Result fields:

```text
path
transaction_id
source_domain
destination_domain
predicted_kind
predicted_min
predicted_max
observed_latency
status
```

Statuses include:

- `within_fixed_contract`;
- `within_bounds`;
- `elastic_observed`;
- `below_minimum`;
- `above_maximum`;
- `missing`;
- `duplicated`.

---

## 17. Throughput model

### 17.1 Nominal capacities

| Stage | Clock | Capacity |
|---|---:|---:|
| Source | 200 MHz | 200 Mrecord/s |
| Normalizer | 200 MHz, II=1 | 200 Mrecord/s |
| Sobel | 200 MHz, II=1 | 200 Mrecord/s |
| Threshold | 200 MHz, II=1 | 200 Mrecord/s |
| Edge/mask merge | 200 MHz, II=1 | 200 Mrecord/s |
| Decimator output | 200 MHz | 100 Msample/s |
| Tile statistics | 200 MHz, II=2 | 100 Msample/s |
| Packetizer | 125 MHz, 2 records/cycle | 250 Mrecord/s |

Nominal sustainable system rate:

```text
200 Mrecord/s
```

### 17.2 Backpressure schedule

Example deterministic sink behavior:

```text
accept 32 output cycles
stall 8 output cycles
accept 16 output cycles
stall 4 output cycles
repeat
```

Report:

- accepted inputs;
- emitted outputs;
- filtered/decimated records;
- current occupancy;
- high-water occupancy;
- full events;
- stall cycles;
- average throughput;
- bottleneck.

### 17.3 Conservation invariant

During execution:

```text
accepted_inputs
- emitted_outputs
- intentionally_filtered_records
= buffered_or_inflight_records
```

After draining:

```text
accepted_inputs
- intentionally_filtered_records
= emitted_outputs
```

### 17.4 Invalid throughput fixture

Connect an II=1 producer to an unbuffered, non-backpressured II=2 consumer.

Expected diagnostic includes:

- producer rate;
- consumer rate;
- unsustainable ratio;
- suggested remedies: buffer, decimate, widen, increase clock, or reduce source rate.

### 17.5 Invalid FIFO fixture

Configure a burst/stall requirement that provably exceeds FIFO capacity.

Expected result:

- strict failure or release-gate error;
- required and configured depth shown.

---

## 18. Dataset acquisition and adaptation

The example must test not only simulation with checked-in XML fixtures, but also the complete path from an external or generated source dataset into FORGE's canonical verification events.

The dataset workflow is:

```text
raw source
  -> source adapter
  -> canonical frame/event objects
  -> deterministic preprocessing
  -> optional tiling/window extraction
  -> software golden model
  -> FORGE dataset serialization
  -> dataset manifest + content hashes
  -> verification flow
```

The adapter layer belongs to the example or to a documented FORGE dataset-extension API. It must not be hidden inside `gen_stimulus.py`.

### 18.1 Dataset tiers

Use three dataset tiers.

#### Tier A — Generated deterministic fixtures

Mandatory for every installation and CI run.

No network access or third-party files are required.

Generated patterns:

- constant frame;
- horizontal edge;
- vertical edge;
- corner;
- checkerboard;
- intensity ramp;
- deterministic pseudo-random noise;
- short burst;
- backpressure schedule;
- reset-mid-frame sequence;
- configuration-update sequence.

These datasets are the source of truth for:

- exact expected outputs;
- fixed latency;
- bounded latency;
- throughput;
- CDC event conservation;
- reset behavior;
- negative fixtures.

#### Tier B — User-provided image folder

Mandatory adapter example, but not dependent on a specific public dataset.

Accepted inputs:

```text
PNG
PGM
JPEG
BMP
NumPy .npy/.npz
```

The adapter converts a directory of images into the canonical pixel-event representation.

This allows a user to test FORGE with their own data without writing a new stimulus generator.

#### Tier C — Optional public datasets

Provide importer scripts for at least:

1. **BSDS500** for natural images and boundary/edge-oriented testing.
2. **Fashion-MNIST** for the optional hls4ml CNN and small grayscale-image tests.

The public datasets must not be downloaded automatically during normal installation or mandatory CI.

### 18.2 Recommended public sources

#### BSDS500

Purpose:

- real natural images;
- Sobel/threshold/edge-pipeline testing;
- optional comparison against human boundary annotations;
- variable image dimensions and realistic intensity distributions.

Official source:

```text
https://www2.eecs.berkeley.edu/Research/Projects/CS/vision/bsds/
```

The repository must not silently redistribute BSDS500 files. The importer should require the user to download/extract the dataset locally and accept a `--source` path.

The importer documentation must preserve the upstream usage/licensing conditions.

Representative command:

```bash
python -m vision_pipeline_demo.datasets.import_bsds500 \
  --source /path/to/BSR/BSDS500/data \
  --split test \
  --limit 8 \
  --resize 32x32 \
  --output forge/verify/generated/bsds500
```

#### Fashion-MNIST

Purpose:

- optional CNN training/evaluation;
- deterministic 28×28 grayscale tiles;
- small input size suitable for simulation;
- optional non-ML pipeline smoke tests.

Official source:

```text
https://github.com/zalandoresearch/fashion-mnist
```

Representative command:

```bash
python -m vision_pipeline_demo.datasets.import_fashion_mnist \
  --root .cache/fashion-mnist \
  --split test \
  --classes 0,1,5,8 \
  --samples-per-class 4 \
  --output forge/verify/generated/fashion_mnist
```

Downloading must be opt-in. The importer must support an offline mode using already downloaded files.

### 18.3 Canonical dataset event model

Every adapter must normalize its source into the same typed model before serialization.

Representative Python API:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class PixelTransaction:
    """One logical input transaction presented to the pixel pipeline."""

    pixel: int
    x: int
    y: int
    frame_id: int
    tile_id: int
    end_of_line: bool
    end_of_frame: bool


@dataclass(frozen=True)
class ControlTransaction:
    """A control-plane action associated with an event."""

    cycle: int
    action: str
    values: Mapping[str, int | bool]


@dataclass(frozen=True)
class SinkSchedule:
    """Deterministic downstream-ready behavior."""

    ready_by_cycle: Sequence[bool]


@dataclass(frozen=True)
class DatasetEvent:
    """Canonical event consumed by the golden model and dataset writers."""

    event_id: str
    width: int
    height: int
    pixels: Sequence[PixelTransaction]
    controls: Sequence[ControlTransaction] = ()
    sink_schedule: SinkSchedule | None = None
    labels: Mapping[str, Any] = field(default_factory=dict)
    source_metadata: Mapping[str, Any] = field(default_factory=dict)


class DatasetAdapter(Protocol):
    """Adapter from one raw source format to canonical FORGE events."""

    name: str
    version: str

    def iter_events(self) -> Iterable[DatasetEvent]:
        """Yield deterministic canonical events in stable order."""
        ...
```

The real implementation may use a different package location, but all adapters must share one canonical event model.

### 18.4 Required adapters

Implement the following adapters.

#### `SyntheticPatternAdapter`

Inputs:

- frame dimensions;
- pattern list;
- deterministic seed;
- event count;
- optional control actions;
- optional sink-ready schedule.

Outputs:

- complete canonical events;
- no external dependencies beyond the normal example installation.

#### `ImageFolderAdapter`

Inputs:

- directory or explicit file list;
- accepted extensions;
- resize/crop policy;
- grayscale conversion;
- tile dimensions;
- maximum event count.

Behavior:

- stable lexical input order unless an explicit manifest provides another order;
- image hash recorded before preprocessing;
- converted image hash recorded after preprocessing;
- unsupported/corrupt files produce actionable diagnostics;
- no random augmentation in acceptance datasets.

#### `NumpyArrayAdapter`

Inputs:

- `.npy` or `.npz`;
- array key;
- expected layout;
- value range;
- optional label array.

Supported layouts should be explicit:

```text
N,H,W
N,H,W,C
H,W
```

The adapter must reject ambiguous or unsupported shapes rather than guessing.

#### `BSDS500Adapter`

Inputs:

- local extracted dataset root;
- split;
- selected IDs;
- resize/crop policy;
- optional ground-truth boundary import.

Outputs:

- image events;
- source image identifiers;
- optional boundary labels kept as verification metadata rather than DUT inputs.

#### `FashionMNISTAdapter`

Inputs:

- local dataset root or explicitly enabled downloader;
- split;
- classes;
- selected indices;
- sample count.

Outputs:

- 28×28 grayscale events;
- class labels in metadata;
- stable sample IDs;
- optional normalized tensors for the hls4ml extension.

### 18.5 Deterministic preprocessing contract

Every adapter must declare preprocessing explicitly.

Representative manifest:

```yaml
preprocessing:
  grayscale:
    method: luma_integer
    coefficients: [77, 150, 29]
    shift: 8

  resize:
    method: nearest
    width: 32
    height: 32

  value_mapping:
    source_min: 0
    source_max: 255
    output_min: 0
    output_max: 255
    rounding: nearest
    saturation: true

  tiling:
    width: 8
    height: 8
    stride_x: 8
    stride_y: 8
    border: drop
```

Do not rely on library defaults for:

- RGB-to-grayscale conversion;
- interpolation;
- rounding;
- value scaling;
- channel ordering;
- border handling;
- tile order.

These choices affect bit-exact expected outputs and must be part of the dataset hash/provenance.

### 18.6 Dataset manifest

Every converted dataset must have a sidecar manifest.

Example:

```yaml
schema:
  name: forge.dataset_manifest
  version: "1.0"

dataset:
  id: bsds500_test_32x32_v1
  adapter: bsds500
  adapter_version: "1.0"
  created_by: vision_pipeline_demo
  event_count: 8
  deterministic: true

source:
  dataset_name: BSDS500
  split: test
  local_root: null
  selected_ids:
    - "100007"
    - "100039"
  upstream_reference: bsds500
  redistribution: false

hashes:
  source_files:
    "100007.jpg": "sha256:..."
    "100039.jpg": "sha256:..."
  preprocessing_config: "sha256:..."
  canonical_events: "sha256:..."
  serialized_dataset: "sha256:..."

preprocessing:
  grayscale: luma_integer
  resize: nearest
  output_size: [32, 32]
  pixel_format: uint8

serialization:
  format: forge-xml
  schema_version: "1.0"
  output_files:
    - bsds500_test_32x32.xml

golden_model:
  implementation: vision_pipeline_demo.golden_model
  version: "1.0"
  expected_output_hash: "sha256:..."
```

Local absolute source paths must not affect the portable semantic dataset hash.

### 18.7 Serialization targets

The canonical events should support:

#### FORGE XML

Mandatory while XML is the built-in verification format.

```text
canonical events -> XML dataset -> gen_stimulus.py -> simulator
```

#### JSON preview

Human-readable debugging output:

```text
dataset.preview.json
```

It is not necessarily the simulation format.

#### NPZ cache

Optional compact cache:

```text
dataset.events.npz
```

Useful for large arrays and hls4ml software evaluation.

The NPZ cache must not become the only source of semantic metadata; the manifest remains authoritative.

### 18.8 Golden-output generation

Dataset adaptation and expected-output calculation are separate stages.

```text
raw image
  -> adapter/preprocessing
  -> canonical input event
  -> independent software golden model
  -> expected output event
  -> serializer
```

The adapter must not calculate expected DUT behavior itself.

Representative command:

```bash
python -m vision_pipeline_demo.datasets.build \
  --adapter image-folder \
  --source ./my_images \
  --config forge/datasets/configs/image_folder_32x32.yml \
  --golden-model vision_pipeline_demo.golden_model \
  --output forge/verify/generated/my_images
```

### 18.9 Proposed dataset CLI

Until FORGE has a generic top-level dataset command, provide example-local commands:

```bash
python -m vision_pipeline_demo.datasets generate-synthetic ...
python -m vision_pipeline_demo.datasets import-images ...
python -m vision_pipeline_demo.datasets import-numpy ...
python -m vision_pipeline_demo.datasets import-bsds500 ...
python -m vision_pipeline_demo.datasets import-fashion-mnist ...
python -m vision_pipeline_demo.datasets inspect <manifest.yml>
python -m vision_pipeline_demo.datasets validate <manifest.yml>
python -m vision_pipeline_demo.datasets rebuild <manifest.yml>
```

A later FORGE dataset-adapter API may expose equivalent commands:

```bash
forge dataset import
forge dataset inspect
forge dataset validate
forge dataset rebuild
```

Do not duplicate the adapter implementation when adding the wrapper.

### 18.10 Adding a new dataset

A user should need to implement only an adapter and configuration.

Process:

1. Implement `DatasetAdapter.iter_events()`.
2. Normalize source records into `DatasetEvent`.
3. Declare all preprocessing.
4. Run the independent golden model.
5. Serialize to FORGE XML.
6. Generate the manifest and hashes.
7. Validate event counts, ranges, and identifiers.
8. Add a verification flow referencing the generated dataset.
9. Add one deterministic adapter test.

Minimal adapter skeleton:

```python
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from vision_pipeline_demo.datasets.model import (
    DatasetAdapter,
    DatasetEvent,
    PixelTransaction,
)


class MyDatasetAdapter(DatasetAdapter):
    name = "my-dataset"
    version = "1.0"

    def __init__(self, source: Path) -> None:
        self._source = source

    def iter_events(self) -> Iterable[DatasetEvent]:
        for frame_id, image in enumerate(self._load_images()):
            height, width = image.shape

            pixels = []
            for y in range(height):
                for x in range(width):
                    pixels.append(
                        PixelTransaction(
                            pixel=int(image[y, x]),
                            x=x,
                            y=y,
                            frame_id=frame_id,
                            tile_id=self._tile_id(x=x, y=y),
                            end_of_line=(x == width - 1),
                            end_of_frame=(
                                x == width - 1 and y == height - 1
                            ),
                        )
                    )

            yield DatasetEvent(
                event_id=f"my-dataset-{frame_id:06d}",
                width=width,
                height=height,
                pixels=tuple(pixels),
                source_metadata={
                    "source": str(self._source.name),
                    "frame_index": frame_id,
                },
            )

    def _load_images(self):
        """Load and deterministically preprocess source images."""
        raise NotImplementedError

    @staticmethod
    def _tile_id(*, x: int, y: int) -> int:
        tile_width = 8
        tile_height = 8
        return (y // tile_height) * 1024 + (x // tile_width)
```

The production implementation must add type/range validation and deterministic source hashing.

### 18.11 Dataset validation

Validation must check:

- manifest/schema version;
- unique event IDs;
- stable event ordering;
- pixel range;
- coordinate range;
- exactly one end-of-frame marker;
- correct end-of-line markers;
- declared frame dimensions;
- tile IDs and ordering;
- control-action cycle validity;
- sink-schedule length;
- expected input/output counts;
- source and serialized hashes;
- golden-output hash;
- no unsupported random preprocessing.

### 18.12 Dataset adapter tests

Required tests:

- same source/config produces byte-identical canonical events;
- filesystem ordering does not change event order;
- source relocation does not change semantic hashes;
- one changed source pixel changes the canonical-event hash;
- one preprocessing change changes the preprocessing and dataset hashes;
- invalid shape/range fails cleanly;
- corrupt image fails with source filename;
- XML round-trip preserves event semantics;
- golden output is independent of adapter implementation;
- external downloader is never invoked without explicit opt-in;
- mandatory CI works with no network.

### 18.13 Repository layout

```text
plugins/vision_pipeline_demo/
  datasets/
    README.md
    model.py
    manifest.py
    serialize_xml.py
    golden_runner.py
    cli.py

    adapters/
      synthetic.py
      image_folder.py
      numpy_array.py
      bsds500.py
      fashion_mnist.py

    configs/
      synthetic_acceptance.yml
      image_folder_32x32.yml
      bsds500_32x32.yml
      fashion_mnist_28x28.yml

  forge/
    verify/
      schemas/data/
        constant_frame.xml
        horizontal_edge.xml
        vertical_edge.xml
        corner.xml
        checkerboard.xml
        ramp.xml
        short_burst.xml
        backpressure_sequence.xml
        reset_mid_frame.xml
        config_update_between_frames.xml

      generated/
        .gitignore
```

Recommended repository policy:

- check in small synthetic XML fixtures;
- check in their manifests;
- do not check in large downloaded datasets;
- do not check in generated caches;
- optionally check in a tiny third-party sample only after confirming redistribution terms;
- CI regenerates synthetic fixtures and compares their hashes;
- external-data jobs use a cache and explicit opt-in.

### 18.14 Dataset acceptance criteria

- [ ] Synthetic datasets require no network.
- [ ] Image-folder import works with user-provided images.
- [ ] NPZ import exercises a structured binary source.
- [ ] BSDS500 import is documented and reproducible from a local download.
- [ ] Fashion-MNIST import is optional and supports offline cached data.
- [ ] Every dataset has a versioned manifest.
- [ ] Source, preprocessing, canonical-event, serialized, and golden hashes are recorded.
- [ ] Preprocessing is explicit and bit-deterministic.
- [ ] XML fixtures use the same canonical event model as external adapters.
- [ ] A user can add a new adapter without modifying the golden model or simulator backend.
- [ ] Mandatory CI never depends on third-party network availability.
- [ ] External licenses/redistribution terms are documented and respected.

---

## 19. Verification datasets

```text
constant_frame.xml
horizontal_edge.xml
vertical_edge.xml
corner.xml
checkerboard.xml
ramp.xml
short_burst.xml
backpressure_sequence.xml
reset_mid_frame.xml
config_update_between_frames.xml
```

Metadata must include:

- schema version;
- dataset ID;
- generator version;
- deterministic seed where applicable;
- frame dimensions;
- pixel format;
- expected input/output counts;
- expected frames;
- content hash.

Provide a small independent Python golden model for normalization, thresholding, gradient, statistics, and packetization.

---

## 20. Verification flows

```text
quickstart_functional
full_functional
fixed_latency
bounded_latency
runtime_latency
throughput_steady_state
throughput_backpressure
fifo_occupancy
cdc_level
cdc_pulse
cdc_mailbox
cdc_stream
reset_crossing
configuration_update
platform_wrapper
release_acceptance
```

### Required behavior

- `quickstart_functional`: minimal exact comparison.
- `full_functional`: all valid image patterns.
- `fixed_latency`: verifies 12 pixel cycles.
- `bounded_latency`: verifies statistics bounds.
- `runtime_latency`: produces static/runtime comparison.
- `throughput_steady_state`: one accepted record per pixel cycle.
- `throughput_backpressure`: stalls, occupancy, conservation.
- `cdc_level`: eventual level transfer.
- `cdc_pulse`: one destination event per legal source event.
- `cdc_mailbox`: atomic configuration updates.
- `cdc_stream`: no loss, duplication, or reordering.
- `reset_crossing`: synchronized release in each domain.
- `platform_wrapper`: wrapper generation and compilation.
- `release_acceptance`: mandatory aggregate gate.

---

## 21. Invalid-design matrix

| Design | Primary expected failure |
|---|---|
| `invalid_direct_bus_cdc.yml` | Direct multi-bit CDC |
| `invalid_bus_scalar_sync.yml` | Scalar synchronizer used for coherent bus |
| `invalid_missing_reset_sync.yml` | Unsynchronized reset release |
| `invalid_protocol.yml` | Protocol mismatch without adapter |
| `invalid_cardinality.yml` | Missing or multiple producer |
| `invalid_ambiguous_match.yml` | Multiple equally valid matches |
| `invalid_latency_alignment.yml` | Unequal exact-cycle reconvergence |
| `invalid_throughput.yml` | Producer exceeds consumer capacity |
| `invalid_fifo_depth.yml` | Required burst exceeds FIFO depth |
| `invalid_plan_hash.yml` | Accepted plan hash is stale |

Each fixture must assert one stable primary diagnostic code.

---

## 22. Generation plan

Plan output must include:

- design and plan schema versions;
- semantic design hash;
- plan hash;
- modules and domains;
- explicit/inferred connections;
- matching evidence;
- transformations;
- CDC crossings;
- latency effects;
- throughput assumptions;
- compatibility-mode use;
- diagnostics;
- expected artifacts.

Commands:

```bash
forge build --plan
forge build --apply
forge build --accept-plan-hash <hash>
```

`--plan` is strictly read-only.

The plan hash changes when:

- FIFO depth changes;
- CDC implementation changes;
- alignment delay changes;
- packet width changes;
- module implementation changes;
- HLS II/reuse changes;
- protocol changes;
- emission-relevant ordering changes.

---

## 23. Matching evidence

For every connection record:

- producer;
- consumer;
- wiring method;
- interface type;
- protocol;
- width;
- coordinates;
- cardinality result;
- source/destination domains;
- CDC requirement;
- selected transformation;
- candidates considered;
- rejected candidates and reasons;
- source configuration location.

Example:

```text
normalizer.pixels_out -> sobel.pixels_in

selected:
  wiring_kind = normalized_pixel
  interface = forge.pixel_stream.v1
  protocol = ready-valid
  lane = 0
  width = compatible
  domains = pixel -> pixel
  cardinality = satisfied

rejected:
  tile_stats.samples_in: wiring_kind mismatch
  packetizer.records_in: interface and domain mismatch
```

---

## 24. Provenance

Record:

- FORGE version;
- all schema versions;
- toolchain versions;
- semantic IR hash;
- plan hash;
- source/contract hashes;
- dataset hashes;
- HLS source/configuration hashes;
- generated artifact hashes where practical;
- command options.

Changing a dataset invalidates affected verification results. Changing an interface contract invalidates topology, plan, and affected verification.

---

## 25. Visual outputs

### Static SVG

Show:

- hierarchy;
- domains;
- interfaces;
- connections;
- transformations;
- CDC;
- warnings/errors;
- contract-driven versus compatibility wiring.

### Interactive HTML

Support:

- pan/zoom;
- search;
- hierarchy collapse;
- domain/protocol/transformation filters;
- latency overlay;
- throughput overlay;
- CDC-only view;
- diagnostics-only view;
- node and edge details;
- matching evidence.

Recommended layout:

```text
control domain | pixel domain | CDC | output domain
```

---

## 26. Generic platform wrapper

Include a generic platform fixture with:

- control clock/reset;
- pixel clock/reset;
- output clock/reset;
- input stream;
- output stream;
- status interface.

Generate and compile the supported `payload.v` or equivalent wrapper without detector-specific terminology.

---

## 27. Optional hls4ml extension

Add only after the base design passes all mandatory criteria.

### 26.1 Topology

```text
normalized tiles
      |
feature_window_builder
      |
async FIFO: pixel -> ml
      |
tiny_hls4ml_cnn
      |
async FIFO: ml -> pixel
      |
metadata_join_rtl
```

### 26.2 Suggested network

```text
Input: 8×8×1 grayscale tile
Conv2D: 4 filters, 3×3, ReLU
MaxPool: 2×2
Conv2D: 8 filters, 3×3, ReLU
GlobalAveragePooling or Flatten
Dense: 4 outputs
```

Classes:

```text
no_edge
horizontal_edge
vertical_edge
corner_or_complex
```

### 26.3 Variants

Latency-oriented:

```yaml
Strategy: Latency
ReuseFactor: 1
IOType: io_stream
```

Resource-oriented:

```yaml
Strategy: Resource
ReuseFactor: 4
IOType: io_stream
```

Compare:

- latency;
- II;
- resources;
- system bottleneck;
- CDC/FIFO behavior;
- plan hash;
- provenance.

### 26.4 Dependency rule

hls4ml remains optional:

```bash
pip install -e ".[hls4ml]"
```

The default suite skips optional ML jobs cleanly when dependencies/tools are absent.

### 26.5 hls4ml provenance

Record:

- hls4ml version;
- model/frontend format;
- backend;
- architecture/weight hashes;
- configuration hash;
- generated-project hash;
- precision;
- reuse factor;
- strategy;
- I/O type;
- target clock;
- Vitis HLS version.

FORGE may propose physical bindings, but the semantic contract must be reviewed and checked in.

---

## 28. Repository structure

```text
plugins/vision_pipeline_demo/
  README.md

  algo/
    rtl/
      control_registers_rtl.sv
      pixel_source_rtl.sv
      threshold_rtl.sv
      edge_mask_merge_rtl.sv
      sample_decimator_rtl.sv
      metadata_join_rtl.sv
      packetizer_rtl.sv
      cdc/
        level_sync.sv
        pulse_sync.sv
        config_mailbox.sv
        async_fifo.sv
        reset_sync.sv

    hls/
      pixel_normalizer/
      sobel/
      tile_stats/

    optional/hls4ml/
      model/
      config/
      generated/
      scripts/

  forge/
    modules.yml
    designs/
      quickstart.yml
      full_acceptance.yml
      full_acceptance_backpressure.yml
      full_acceptance_resource_variant.yml
      invalid/
      optional/

    interfaces/
      control_registers.interface.yaml
      pixel_source.interface.yaml
      pixel_normalizer.interface.yaml
      sobel.interface.yaml
      threshold.interface.yaml
      edge_mask_merge.interface.yaml
      sample_decimator.interface.yaml
      tile_stats.interface.yaml
      metadata_join.interface.yaml
      packetizer.interface.yaml
      cdc_level_sync.interface.yaml
      cdc_pulse_sync.interface.yaml
      cdc_mailbox.interface.yaml
      async_fifo.interface.yaml
      reset_sync.interface.yaml

    platforms/
      generic_streaming_platform.yml

    verify/
      design.verification.yml
      tools/
        bootstrap.py
        gen_stimulus.py
        golden_model.py
      schemas/data/
      <generated flow directories>

  docs/
    quickstart.md
    architecture.md
    contracts.md
    clocks-resets-cdc.md
    latency-throughput.md
    verification.md
    invalid-designs.md
    hls4ml-extension.md
```

Use the repository’s current `plugins/` convention until a separate terminology migration is approved.

---

## 29. CI matrix

### Fast merge-request CI

- schema/contract validation;
- deterministic IR and plan hash;
- Python tests;
- RTL lint;
- HLS C simulation where affordable;
- quickstart generation;
- invalid-fixture diagnostics;
- example documentation checks.

### Standard acceptance CI

On supported tool runners:

- complete top generation;
- XSim compile/elaborate/simulate;
- fixed/bounded latency;
- CDC tests;
- throughput/backpressure;
- wrapper compilation;
- SVG/HTML generation.

### Scheduled HLS CI

- synthesize conventional HLS modules;
- parse latency, II, and resources;
- compare declared and reported timing;
- export IP;
- run mixed RTL/HLS integration.

### Optional hls4ml CI

Separate non-core job:

- install pinned optional dependencies;
- generate the CNN project;
- verify provenance;
- synthesize or C-simulate;
- integrate and verify both configurations.

---

## 30. Implementation order

### A. Quickstart

- normalizer HLS;
- threshold RTL;
- one clock;
- one dataset/flow;
- basic diagram.

### B. Fixed-latency parallel pipeline

- Sobel;
- fan-out;
- alignment delay;
- exact-cycle merge.

### C. Bounded/elastic metadata path

- decimator;
- tile statistics;
- tagged join;
- bounded/elastic reports.

### D. Domains and CDC

- control/output domains;
- reset domains;
- level/pulse/mailbox/stream CDC;
- negative CDC fixtures.

### E. Throughput/backpressure

- packetizer;
- occupancy;
- deterministic stalls;
- bottleneck and invalid-rate checks.

### F. Release artifacts

- platform wrapper;
- generation-plan tests;
- provenance;
- SVG/HTML;
- release aggregate flow.

### G. Optional hls4ml

- tiny CNN;
- ML domain;
- feature/result FIFOs;
- latency/resource variants;
- optional CI.

---

## 31. Acceptance checklist

### Functional

- [ ] Quickstart matches golden output.
- [ ] Full design matches golden output.
- [ ] No unintended loss or duplication.
- [ ] Configuration changes are atomic.
- [ ] Reset recovery is clean.

### Contracts/topology

- [ ] All modules have valid contracts.
- [ ] Valid acceptance design has no heuristic wiring in strict mode.
- [ ] Coordinates, cardinality, protocols, and grouped members validate.
- [ ] Matching evidence is complete.

### Clock/reset/CDC

- [ ] Every instance resolves to clock/reset domains.
- [ ] Every crossing has an approved CDC.
- [ ] Direct bus CDC is rejected.
- [ ] Scalar-sync misuse is rejected.
- [ ] Level, pulse, mailbox, FIFO, and reset behavior pass.

### Latency

- [ ] Fixed 12-cycle pixel segment passes.
- [ ] Statistics remains within bounds.
- [ ] Elastic paths are not reported as fixed.
- [ ] Static/runtime results agree for fixed contracts.
- [ ] Edge, adapter, and CDC latency are separate.
- [ ] Uncompensated exact-cycle join fails.
- [ ] Cross-domain latency is not collapsed into one cycle count.

### Throughput

- [ ] II=1 path sustains one record/pixel cycle.
- [ ] II=2 branch receives an appropriate sampled/buffered rate.
- [ ] Nominal system sustains 200 Mrecord/s.
- [ ] Backpressure preserves transactions.
- [ ] Occupancy/high-water is reported.
- [ ] Valid burst does not overflow.
- [ ] Invalid rate/depth fixtures fail.
- [ ] Bottleneck is identified correctly.

### Generation/provenance

- [ ] Plan is deterministic and read-only.
- [ ] Plan hash changes for emission-relevant changes.
- [ ] Provenance contains required hashes/versions.
- [ ] Staleness identifies changed inputs.
- [ ] Dry-run writes nothing.

### Reports/documentation

- [ ] Required verification flows pass.
- [ ] Invalid fixtures fail for intended reasons.
- [ ] SVG is generated.
- [ ] Interactive HTML opens offline.
- [ ] Quickstart works from a clean environment.
- [ ] Optional hls4ml requirements are clearly separated.

---

## 32. Non-goals

The project does not need to:

- deliver production computer vision;
- process HD/4K video;
- implement a camera protocol;
- train an ML model;
- support every hls4ml frontend/backend;
- replace vendor CDC/timing tools;
- benchmark all FPGA families;
- implement a graphical editor;
- prove ASIC support.

---

## 33. Definition of success

A new user can:

1. complete the quickstart without CERN or detector knowledge;
2. inspect what FORGE resolved;
3. understand why each connection was selected;
4. see every domain, CDC, delay, FIFO, and adapter;
5. run functional, latency, and throughput verification;
6. break a design deliberately and receive an actionable diagnostic;
7. compare static and observed timing;
8. reproduce outputs from committed inputs;
9. optionally integrate the hls4ml CNN without changing FORGE core.

The example becomes the public proof of the FORGE chain:

```text
contracts
  -> canonical design resolution
  -> generated topology
  -> transformations and CDC planning
  -> verification
  -> latency/throughput analysis
  -> provenance
  -> graphical explanation
```
