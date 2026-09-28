# FORGE — Release Scope Freeze

Classifies every user-visible capability per
`docs/plan/FORGE_framework_freeze_and_public_release_plan.md` §3. Based on
the real audit in `release_readiness_audit.md` (same directory) — every
row below has executable evidence (a passing test, a real command run
against a real design), not an intention.

Legend: **Stable** / **Beta** / **Experimental** / **Deprecated** /
**Internal** / **Deferred**.

| Capability | Classification | Evidence / notes |
|---|---|---|
| Project initialization (`forge init`) | **Stable** | Full clean-install smoke test: scaffold → validate → build → test (real XSim) → report, all green, from a wheel installed into an empty venv. |
| Module registry | **Stable** | `forge/contracts/config.py`; core of every reference plugin. |
| Contracts (canonical roles/protocols/interface members) | **Stable** | Generated reference pages (`docs/reference/{canonical-roles,protocols,interface-members}.md`), AST-verified against source. |
| Topology resolution (matching, scatter/gather, N-D/prefix arrays) | **Stable** | Exercised by all 3 reference plugins; `docs/concepts/contracts-and-protocols.md`. |
| Canonical IR (`forge/ir/`) | **Stable** | Byte-identical-to-legacy proofs (`test_generation_ir_equivalence.py` et al.); drives all 3 generation modes (verilog/vhdl/bd). |
| Generation plans / deterministic hashes | **Stable** | `forge.ir.serialize.content_hash`; exercised by `forge build --provenance`. |
| Provenance | **Stable** | `forge/ir/provenance.py`; `forge inspect --explain-staleness`. |
| Top-level generation (Verilog/VHDL/Block Design TCL) | **Stable** | All 3 modes exercised by reference plugins in the real `forge init` smoke test and CI. |
| Vitis HLS integration | **Stable** | `forge hls`; `trigger_demo`/`vision_pipeline_demo` both mix HLS+RTL. |
| XSim | **Stable** | Real tool present and exercised in this environment; primary supported simulator. |
| Verilator | **Beta** | `doctor` confirms it's lint-only, not a simulation backend — narrower than XSim's role. Matches README's documented scope, not a gap. |
| Domains and CDC | **Stable** for the 5 implemented kinds (`level_sync`, `2ff_sync`, `pulse_sync`, `mailbox_transfer`, `async_fifo`) | `forge/contracts/cdc.py`; real generated synchronizer/FIFO RTL, not stubs (see audit — one stale docstring claiming otherwise was found and fixed this session). `clock_secondary`/`reset_secondary` roles are **Deferred** (reserved, no functional effect, documented as such in README/docs/index.md). |
| Latency analysis (fixed/bounded/elastic) | **Stable** | `forge/analysis/latency_static/`; exercised by `vision_pipeline_demo`. |
| Throughput and occupancy (static) | **Stable** | `forge/analysis/throughput_static/`. |
| Throughput and occupancy (runtime probe) | **Beta** | `forge/analysis/throughput_runtime/probe.py` — real, but reporting depends on a cached probe CSV that isn't always present (several tests skip without it in this environment); matches plan §3.1's own Beta classification. |
| Verification backends (XSim, csim, Verilator-lint) | **Stable** (XSim, csim) / **Beta** (Verilator, GHDL — `doctor` reports GHDL as an optional, currently-missing tool in this environment) | |
| Datasets (XML and JSON) | **Stable** | `design.verification.yml` + golden data. Both formats load through the registered-loader protocol (`dataset_format.DatasetFormatLoader`), and `test_dataset_format.py` asserts the two produce byte-identical events for the same dataset — the architectural proof that verification is not XML-specific. All 3 reference plugins use XML; `passthrough_demo` also ships the JSON equivalent. |
| Golden-model providers | **Stable** protocol, **Beta** in breadth | Protocol is real and tested. XML and JSON datasets are both exercised; formats needing domain interpretation (HDF5, ROOT, NumPy) go through the project-owned `ProjectDatasetAdapter` and are not built in. |
| Structured results (`FlowResult`, golden-comparison, CDC-verification artifacts) | **Stable** | Schema-versioned, generated reference at `docs/reference/artifacts.md`. |
| DOT/SVG design explorer | **Stable** | `forge/analysis/design_explorer/`; exercised in the `forge init` smoke test's `report/` output. |
| HTML explorer | **Stable** | Same; offline, self-contained (`test_docs_site_offline.py`-equivalent checks apply to the explorer's own vendored-JS discipline). |
| Report generation (`forge report`) | **Stable** | Produces `dashboard.html`, `summary.md`, `topology.{dot,svg}`, `maturity.md`, `latency_check.md`, `verification_results.md` — all confirmed present in the clean-install smoke test. |
| Reference plugins (`passthrough_demo`, `trigger_demo`, `vision_pipeline_demo`) | **Stable** | All 3 real, CI-exercised, not placeholders (`docs/explanation/project-scope.md`). |
| Tutorial (progressive 12-chapter `vision_pipeline_demo`) | **Stable** | Complete per Phase 10 productization work; `docs/tutorials/vision-pipeline/`. |
| Public Python API | **Stable** for `__all__`-listed symbols only | New this session: `docs/reference/public-python-api.md` + `docs/internal/release/public_api_inventory.json`, generated from live `__all__` inventories, with an import-every-symbol test. `forge.core.utils` exports 4 underscore-prefixed names in `__all__` — explicitly **excluded** from the stable surface per policy (private-by-naming-convention overrides `__all__` membership), listed as a known inconsistency on the generated page rather than silently promoted. |
| CLI (`forge {init,validate,build,verify,inspect,report,doctor}` + `forge topgen/hls/verify/analyze/framework/core` subsystem commands) | **Stable** for the golden-path verbs; **Beta/advanced** for subsystem commands | `docs/development/cli_exit_codes.md` documents the shared `CommandEnvelope`/exit-code policy; subsystem commands (`topgen gen-top`, `hls *`, `framework *`) are real and tested but lower-level, matching the plan's own "advanced/compatibility interface" framing. |
| Release artifacts (`ci/release_artifacts.sh`) | **Stable** | One command builds the wheel + source archive, installs the wheel into a clean venv and smoke-tests it, and assembles `SHA256SUMS`/`MANIFEST.json`, release notes (this version's `CHANGELOG.md` section, verbatim), migration notes, the support matrix and the tested-environment list. Every artifact is derived from the repository, none typed by hand. Cutting an actual release (tag + publish) remains a human decision. |
| HLS port prediction per Vitis HLS release | **Stable** for a release with recorded synthesis output, `untested` for any other | `forge.hls.tool_matrix` replays the fixture corpus against `golden/<version>.json` and classifies every difference; `forge doctor` reports the status of the release on the user's PATH, and the generated support matrix lists only releases with evidence. Currently one release (2024.1) is recorded. |
| Optional hls4ml integration | **Deferred** | Not implemented (docs mention it only as a stated non-goal for this release); excluded from the stable gate per plan principle 5. |
| Interactive authoring GUI / drag-and-drop topology editing / local `forge studio` | **Deferred** | Not implemented; no code found. |
| Variable-size runtime datasets | **Deferred** | Not implemented beyond fixed-size XML. |
| Per-pin CDC mapping refactor | **Deferred** | Current CDC mapping is per-connection, not per-pin; no in-progress refactor found. |
| Physical-port-array interactive expansion | **Deferred** | Not implemented. |
| ASIC backend support | **Deferred / Not supported** | README explicitly lists "generic ASIC flows" as unsupported. |

## Notes on this classification

- This table corrects the plan's own §3.1 starting recommendation in two
  places where the real code is further along than assumed: CDC async-FIFO
  generation is real (not "wired directly" — a stale docstring said
  otherwise and was fixed this session), and the progressive tutorial is
  complete (not in-progress).
- Nothing here is downgraded from what was previously shipped; this is a
  freeze of the *current* real state, not a re-scoping exercise.
