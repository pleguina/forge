# How to Set Up the Verification Framework

This guide is a repo-local setup guide for the framework repository. Downstream plugin flows should live in the plugin repository and call the installed `forge` package through explicit paths.

For the canonical new-plugin adoption path, start with:

- [Quickstart](../getting-started/quickstart.md)
- [How to Integrate Verification](integrate-verification.md)

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

## 2. Create the Python environment and install `forge`

From the repo root, create a virtualenv and install the `forge` package:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e forge/
```

This installs the `forge topgen`, `forge verify`, `forge hls`, `forge analyze`, and
`forge core` sub-command groups under the single `forge` entry point.  To verify:

```bash
forge --version
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
  plugins/trigger_demo/forge/verify/tools/tests \
  -q
```

Expected output:

```text
110 passed
```

These proof-consumer suites are framework-owned validation surfaces. They do not
require Vivado or DUT build artifacts.

Downstream plugin test suites are run from the plugin repository. FORGE should not require any particular downstream plugin to be mounted inside this repo.

### What the tests cover

| Test file                      | Covers                                         |
|-------------------------------|------------------------------------------------|
| `test_gen_stimulus.py` (48 tests) | XML dataset parsing, per-module stimulus generation, dry-run behavior, committed `stimulus_current.svh` fixtures |
| `test_trigger_demo_verify.py` (62 tests) | Plugin bootstrap, csim/xsim backend registration, `verify.flow.yml` loading, `design.verification.yml` contract validation, full-chip synthetic-flow loading |

---

## 4. Run the framework CLI on supported proof consumers

The framework CLI is the supported entrypoint for verification generation,
preflight, health checks, and execution.

Framework-owned smoke examples:

```bash
source .venv/bin/activate
pip install -e forge/

forge verify doctor \
  plugins/trigger_demo/forge/verify/design.verification.yml
```

Downstream plugins may add their own environment setup for plugin-local tools and data. That setup belongs in the plugin repository, while FORGE remains the installed framework package.

## 5. Generate DUT artifacts (example consumer flow)

The exact DUT-generation command depends on the consumer design you are using.
For a plugin-owned design, use `forge topgen gen-top` with explicit plugin
paths:

```bash
source .venv/bin/activate

forge topgen gen-top \
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

## 6. Run a downstream XSIM simulation (requires Vivado)

With DUT artifacts and Vivado available, run plugin simulations from the plugin repository using its own guide. A typical sibling-workspace setup looks like:

```text
workspace/
  arc-framework/
  my-plugin/
```

Install FORGE from `arc-framework/`, then execute the plugin-owned verification command from `my-plugin/` with explicit paths to the plugin's `forge/verify/...` files. Submodules are optional packaging choices, not a framework requirement.

---

## 7. Troubleshooting

| Symptom                                                  | Fix                                                     |
|----------------------------------------------------------|---------------------------------------------------------|
| `ModuleNotFoundError: No module named 'forge'`        | `pip install -e forge/`                                   |
| `ModuleNotFoundError: No module named 'yaml'`            | `pip install pyyaml`                                    |
| `ModuleNotFoundError: No module named 'pytest'`          | `pip install pytest`                                    |
| `LookupError: Plugin '<id>' has not been declared`       | Source or import the plugin bootstrap before calling `forge verify` |
| `RuntimeError: Plugin '<id>' has not been bootstrapped`  | Tests: check conftest imports bootstrap; scripts: source plugin setup first |
| `[preflight] FAILED: Port signature not found`           | Run `forge topgen gen-top ...` to regenerate artifacts    |
| `verify.flow.yml missing required fields`                | Check YAML has: `flow.plugin`, `flow.kind`, `flow.backend`, `dut.*`, `simulation.*` |
| `xvlog: command not found`                               | Source your Vivado settings: `source $XILINX_VIVADO/settings64.sh` |

---

## 8. Repo layout reference

```
forge/verification/                   <- framework verification Python package source
plugins/trigger_demo/               <- supported proof consumer
<plugin-repo>/forge/verify/tools/     <- plugin-owned Python files
<plugin-repo>/forge/verify/*_xsim/    <- plugin-owned generated or maintained XSIM flows
out/<design>/                       <- forge topgen gen-top generated DUT artifacts
getting-started/quickstart.md       <- canonical new-plugin quickstart
how-to/integrate-verification.md    <- canonical verification integration guide
<plugin-repo>/forge/verify/           <- plugin-local backend adapters and docs
```
