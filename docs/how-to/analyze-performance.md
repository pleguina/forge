# How to Analyze Performance

`forge analyze` provides five sub-commands for measuring, checking, visualising, and
reporting on your plugin's performance after it has been built and simulated.

```
forge analyze hls-report       # HLS synthesis metrics table (CSV / MD / HTML)
forge analyze latency-check    # Static per-path latency mismatch detector
forge analyze runtime-latency  # HLS-predicted vs simulation-observed latency
forge analyze plot-results     # Render comparison PNG figures
forge analyze dashboard        # Aggregate all artifacts into one HTML page
```

---

## 1. What each command needs

### 1.1 `hls-report`

| Input | Source |
|-------|--------|
| `build_hls/` tree with `csynth.xml` per module | Produced by `forge hls run --stages synth` |

```bash
forge analyze hls-report \
  --hls-build-root build_hls_<plugin> \
  --output out/reports
```

Writes `hls_summary.csv`, `hls_summary.md`, `hls_summary.html` to the output
directory.  Each row covers one synthesised module:

| Column | Meaning |
|--------|---------|
| Fmax (MHz) | Estimated maximum clock frequency |
| Slack (ns) | Timing slack against target clock |
| II | Pipeline initiation interval |
| Lat (W) | Worst-case latency in clock cycles |
| LUT / FF / DSP / BRAM | Resource usage |

**Plugin requirement:** HLS modules must have been synthesised (stage `synth`
or later).  Pure `csim`-only builds produce no `csynth.xml` and are reported as
`missing`.

---

### 1.2 `latency-check`

Builds a directed latency graph from `design.yml` + `modules.yml`, identifies
all merge points (nodes with ≥ 2 incoming edges), and reports whether the
accumulated latency is equal on every incoming path.  A mismatch means data
from two branches arrives at different cycles, which is almost always a bug.

```bash
forge analyze latency-check plugins/<plugin>/forge/designs/design.yml \
  --contracts-from plugins/<plugin>/forge/modules.yml \
  --hls-build-root build_hls_<plugin> \
  --output out/reports/latency_check.md
```

**Latency resolution order** (first match wins):

| Priority | Source | `modules.yml` field |
|----------|--------|---------------------|
| 1 | Explicit override | `latency_cycles: <N>` |
| 2 | HLS synthesis report (`csynth.xml`) | *(automatic when `--hls-build-root` is given)* |
| 3 | Manual hint | `latency_hint: <N>` |
| 4 | Unknown | *(warning in report)* |

**Plugin requirement — annotate every module:**

```yaml
# modules.yml
modules:

- name: hit_decoder
  kind: hls
  latency_hint: 0        # combinational: ap_ctrl_none, no pipeline registers

- name: trigger_logic
  kind: hls
  latency_hint: 3        # HLS LATENCY min=3 max=3 — overridden by hls_report
                         # once synth runs; keep as fallback

- name: trigger_fanout
  kind: rtl
  latency_hint: 0        # combinational pass-through RTL

- name: trigger_logic    # multi-cycle but variable
  variable_latency: true
  latency_hint: 4
```

For a **linear pipeline** (no fan-in merge points) the check always passes;
it becomes meaningful once you have branches that recombine, e.g. a parallel
decoder fan-out that merges into a collector.

---

### 1.3 `runtime-latency`

Compares the HLS-predicted latency for a named module against the
**simulation-observed** end-to-end latency measured from a probe CSV.

```bash
forge analyze runtime-latency \
  --probe-csv out/reports/pipeline_probe.csv \
  --probe-pairs "trigger_logic:dec_0_raw_valid:tout_out_valid" \
  --hls-build-root build_hls_<plugin> \
  --output out/reports/runtime_latency.md
```

**`--probe-pairs` format:** `module_name:input_valid_signal:output_valid_signal`

- `module_name` must match a key in `modules.yml` so the HLS-predicted latency
  can be looked up.
- `input_valid_signal` / `output_valid_signal` are signal name strings that
  appear in the probe CSV.

**Probe CSV format** (`cycle,signal,value` — long format):

```csv
cycle,signal,value
0,dec_0_raw_valid,1
10,tout_out_valid,1
8,dec_0_raw_valid,1
18,tout_out_valid,1
```

Latency is measured as `first_output_valid_cycle − first_input_valid_cycle`.

**Plugin requirement — produce a probe CSV.**  The framework does not
auto-generate it; the plugin is responsible for writing this file.  Two
approaches work:

*Option A — write it manually from simulation output:*

```python
# post-process algo_top_outputs.csv after forge verify run
import csv

input_events  = [0, 8, 16]   # cycles where dec_0_raw_valid was driven high
output_csv    = "...xsim_work/algo_top_outputs.csv"

with open("out/reports/pipeline_probe.csv", "w") as f:
    f.write("cycle,signal,value\n")
    for c in input_events:
        f.write(f"{c},dec_0_raw_valid,1\n")
    for row in csv.DictReader(open(output_csv)):
        if row["tout_out_valid"] == "1":
            f.write(f"{row['cycle']},tout_out_valid,1\n")
```

*Option B — add probe logging to the testbench (PROBE_LOG macro):*

```systemverilog
`ifdef PROBE_LOG
  integer probe_fd;
  initial probe_fd = $fopen("pipeline_probe.csv", "w");
  initial $fwrite(probe_fd, "cycle,signal,value\n");
  always @(posedge ap_clk) begin
    if (dec_0_raw_valid)
      $fwrite(probe_fd, "%0d,dec_0_raw_valid,1\n", cycle_count);
    if (tout_out_valid)
      $fwrite(probe_fd, "%0d,tout_out_valid,1\n", cycle_count);
  end
