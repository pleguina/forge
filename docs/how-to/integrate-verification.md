# How to Integrate Verification

This guide defines the current supported plugin interface for framework verification.

It is the canonical verification-integration guide for new plugins.

Use it together with:

- [Quickstart](../getting-started/quickstart.md) for the shortest supported adoption path
- [How to Author Topology Contracts](author-topology-contracts.md) for topology-generation and contract-driven wiring
- [How to Analyze Performance](analyze-performance.md) for latency measurement and performance reporting

Do not treat older repo notes or migration documents as normative unless they are linked from the support-classified framework surface.

## 1. Design goal

The intended minimal plugin authoring surface is:

```text
plugins/<plugin>/
    algo/                       ← implementation sources (C++, HDL)
    forge/                        ← FORGE integration capsule
        modules.yml             ← module registry
        designs/
            design.yml
        interfaces/
            <module>.interface.yaml
        verify/
            design.verification.yml   ← verification contract (authored)
            schemas/data/             ← dataset XML files (authored)
            tools/
                bootstrap.py          ← plugin identity (authored)
                gen_stimulus.py       ← stimulus generation (authored)
                <optional checker code>
```

Everything else (per-flow `verify.flow.yml`, `tb_*.sv`, `wave.tcl`) is
framework-owned, framework-generated, or plugin-generated from those authored inputs.

See [How to Author Topology Contracts](author-topology-contracts.md) for the full `forge/` layout rules and path anchor conventions.

## 2. Current framework-standard verification contract

For the current v1.0 framework verification surface:

- datasets are XML-backed
- the authored verification contract is `design.verification.yml`
- the framework generates per-flow `verify.flow.yml`, `tb_<module>.sv`, and `wave.tcl`
- the plugin generates `stimulus_current.svh`

Framework-standard run selectors for XML-backed flows are:

- `--xml-input`
- `--event-id`
- `--event-list`
- `--all-events`

If your plugin uses those directly, that is now part of the public framework contract rather than an accidental OMTF convention.

## 3. What the framework owns

The framework owns the generic verification lifecycle:

- loading `design.verification.yml`
- generic flow validation and normalization
- plugin bootstrap guards
- framework backend dispatch
- generation of `verify.flow.yml`
- generation of `tb_<module>.sv`
- generation of `wave.tcl`
- generic xsim and csim orchestration
- checker launch orchestration when declared in flow config
- checker argument formatting for generated paths such as `{observed_log}`,
  `{dataset_xml}`, `{flow_dir}`, and `{work_dir}`

These are generated or framework-owned and should not be treated as plugin-authored surfaces for a new plugin:

```text
plugins/<plugin>/forge/verify/<flow>/
    verify.flow.yml
    tb_<module>.sv
    wave.tcl
```

## 4. What the plugin owns

The plugin owns only domain-specific behavior:

- dataset content and semantics
- interpretation of XML-backed events or transactions
- stimulus generation into `stimulus_current.svh`
- optional semantic checking beyond generic pass/fail

The framework may standardize that the dataset is XML-backed, but it does not know your detector, protocol, or domain semantics.

## 5. Required authored files

### 5.1 `design.verification.yml`

This is the single authored verification contract for a plugin. It declares:

- plugin id
- datasets
- simulation defaults
- verification flows

Example:

```yaml
plugin: myplugin

datasets:
  my_dataset:
    xml: schemas/data/events.xml   # relative to forge/verify/ (this file's directory)

defaults:
  clk_period_ns: 4.0
  reset_cycles: 4
  idle_cycles_after_reset: 0
  post_stimulus_drain_cycles: 8

flows:
  - name:             full_chip_my_top_xsim
    kind:             full_chip_rtl
    backend:          xsim
    top_module:       my_top
    tb_module:        tb_my_top
    dut_rtl_source:   out/algorithm
    dataset:          my_dataset
    default_event_id: 1
```

### 5.2 `tools/bootstrap.py`

This file declares plugin identity and marks the plugin bootstrapped.

For a simple plugin using framework-owned backends, bootstrap should be close to identity-only.

### 5.3 `tools/gen_stimulus.py`

This is the main plugin-specific verification code.

For each RTL flow, it must generate:

```text
<flow_dir>/stimulus_current.svh
```

That file must define:

```systemverilog
task automatic run_stimulus();
endtask
```

The framework-generated testbench includes and calls it automatically.

### 5.4 `schemas/data/...`

The plugin must ship its reference datasets under `schemas/data/` so the verification contract can refer to them consistently.

### 5.5 Optional checker

If the generated TB plus `run_stimulus()` can self-check using assertions or `$fatal`, no external checker is required.

If not, the plugin may provide a checker binary or script that evaluates the framework-generated observation CSV.

For large XML-backed datasets split across multiple files, declare the parts in
the dataset block generated into `verify.flow.yml`:

```yaml
dataset:
    xml: default_smoke.xml
    parts_glob: data/TestEvents_part*.xml
```

Then run the complete sweep with:

```bash
forge verify run path/to/verify.flow.yml --all-dataset-parts
```

FORGE executes each XML part through the normal backend and checker lifecycle,
using an isolated `xsim_work/dataset_parts/<part-name>/` directory per part, and
prints an aggregate PASS/FAIL table. Use `--dataset-parts-glob` to override the
declared glob for a temporary subset.

Checker arguments are declared in the authored `design.verification.yml` and
rendered into generated `verify.flow.yml`. The framework formats standard
placeholders at run time:

