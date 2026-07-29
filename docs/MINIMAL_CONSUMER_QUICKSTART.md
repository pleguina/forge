# Minimal Consumer Quickstart

This is the shortest supported path for a new user who wants to build a new algorithm plugin on top of the current framework.

This file is the canonical onboarding entry point for the current public framework surface.

Use this guide together with:

- `docs/PLUGIN_AUTHOR_GUIDE.md` for topology-generation contract authoring
- `docs/VERIFY_PLUGIN_AUTHOR_GUIDE.md` for verification integration
- `docs/ANALYSIS_GUIDE.md` for latency measurement, HLS reports, plots, and the HTML dashboard

Do not treat older repo notes in `docs/` or `docs/archive/` as primary onboarding material unless they are explicitly linked from the support-classified framework surface.

## Current supported verification standard

For the current v1.0 framework verification contract:

- datasets are XML-backed
- `design.verification.yml` is the authored verification contract
- `verify.flow.yml`, `tb_*.sv`, and `wave.tcl` are framework-generated
- `stimulus_current.svh` is plugin-generated from the dataset and flow context

If your plugin fits that contract, the framework path below is the supported route.

## Minimal checklist

1. Run `forge topgen init-plugin <plugin>` to scaffold your plugin topology
   assets: `plugins/<plugin>/forge/modules.yml`,
   `plugins/<plugin>/forge/interfaces/*.interface.yaml`,
   `plugins/<plugin>/forge/designs/design.yml`, and an RTL stub at
   `plugins/<plugin>/algo/rtl/<plugin>.v` — `forge topgen gen-top` succeeds
   against the scaffold immediately, with no hand-editing; then adapt the
   stub to your real module. Pass `--dry-run` to preview without writing.
2. Run `forge verify init-plugin <plugin>` to scaffold your verification root:
   `plugins/<plugin>/forge/verify/`
3. Author `plugins/<plugin>/forge/verify/design.verification.yml`
4. Author `plugins/<plugin>/forge/verify/tools/bootstrap.py`
5. Author `plugins/<plugin>/forge/verify/tools/gen_stimulus.py`
6. Place XML datasets under `plugins/<plugin>/forge/verify/schemas/data/`
7. Run `forge topgen gen-top` to generate DUT artifacts
8. Run `forge verify generate plugins/<plugin>/forge/verify/design.verification.yml`
9. Run your plugin stimulus generator so each flow gets `stimulus_current.svh`
10. Run `forge verify doctor` and then `forge verify run`
11. (Optional) Run `forge analyze` to measure latency and generate performance reports — see `docs/ANALYSIS_GUIDE.md`

## What you author versus what the framework generates

User-authored:

- `plugins/<plugin>/forge/modules.yml`
- `plugins/<plugin>/forge/interfaces/*.interface.yaml`
- `plugins/<plugin>/forge/designs/design.yml`
- `plugins/<plugin>/forge/verify/design.verification.yml`
- `plugins/<plugin>/forge/verify/tools/bootstrap.py`
- `plugins/<plugin>/forge/verify/tools/gen_stimulus.py`
- `plugins/<plugin>/forge/verify/schemas/data/*.xml`
- optional plugin checker code
- `plugins/<plugin>/forge/verify/plot_config.yml` (if using `forge analyze plot-results`)

Framework-generated:

- `plugins/<plugin>/forge/verify/<flow>/verify.flow.yml`
- `plugins/<plugin>/forge/verify/<flow>/tb_<module>.sv`
- `plugins/<plugin>/forge/verify/<flow>/wave.tcl`

Plugin-generated but not hand-maintained:

- `plugins/<plugin>/forge/verify/<flow>/stimulus_current.svh`

Generated working artifacts:

- `out/...`
- `build/...`
- `build_hls/...`
- `build_targeted/...`
- `plugins/<plugin>/forge/verify/<flow>/xsim_work/...`

## Commands

Topology generation:

```bash
forge core verify-contract --ip-info ip_info.yaml \
    --contract plugins/<plugin>/forge/interfaces/<module>.interface.yaml

forge topgen gen-top plugins/<plugin>/forge/designs/design.yml \
    --mode verilog \
    --consumer-root . \
    --build-dir build \
    --hls-build-root build_hls \
    --ip-root ips \
    --contracts-from plugins/<plugin>/forge/modules.yml \
    --output out/algorithm/algo_top.v
```

Verification generation and health checks:

```bash
forge verify generate plugins/<plugin>/forge/verify/design.verification.yml
forge verify doctor   plugins/<plugin>/forge/verify/design.verification.yml
```

Verification run:

```bash
forge verify run plugins/<plugin>/forge/verify/<flow>/verify.flow.yml --plugin <plugin>
```

## The `--dry-run` invariant

Every FORGE command that accepts `--dry-run` (`topgen gen-top`, `topgen clean`,
`verify generate`, `verify prepare`, `topgen init-plugin`, `verify init-plugin`)
guarantees that **no project file is created, modified, or deleted** — the
project directory tree must be byte-for-byte identical before and after the
call, including any file that a real run would auto-generate as a cached
input (e.g. `ip_info.yaml` when `--contracts-from`/`--ip-info` aren't given).
`topgen gen-top --dry-run` computes that input in memory instead of writing
it to disk and reading it back. `forge/tests/test_topgen_cli_commands.py::test_gen_top_dry_run_lists_without_writing`
snapshots the full directory tree before/after `--dry-run` and asserts no
diff — treat any change to `cmd_gen_top` that breaks that test as a
regression against this invariant, not an acceptable side effect.

## Analysis (optional but recommended)

After verification passes, `forge analyze` produces latency reports, plots, and an HTML dashboard.
See `docs/ANALYSIS_GUIDE.md` for the full guide.  Quick reference:

```bash
# HLS synthesis metrics table
forge analyze hls-report --hls-build-root build_hls_<plugin> --output out/reports

# Static latency check (requires latency_hint or latency_cycles in modules.yml)
forge analyze latency-check plugins/<plugin>/forge/designs/design.yml \
    --contracts-from plugins/<plugin>/forge/modules.yml \
    --hls-build-root build_hls_<plugin> \
    --output out/reports/latency_check.md

# HLS-predicted vs simulation-observed latency
forge analyze runtime-latency \
    --probe-csv out/reports/pipeline_probe.csv \
    --probe-pairs "<module>:<in_valid_signal>:<out_valid_signal>" \
    --hls-build-root build_hls_<plugin> \
    --output out/reports/runtime_latency.md

# Result comparison plots (requires plot_config.yml + observed/reference CSVs)
forge analyze plot-results \
    --config plugins/<plugin>/forge/verify/plot_config.yml \
    --observed out/reports/observed.csv \
    --reference out/reports/reference.csv \
    --output out/reports/plots

# Self-contained HTML dashboard aggregating all of the above
forge analyze dashboard --input out/reports --output out/dashboard
```

## If you want a dataset standard other than XML

The current public framework verification contract is XML-backed. If you want to use a different dataset standard, there are three paths.

### Path 1: Stay within the supported v1.0 contract

Recommended when possible.

Keep the public framework contract XML-backed and add a plugin-local translation layer:

- convert your native source format into the framework-standard XML dataset representation before `forge verify run`, or
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