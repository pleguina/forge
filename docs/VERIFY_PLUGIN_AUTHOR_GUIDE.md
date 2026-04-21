# Plugin Author Guide

This guide defines the current supported plugin interface for framework verification.

It is the canonical verification-integration guide for new plugins.

Use it together with:

- `framework/MINIMAL_CONSUMER_QUICKSTART.md` for the shortest supported adoption path
- `framework/PLUGIN_AUTHOR_GUIDE.md` for topology-generation and contract-driven wiring

Do not treat older repo notes or migration documents as normative unless they are linked from the support-classified framework surface.

## 1. Design goal

The intended minimal plugin authoring surface is:

```text
plugins/<plugin>/verify/
    design.verification.yml
    schemas/data/
    tools/
        bootstrap.py
        gen_stimulus.py
        <optional checker code>
```

Everything else should be framework-owned, framework-generated, or plugin-generated from those authored inputs.

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

These are generated or framework-owned and should not be treated as plugin-authored surfaces for a new plugin:

```text
plugins/<plugin>/verify/<flow>/
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
    xml: plugins/myplugin/verify/schemas/data/events.xml

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

## 10. Notes on current examples

`trigger_demo` is the maintained supported proof consumer for the current public framework path. It demonstrates multiple framework-owned flows, contract-driven generation, and full-chip integration proof without relying on OMTF semantics.

`omtf` remains a valid reference consumer, but parts of its integration still reflect migration or advanced extension needs and should not be copied blindly into a new plugin.
