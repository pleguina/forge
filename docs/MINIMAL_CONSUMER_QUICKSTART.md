# Minimal Consumer Quickstart

This is the shortest supported path for a new user who wants to build a new algorithm plugin on top of the current framework.

This file is the canonical onboarding entry point for the current public framework surface.

Use this guide together with:

- `framework/PLUGIN_AUTHOR_GUIDE.md` for topology-generation contract authoring
- `framework/verify/PLUGIN_AUTHOR_GUIDE.md` for verification integration

Do not treat older repo notes in `docs/` or `docs/archive/` as primary onboarding material unless they are explicitly linked from the support-classified framework surface.

## Current supported verification standard

For the current v1.0 framework verification contract:

- datasets are XML-backed
- `design.verification.yml` is the authored verification contract
- `verify.flow.yml`, `tb_*.sv`, and `wave.tcl` are framework-generated
- `stimulus_current.svh` is plugin-generated from the dataset and flow context

If your plugin fits that contract, the framework path below is the supported route.

## Minimal checklist

1. Create your plugin topology assets:
   `plugins/<plugin>/modules.yml`, `plugins/<plugin>/interfaces/*.interface.yaml`, `plugins/<plugin>/designs/design.yml`
2. Create your verification root:
   `plugins/<plugin>/verify/`
3. Author `plugins/<plugin>/verify/design.verification.yml`
4. Author `plugins/<plugin>/verify/tools/bootstrap.py`
5. Author `plugins/<plugin>/verify/tools/gen_stimulus.py`
6. Place XML datasets under `plugins/<plugin>/verify/schemas/data/`
7. Run `arc topgen gen-top` to generate DUT artifacts
8. Run `arc verify generate plugins/<plugin>/verify/design.verification.yml`
9. Run your plugin stimulus generator so each flow gets `stimulus_current.svh`
10. Run `arc verify doctor` and then `arc verify run`

## What you author versus what the framework generates

User-authored:

- `plugins/<plugin>/modules.yml`
- `plugins/<plugin>/interfaces/*.interface.yaml`
- `plugins/<plugin>/designs/design.yml`
- `plugins/<plugin>/verify/design.verification.yml`
- `plugins/<plugin>/verify/tools/bootstrap.py`
- `plugins/<plugin>/verify/tools/gen_stimulus.py`
- `plugins/<plugin>/verify/schemas/data/*.xml`
- optional plugin checker code

Framework-generated:

- `plugins/<plugin>/verify/<flow>/verify.flow.yml`
- `plugins/<plugin>/verify/<flow>/tb_<module>.sv`
- `plugins/<plugin>/verify/<flow>/wave.tcl`

Plugin-generated but not hand-maintained:

- `plugins/<plugin>/verify/<flow>/stimulus_current.svh`

Generated working artifacts:

- `out/...`
- `build/...`
- `build_hls/...`
- `build_targeted/...`
- `plugins/<plugin>/verify/<flow>/xsim_work/...`

## Commands

Topology generation:

```bash
arc core verify-contract --ip-info ip_info.yaml \
    --contract plugins/<plugin>/interfaces/<module>.interface.yaml

arc topgen gen-top plugins/<plugin>/designs/design.yml \
    --mode verilog \
    --consumer-root . \
    --build-dir build \
    --hls-build-root build_hls \
    --ip-root ips \
    --contracts-from plugins/<plugin>/modules.yml \
    --output out/algorithm/algo_top.v
```

Verification generation and health checks:

```bash
arc verify generate plugins/<plugin>/verify/design.verification.yml
arc verify doctor   plugins/<plugin>/verify/design.verification.yml
```

Verification run:

```bash
arc verify run plugins/<plugin>/verify/<flow>/verify.flow.yml --plugin <plugin>
```

## If you want a dataset standard other than XML

The current public framework verification contract is XML-backed. If you want to use a different dataset standard, there are three paths.

### Path 1: Stay within the supported v1.0 contract

Recommended when possible.

Keep the public framework contract XML-backed and add a plugin-local translation layer:

- convert your native source format into the framework-standard XML dataset representation before `fw_verify run`, or
- generate XML once as a build/preparation artifact and reference that XML from `design.verification.yml`

This preserves full compatibility with the current public framework surface.

### Path 2: Use a plugin-specific dataset contract

Supported only as a plugin extension, not as the public minimal path.

If XML is not acceptable, your plugin must own the deviation explicitly. In practice that means your plugin will likely need some combination of:

- plugin-specific `flow_config.py`
- plugin-specific `runtime_context.py`
- plugin-specific backend override
- plugin-specific stimulus generation path
- plugin-specific checker argument conventions

If you go this route, treat it as an explicit framework extension or experimental consumer pattern, not as the default public framework contract.

### Path 3: Propose a new framework-standard dataset contract

Recommended when more than one plugin needs the same non-XML dataset model.

If you want another standard to become first-class framework API, the required bar should be:

1. define the new contract in framework documentation and schema
2. update generic flow loading and preflight ownership accordingly
3. define launcher/runtime semantics explicitly
4. prove it with at least one non-OMTF consumer and tests

Until that work is done, XML remains the only framework-standard dataset contract for v1.0 verification.

## What not to cargo-cult from older examples

For new plugins, do not assume you should hand-author:

- `verify.flow.yml`
- `tb_<module>.sv`
- `wave.tcl`
- flow-local `port_map.yaml`

Do not assume you need to add:

- `backend_xsim.py`
- `flow_config.py`
- `runtime_context.py`

Those are either framework-owned or migration/extension escapes, not the target minimal interface.

## Supported proof consumer

The maintained non-OMTF proof consumer for the current public framework path is:

- `plugins/trigger_demo/` for topology generation, framework-owned csim/xsim flows, and full-chip integration proof

Use it to sanity-check adoption patterns before copying anything from the OMTF consumer.