| Placeholder | Meaning |
|---|---|
| `{observed_log}` | Observation CSV declared by the checker block |
| `{dataset_xml}` | Dataset selected by `--xml-input` or the flow default |
| `{event_id}` | Selected single-event ID |
| `{latency_cycles}` | Checker latency value from the flow |
| `{tolerance}` | Checker tolerance value from the flow |
| `{consumer_root}` | Resolved consumer/plugin root |
| `{flow_dir}` | Directory containing generated `verify.flow.yml` |
| `{work_dir}` | Backend working directory, e.g. xsim compile/sim output area |

Use `{work_dir}` for checker-generated reports, plots, and machine-readable
artifacts. The framework supplies the location; the plugin still owns the
domain-specific contents of those artifacts.

## 6. What should not be required from a new plugin

A new plugin should not need to author any of the following unless it is working around a real framework gap or deliberately extending the public contract:

- `backend_xsim.py`
- `backend_csim.py`
- `flow_config.py`
- `runtime_context.py`
- hand-authored `verify.flow.yml`
- hand-authored `tb_<module>.sv`
- hand-authored `wave.tcl`
- hand-authored flow-local `port_map.yaml`

If a plugin requires those, treat it as either:

- a framework gap
- an intentional extension beyond the public v1.0 contract
- a temporary migration state

## 7. Stimulus handoff contract

For every RTL simulation flow, the plugin generator writes:

```text
<flow_dir>/stimulus_current.svh
```

The framework-generated testbench includes and calls it automatically.

The framework does not:

- interpret your domain semantics
- derive cycle-accurate port drives from golden data by itself
- know your protocol-specific input meaning

That translation remains plugin-specific.

## 8. If you want a dataset standard other than XML

The current public framework verification contract is XML-backed. If a new user wants another standard, the recommended order is:

### 8.1 Preferred: translate into framework-standard XML

Stay within the supported public contract by converting your native source format into XML before verification.

This can be done:

- as a pre-generation step
- inside plugin stimulus preparation
- or as a checked-in generated dataset when appropriate

### 8.2 Extension path: own the deviation in the plugin

If XML is unacceptable, the plugin must own that explicitly. In practice, this often means providing one or more of:

- plugin-specific flow loader extensions
- plugin-specific runtime context extensions
- plugin-specific backend overrides
- plugin-specific checker argument conventions

That path is valid, but it is outside the minimal public v1.0 verification contract.

### 8.3 Platform path: upstream a new standard

If more than one plugin needs the same non-XML model, it should be proposed as a new framework-standard contract rather than repeated as private plugin glue.

That requires:

1. framework schema changes
2. framework doc changes
3. explicit launcher/runtime semantics
4. tests and at least one real consumer proof path

Until then, XML remains the only framework-standard dataset contract.

## 9. Deployment checklist for a new plugin

Before calling a plugin integrated, verify that it has:

1. `design.verification.yml`
2. `tools/bootstrap.py`
3. `tools/gen_stimulus.py`
4. XML datasets under `schemas/data/`
5. optional checker only if self-checking is not enough

And verify that it does not require authored copies of generated artifacts.

## 10. Post-verification analysis

Once all flows pass, `forge analyze` provides latency measurement, static checks, plots,
and an HTML dashboard.  It requires a small amount of plugin-authored content:

| What | Where | Used by |
|------|-------|---------|
| `latency_hint: N` on each module | `modules.yml` | `forge analyze latency-check` |
| `plot_config.yml` with figure specs | `plugins/<plugin>/forge/verify/plot_config.yml` | `forge analyze plot-results` |
| Probe CSV (`cycle,signal,value`) | produced post-simulation | `forge analyze runtime-latency` |
| Observed + reference CSVs | produced post-simulation | `forge analyze plot-results` |

Quick run sequence (after `forge verify run` passes):

```bash
forge analyze hls-report \
    --hls-build-root build_hls_<plugin> --output out/reports

forge analyze latency-check plugins/<plugin>/forge/designs/design.yml \
    --contracts-from plugins/<plugin>/forge/modules.yml \
    --hls-build-root build_hls_<plugin> \
    --output out/reports/latency_check.md

forge analyze runtime-latency \
    --probe-csv out/reports/pipeline_probe.csv \
    --probe-pairs "<module>:<in_valid>:<out_valid>" \
    --hls-build-root build_hls_<plugin> \
    --output out/reports/runtime_latency.md

forge analyze plot-results \
    --config plugins/<plugin>/forge/verify/plot_config.yml \
    --observed out/reports/observed.csv \
    --reference out/reports/reference.csv \
    --output out/reports/plots

forge analyze dashboard --input out/reports --output out/dashboard
```

See [How to Analyze Performance](analyze-performance.md) for the complete reference.

## 10. Notes on current examples

`trigger_demo` is the maintained supported proof consumer for the current public framework path. Its FORGE capsule is at `plugins/trigger_demo/forge/`. It demonstrates multiple framework-owned flows, contract-driven generation, and full-chip integration proof without relying on OMTF semantics.

Real downstream plugins may live outside this repository. For active development and integration, the preferred layout is sibling repositories in one workspace, for example `arc-framework/` next to a plugin repository. The plugin owns its FORGE capsule, build products, generated artifacts, and domain-specific scripts; FORGE is installed into the environment and consumed through `forge ...` commands. Use an in-tree mount or submodule only when a downstream project deliberately needs a pinned, vendored plugin snapshot.
