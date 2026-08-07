# FORGE — Release Test Matrix

What ran, in this environment, this session — real commands, real output,
not a checklist filled in from memory. Companion to
`release_readiness_audit.md`.

## Environment

- Python 3.9.25, pytest 8.4.2
- Real tools on `PATH`: `xvlog`, `xelab`, `xsim` (Vivado XSim), `verilator`
- Not available in this environment: `ghdl`, `pyverilog` (parser extra),
  `matplotlib`, `numpy`, `networkx` — all correctly reported as optional
  by `forge doctor`, none block any required check.
- No Vitis HLS synthesis toolchain available — HLS-synthesis-dependent
  tests skip with an explicit reason rather than failing or being silently
  omitted.

## Python/core

| Check | Result |
|---|---|
| Full unit/integration suite (`python -m pytest forge/tests`) | **1269–1274 passed** (varies by which optional local build caches — e.g. `gen-top/design_passthrough_demo` — are warm at run time), **12 skipped**, **0 failed** |
| Skipped-test reasons (all stated, none silent) | 3× no cached `vision_pipeline_demo` HLS build (`run_vision_pipeline_demo.sh` not run this session — real Vitis HLS synthesis, deliberately not attempted in this sandbox); 8× `ip_info.yaml`/`TOPGEN_CONSUMER_ROOT` not set (tests designed to run against a real consumer checkout, not this framework repo itself); 1× no generated `vision_pipeline_demo` algo_top.v (same HLS-cache dependency) |
| Packaging tests (sdist/wheel build + install) | **PASS** — see "Packaging" below |
| Docs generation/quality tests (`test_docs_site_*`, `test_docs_staleness`, `test_docsgen_*`) | **PASS**, 12/12 in the docs-specific subset run standalone |
| Deterministic-output tests (`forge.docsgen --check`, `test_deterministic_output` in each `docsgen` generator's test file) | **PASS** |
| Security/escaping tests (DOT/HTML/JSON escaping in `forge.analysis.design_explorer`) | Pre-existing, part of the full suite above; not re-audited line-by-line this session (see audit's "not attempted" section) |

## Reference plugins

| Plugin | Result |
|---|---|
| `passthrough_demo` | Real `forge topgen gen-top` + `forge test run` exercised directly this session (regenerating state accidentally deleted during hygiene cleanup, then verified fixed) — **PASS** |
| `trigger_demo` | `forge topgen validate`, `forge verify prepare --dry-run`, `forge verify doctor` (`status=warn`, 0 errors — expected, not a failure), full plugin test suite (110 passed) — all via `ci/fresh_user_check.sh`'s own independent clean-venv run — **PASS** |
| `vision_pipeline_demo` | Not re-run end-to-end this session (requires real Vitis HLS synthesis, not available here) — tests that depend on it skip honestly rather than fail; the plugin's own prior CI history (Phase 10 productization work) is the last real evidence of a full HLS run |

## Backends

| Backend | Status |
|---|---|
| XSim | **Supported, exercised for real** — `forge doctor` confirms on `PATH`; real simulation runs happened in both the manual clean-install smoke test and `ci/fresh_user_check.sh` |
| Verilator | **Available as lint tool only** (confirmed via `doctor`), not exercised as a simulation backend — matches documented scope, not a gap |
| Vitis HLS C-simulation/synthesis | **Not available in this environment** — no Vitis HLS toolchain on `PATH`; every dependent test skips with an explicit reason |
| GHDL/VHDL simulation | **Not available in this environment** — `doctor` reports it as an optional, missing tool |

## Tutorial

| Item | Result |
|---|---|
| Quickstart (`ci/quickstart_commands.sh`, embedded verbatim in `docs/getting-started/quickstart.md`) | **PASS** via `ci/fresh_user_check.sh` |
| Progressive vision-pipeline tutorial (12 chapters) | Not re-executed step-by-step this session (would require the HLS toolchain); prior Phase 10 session work is the last real completion evidence (`T3`/`T7`/`T8` commits) |
| Report generation | **PASS** — real `dashboard.html`, `summary.md`, `topology.{dot,svg}`, `topology_explorer.html`, `maturity.md`, `latency_check.md`, `verification_results.md` all produced by the clean-install `forge init` smoke test |
| Tutorial-asset staleness | Covered by the existing `forge/tests/test_docs_staleness.py` suite — **PASS** |

## Packaging (new evidence this session)

| Step | Result |
|---|---|
| `python -m build` (sdist + wheel) from a clean `build/`/`*.egg-info` state | **PASS** — both archives built |
| Wheel contents inspected | No test/build junk found; all expected package data present (`py.typed`, `topgen/ip/*.yaml`, `hls/templates/*.tcl.j2`, `analyze/design_explorer/vendor/*`) |
| Install into a fresh venv (no repo on `PYTHONPATH`) | **Initially FAILED** — `ModuleNotFoundError: No module named 'forge.core.cli.groups'` (see audit P0-1). **Fixed and re-verified PASS** after correcting `pyproject.toml`'s packages list. |
| `forge --version` / `forge --help` from the installed wheel | **PASS** (post-fix) |
| `forge doctor` from the installed wheel | **PASS** — all required checks pass, optional-tool warnings correctly non-blocking |
| `forge init <plugin_id>` from the installed wheel (full chain: scaffold → validate → build → real XSim test → report) | **PASS** — real generated RTL, real simulation run, real report bundle, zero errors |
| `forge init <plugin_id> --dry-run` | **PASS** — previews the scaffold, writes nothing |

## CI scripts run directly this session

| Script | Result |
|---|---|
| `ci/stale_reference_check.sh` | **FAIL** — pre-existing on branch HEAD, not caused by this session (reproduced against a `git stash`d pre-session tree too); see audit P1-1 |
| `ci/vision_pipeline_demo_reference_check.sh` | **PASS** |
| `ci/agnosticism_check.sh` | **PASS** |
| `ci/import_direction_check.sh` | **PASS** |
| `ci/fresh_user_check.sh` (independent clean-venv onboarding check) | **PASS** — "All fresh-user onboarding checks passed." |

## Coverage

Not re-measured with `--cov` this session (full-suite runs used `--no-cov`
for speed while iterating); the project's existing `pyproject.toml` gate
(`--cov-fail-under=49`) was last confirmed passing in the prior session's
own history and is unaffected by this session's changes (no coverage-
relevant logic was modified — only docstrings, comments, docs, one new
generator module with its own new tests, and a packaging manifest fix).

## Known limitations recorded honestly (not silently omitted)

- No real Vitis HLS synthesis was run this session — every HLS-cache-
  dependent test skips with a stated reason rather than being counted as
  passing or silently excluded.
- External fresh-user validation ran in this same sandbox (fresh venv,
  independent script), not on a genuinely separate machine/account.
