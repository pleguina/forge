# Framework Setup Guide

This guide is primarily a repo-local setup guide for the framework repository.

It starts with framework-owned proof paths and only later shows the current OMTF downstream workflow.

For the canonical new-plugin adoption path, start with:

- `framework/MINIMAL_CONSUMER_QUICKSTART.md`
- `framework/verify/PLUGIN_AUTHOR_GUIDE.md`

Get from zero to a working verification environment in under 30 minutes.

---

## 1. Prerequisites

| Tool            | Minimum version | Required for               |
|-----------------|-----------------|----------------------------|
| Python          | 3.11            | Everything                 |
| PyYAML          | 6.0             | Flow loading (auto-installed below) |
| pytest          | 7.0             | Running the test suite     |
| Vivado (`xsim`) | 2024.1          | E2E simulation only        |

Vivado is **not required** to run the Python test suite.  Install it only
when you need to execute a full simulation.

---

## 2. Create the Python environment and install `arc`

From the repo root, create a virtualenv and install the `arc` package:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e arc/
```

This installs `topgen`, `fw_verify`, and all orchestration tooling under the
single `arc` entry point.  To verify:

```bash
arc --version
```

Then install `pytest` (not included in the default dependencies):

```bash
pip install pytest
```

---

## 3. Run the framework proof suites

```bash
cd /path/to/repo
source .venv/bin/activate
python3 -m pytest \
  plugins/trigger_demo/verify/tools/tests \
  -q
```

Expected output:

```text
110 passed
```

These proof-consumer suites are framework-owned validation surfaces. They do not
require Vivado or DUT build artifacts.

If you specifically want the current downstream OMTF Python suite, run:

```bash
python3 -m pytest plugins/omtf/verify/tools/tests/ -q
```

The OMTF test conftest at `plugins/omtf/verify/tools/tests/conftest.py` sets up
`sys.path` and bootstraps the OMTF plugin automatically. You do not need to
set `PYTHONPATH` manually to run that downstream test suite with pytest.

### What the tests cover

| Test file                      | Covers                                         |
|-------------------------------|------------------------------------------------|
| `test_flow_config.py`         | YAML parsing, field validation, shell-env output |
| `test_verify_preflight.py`    | Artifact existence and fingerprint checking    |
| `test_backend_contract.py`    | BackendAdapter interface enforcement           |
| `test_bootstrap_lifecycle.py` | Plugin registration and bootstrap guard        |
| `test_framework_promotion.py` | fw_verify integration (parse_generic + registry) |

---

## 4. Run the framework CLI on supported proof consumers

The framework CLI is the supported entrypoint for verification generation,
preflight, health checks, and execution.

Framework-owned smoke examples:

```bash
source .venv/bin/activate
pip install -e arc/

arc verify doctor \
  plugins/trigger_demo/verify/design.verification.yml \
  --flow trigger_pipeline_xsim \
  --dry-run
```

The current downstream OMTF flow still uses the OMTF env script for plugin
tool discovery and path variables, but it now consumes `fw_verify` through the
installed package surface.

## 5. Generate DUT artifacts (example consumer flow)

The exact DUT-generation command depends on the consumer design you are using.
For a plugin-owned design, use `arc topgen gen-top` with explicit plugin
paths:

```bash
source .venv/bin/activate

arc topgen gen-top \
  plugins/<plugin>/designs/design.yml \
  --mode verilog \
  --consumer-root . \
  --ip-root ips/ \
  --hls-build-root build_hls/ \
  --output out/<design>/algo_top.v
```

This writes `out/<design>/` with:
`algo_top.v`, `build_manifest.json`, `port_map.yaml`, `tb_bindings.svh`,
`port_signature.json`, `probe_map.yaml`.

---

## 6. Run the current OMTF downstream XSIM simulation (requires Vivado)

With DUT artifacts and Vivado available, the current OMTF downstream path is:

```bash
source .venv/bin/activate
pip install -e arc/
source plugins/omtf/verify/tools/omtf_verify_env.sh

arc verify run \
  plugins/omtf/verify/full_chip_algo_top_xsim/verify.flow.yml \
  --plugin omtf \
  --xml-input plugins/omtf/verify/schemas/data/TestEvents.xml \
  --all-events
```

Or with explicit overrides:

```bash
arc verify run \
  plugins/omtf/verify/full_chip_algo_top_xsim/verify.flow.yml \
  --plugin omtf \
  --xml-input /path/to/events.xml \
  --event-id 55

arc verify run \
  plugins/omtf/verify/full_chip_algo_top_xsim/verify.flow.yml \
  --plugin omtf \
  --xml-input plugins/omtf/verify/schemas/data/TestEvents.xml \
  --event-id 1 \
  --probe-log
```

The runtime path is: preflight → plugin stimulus generation → xvlog/xelab/xsim
execution → optional checker invocation.

OMTF flow-local wrappers may still exist as convenience helpers, but they are
not the canonical new-user path.

---

## 7. Troubleshooting

| Symptom                                                  | Fix                                                     |
|----------------------------------------------------------|---------------------------------------------------------|
| `ModuleNotFoundError: No module named 'arc'`        | `pip install -e arc/`                                   |
| `ModuleNotFoundError: No module named 'yaml'`            | `pip install pyyaml`                                    |
| `ModuleNotFoundError: No module named 'pytest'`          | `pip install pytest`                                    |
| `LookupError: Plugin 'omtf' has not been declared`       | Source the env script before calling `arc verify`       |
| `RuntimeError: Plugin 'omtf' has not been bootstrapped`  | Tests: check conftest imports bootstrap; scripts: source env first |
| `[preflight] FAILED: Port signature not found`           | Run `arc topgen gen-top ...` to regenerate artifacts    |
| `verify.flow.yml missing required fields`                | Check YAML has: `flow.plugin`, `flow.kind`, `flow.backend`, `dut.*`, `simulation.*` |
| `xvlog: command not found`                               | Source your Vivado settings: `source $XILINX_VIVADO/settings64.sh` |

---

## 8. Repo layout reference

```
arc/verify/                          ← framework verification Python package source
plugins/omtf/verify/tools/           ← OMTF plugin Python files
plugins/omtf/verify/tools/tests/     ← OMTF pytest test suite
plugins/omtf/verify/full_chip_algo_top_xsim/    ← full-chip XSIM flow
plugins/omtf/verify/*_xsim/                     ← current generated/maintained XSIM flows
out/algorithm/                       ← arc topgen gen-top generated DUT artifacts
docs/MINIMAL_CONSUMER_QUICKSTART.md  ← canonical new-plugin quickstart
docs/VERIFY_PLUGIN_AUTHOR_GUIDE.md   ← canonical verification integration guide
plugins/omtf/verify/ADAPTER_CONTRACT.md  ← backend adapter API reference
```