`endif
```

Enable with `forge verify run --probe-log` (passes `-d PROBE_LOG=1` to xvlog).

---

### 1.4 `plot-results`

Renders PNG comparison figures from an observed CSV and an optional reference
CSV.  The plot definitions live in a **plugin-owned** `plot_config.yml`.

```bash
forge analyze plot-results \
  --config plugins/<plugin>/forge/verify/plot_config.yml \
  --observed out/reports/observed.csv \
  --reference out/reports/reference.csv \
  --output out/reports/plots
```

**Plugin requirement — provide `plot_config.yml`:**

```yaml
# plugins/<plugin>/forge/verify/plot_config.yml
plots:

  - name: out_valid_timeline     # becomes out_valid_timeline.png
    kind: line                   # line | scatter | bar | histogram
    x: cycle
    y: tout_out_valid
    title: "Output Valid Timeline"
    xlabel: "Clock Cycle (@ 250 MHz)"
    ylabel: "tout_out_valid"
    figsize: [14, 4]

  - name: trigger_quality
    kind: scatter
    x: cycle
    y: trigger_quality
    title: "Trigger Quality per Event (observed vs reference)"
    xlabel: "Output Cycle"
    ylabel: "trigger_quality"
```

**Observed / reference CSV format** — one row per data point, column names
must match `x` and `y` fields in `plot_config.yml`:

```csv
cycle,tout_out_valid,trigger_quality,trigger_accept
10,1,3,1
18,1,1,0
26,1,4,1
```

The framework does not dictate what columns exist — those are fully
plugin-defined.  `matplotlib` must be installed (`pip install matplotlib`).

---

### 1.5 `dashboard`

Aggregates the outputs of the four commands above into a single
self-contained HTML file (plots embedded as base64).

```bash
forge analyze dashboard \
  --input out/reports \
  --output out/dashboard
```

The aggregator looks for these files inside `--input`:

| File | Produced by |
|------|-------------|
| `hls_summary.md` | `forge analyze hls-report` |
| `latency_check.md` | `forge analyze latency-check` |
| `runtime_latency.md` | `forge analyze runtime-latency` |
| `plots/*.png` | `forge analyze plot-results` |

Any missing file is silently skipped and its section shows "not available".

---

## 2. Full workflow (example: trigger_demo)

```bash
# 1. Build HLS (synth stage required for latency numbers)
forge hls run \
  --registry plugins/trigger_demo/forge/modules.yml \
  --hls-build-root build_hls_trigger_demo \
  --stages synth

# 2. Run verification to produce simulation output CSVs
./run_trigger_demo.sh --skip-hls --no-clean

# 3. (Plugin step) build probe CSV + observed/reference CSVs from xsim output
python3 scripts/build_analyze_inputs.py   # plugin-provided script

# 4. HLS synthesis report
forge analyze hls-report \
  --hls-build-root build_hls_trigger_demo \
  --output out/reports

# 5. Static latency check
forge analyze latency-check plugins/trigger_demo/forge/designs/design.yml \
  --contracts-from plugins/trigger_demo/forge/modules.yml \
  --hls-build-root build_hls_trigger_demo \
  --output out/reports/latency_check.md

# 6. Runtime latency (end-to-end pipeline measurement)
forge analyze runtime-latency \
  --probe-csv out/reports/pipeline_probe.csv \
  --probe-pairs "trigger_logic:dec_0_raw_valid:tout_out_valid" \
  --hls-build-root build_hls_trigger_demo \
  --output out/reports/runtime_latency.md

# 7. Result plots
forge analyze plot-results \
  --config plugins/trigger_demo/forge/verify/plot_config.yml \
  --observed out/reports/observed_pipeline.csv \
  --reference out/reports/reference_pipeline.csv \
  --output out/reports/plots

# 8. HTML dashboard
forge analyze dashboard \
  --input out/reports \
  --output out/dashboard
# → open out/dashboard/dashboard.html
```

---

## 3. Plugin author checklist

| Step | File to create / field to add | Command that uses it |
|------|-------------------------------|----------------------|
| Annotate all modules with known latency | `modules.yml`: `latency_hint: N` or `latency_cycles: N` | `latency-check` |
| Mark variable-latency modules | `modules.yml`: `variable_latency: true` | `latency-check` |
| Provide plot definitions | `plugins/<plugin>/forge/verify/plot_config.yml` | `plot-results` |
| Produce probe CSV (long-format) | script or testbench `PROBE_LOG` | `runtime-latency` |
| Produce observed CSV (wide-format) | post-process xsim output CSV | `plot-results` |
| Produce reference CSV (wide-format) | golden stimulus data | `plot-results` |

---

## 4. Understanding the latency delta

The `runtime-latency` delta (`observed − HLS_predicted`) accounts for all
pipeline stages **between** the two probe points that are not inside the named
module:

```
observed latency = HLS module latency
                 + register_stages on upstream connections
                 + delay_cycles on downstream connections
                 + combinational pass-through stages (≈ 0 each)
```

Example from trigger_demo (probe: `dec_0_raw_valid → tout_out_valid`):

```
HLS predicted (trigger_logic alone) =  3 cycles
+ register_stages: 2  (col → trig)  =  2 cycles
+ delay_cycles: 3     (trig → tfan) =  3 cycles
+ dec / col / tfan / tsink / tout   =  2 cycles  (combinational overhead)
─────────────────────────────────────────────────
Observed (simulation)               = 10 cycles   Δ = +7
```

For the conceptual model behind timing metadata (`latency_cycles` /
`latency_hint` / `variable_latency`) that feeds `latency-check`, see
[Latency Model](../concepts/latency-model.md).
