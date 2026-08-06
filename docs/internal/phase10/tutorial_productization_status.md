# Phase 10 Tutorial Productization — Final Status (T0–T8)

**Status:** T8 deliverable (clean-user + CI closure, final evidence report) for
`docs/plan/FORGE_phase10_progressive_tutorial_productization_plan.md`. Internal-only;
not linked from public MkDocs pages (`mkdocs.yml`'s `exclude_docs: internal/**`).

**Method:** every command and number below was actually run against the `rename/forge`
branch, not copied from memory or a prior report. The headline evidence — a genuinely
clean checkout, isolated venv, quickstart run, full 8-design/9-flow run, docs-asset
staleness check, `mkdocs build --strict`, and a fresh `forge report --plugin` bundle —
was produced in this T8 pass specifically because §17.6 of the plan requires it and no
earlier slice had actually done it (each prior slice validated its own change against the
existing working tree, which already had build caches and, as this pass found, an
undeclared dependency masking a real gap).

---

## 1. Slice-by-slice summary

| Slice | Delivered | Commit |
|---|---|---|
| T0 | Evidence-based audit, capability/ownership/gap map | `ef78bb0` |
| T1 | Phase/slice-reference cleanup (~430 matches, ~85 files, scoped to the plugin + 2 public pages), 4 ADRs, CI lint | `d54d0fa` |
| T2 | Ownership vocabulary (`tutorial/ownership.py`), `tutorial.yml` manifest, progressive `runner.py` | `f901089` |
| T3 | 12-chapter MkDocs rewrite + compatibility redirects | `37af1ac` |
| T4/T5 | Deterministic image panels, topology SVGs, throughput/occupancy figures | `cf93c2e` |
| T6 | `ReportAttachmentProvider` extension point, `forge report --plugin`, dashboard topology link | `409be69` |
| T7 | Local CSS, ownership badges, step-progress nav, hero/logo/favicon, theme features | `7281aab` |
| T8 | Clean-checkout validation, 2 real bugs found and fixed, CI closure, this document | *(this commit)* |

---

## 2. T8: clean-checkout validation (the part never done before)

A fresh `git clone` of the branch (not the working tree any other slice used) into an
isolated Python venv, `pip install -e "forge[parser,docs]"`, then the exact commands a new
external user or CI job would run:

```
$ bash run_vision_pipeline_demo.sh --step quickstart
...
  v  pixel_normalizer_csim PASSED
  v  quickstart_pipeline_xsim PASSED
All 2 flow(s)/check(s) behaved as expected.
real  0m22.083s          # fresh Vitis HLS csim+synth, no cache
```

```
$ bash run_vision_pipeline_demo.sh --jobs=4     # exact CI release-gate invocation
...
  v  pixel_normalizer_csim PASSED
  v  quickstart_pipeline_xsim PASSED
  v  pixel_result_xsim PASSED
  v  tile_stats_xsim PASSED
  v  packetizer_xsim PASSED
  v  invalid_fifo_depth_xsim FAILED as expected (negative fixture evidence)
  v  full_functional_xsim PASSED
  v  platform_wrapper_xsim PASSED
  v  cdc_xsim PASSED
  v  invalid_direct_bus_cdc rejected by gen-top --strict as expected
All 10 flow(s)/check(s) behaved as expected.
real  1m12.714s          # 3 fresh HLS module builds, no cache
```

```
$ python3 plugins/vision_pipeline_demo/tutorial/generate_reference_assets.py --check
PASS: all reference assets are fresh.

$ mkdocs build --strict
(exit 0)

$ forge report plugins/vision_pipeline_demo/forge/designs/design_platform_wrapper.yml \
    --contracts-from plugins/vision_pipeline_demo/forge/modules.yml \
    --hls-build-root build_hls_vision_pipeline_demo \
    --plugin vision_pipeline_demo \
    --output build/vision_pipeline_demo/report
(exit 0 — dashboard.html + topology.svg/explorer + ownership legend + platform-wrapper
 figures, all from artifacts this same clean checkout just built)
```

### 2.1 Two real bugs found — not assumed, found by actually running the sequence in order

**Bug 1 — undeclared `Pillow` dependency.** `generate_reference_assets.py --check` failed
with `ModuleNotFoundError: No module named 'PIL'` on the very first clean-venv attempt.
Pillow is used by real plugin production code
(`plugins/vision_pipeline_demo/datasets/adapters/image_folder.py`, a project-authored
dataset adapter — not just tutorial tooling) and by
`tutorial/visualizations/image_panels.py`. It was never declared anywhere — `forge`'s
`dependencies`/extras in `pyproject.toml` don't list it, and neither does
`ci/framework-base.yml`'s shared `.forge_python_setup` install (which `forge:python-unit-tests`
uses before running vision_pipeline_demo's own pytest suite, including
`test_dataset_adapters.py`/`test_image_panels.py`, both hard `from PIL import Image` at
module level with no `importorskip` guard). It only ever worked because every environment
this was developed in already happened to have Pillow installed from something else. Fixed:
added `Pillow>=10.0` to `forge[docs]` in `forge/pyproject.toml` (docs-asset generation) and
to `.forge_python_setup`'s install line in `ci/framework-base.yml` (the plugin test suite).
First fix attempt used an unquoted `Pillow>=10.0` in the CI shell command — caught before
committing that bash parses unquoted `>=` as an output redirect (`Pillow` unversioned, plus
a stray file named `=10.0`), verified with a throwaway `bash -c` reproduction, fixed by
quoting: `"Pillow>=10.0"`.

**Bug 2 — `_clean()` deletes curated evidence with nothing to regenerate it.**
After the full `--jobs=4` run, `git status` showed
`plugins/vision_pipeline_demo/forge/verify/{packetizer_xsim,full_functional_xsim}/throughput_result.json`
as locally **deleted**. Both are real, checked-in `VERIFICATION RESULT` evidence — produced
once by a deliberately manual `render_throughput_result*.py` (see that script's own
docstring for the probe-limitation reasoning behind its numbers), not by this runner's own
regeneration pipeline. `runner.py`'s `_clean()` does a blanket `shutil.rmtree()` of each
flow's whole output directory before regenerating it, with no awareness that one file in
there isn't regenerable. Chapter 07 of the tutorial explicitly tells the reader to go
inspect `throughput_result.json` as "checked-in evidence" — a real external user following
that exact documented command (`./run_vision_pipeline_demo.sh --step packetizer`, or the
full sequence) would have watched their own copy of that evidence disappear, and
`generate_reference_assets.py` (which reads it) would then fail exactly as Bug 1 did, for
an unrelated reason. Fixed in `runner.py::_clean()`: read-and-restore
`throughput_result.json`'s bytes around the `rmtree` for any flow directory that has one.
Verified fixed by re-running `--step packetizer` in the same clean checkout: `git status`
now shows zero diff on that file, and `generate_reference_assets.py --check` passes.
Regression test added: `test_clean_preserves_throughput_result_json_but_wipes_everything_else`
in `plugins/vision_pipeline_demo/forge/verify/tools/tests/test_tutorial_manifest.py`.

Both bugs are now closed with a code fix plus a regression test, not just documented as
known issues.

### 2.2 CI closure

- `forge:docs-build` (`.gitlab-ci.yml`, fast lane, no Vivado) now runs
  `generate_reference_assets.py --check` before `mkdocs build --strict`, and its pytest
  invocation now includes `forge/tests/test_docs_visual_language.py` (T7's new tests were
  not previously in this job's explicit file list).
- `forge:vision-pipeline-release-gate` (`ci/framework-release.yml`, Vivado-gated) now runs
  `forge report --plugin vision_pipeline_demo` against `design_platform_wrapper.yml` after
  `run_vision_pipeline_demo.sh`, producing a genuinely fresh offline report bundle every
  release run (not merely proven to work once, manually, outside CI) and publishing
  `build/vision_pipeline_demo/report/` as a job artifact.
- `.forge_python_setup` (`ci/framework-base.yml`) now installs Pillow (Bug 1 above).

---

## 3. Final capability-to-chapter matrix

| Chapter | Design | Capabilities introduced |
|---|---|---|
| 01 — Quickstart | `design.yml` | module registry, contracts, validation, RTL generation, HLS execution, golden-model verification |
| 02 — Project structure and contracts | *(quickstart, revisited)* | ownership vocabulary, interface contracts, module registry internals |
| 03 — Mixed RTL/HLS | `design_pixel_result.yml` | a second HLS module, fan-out from one producer |
| 04 — Parallel paths and latency | `design_pixel_result.yml` | alignment delay, exact-cycle merge |
| 05 — Bounded and elastic processing | `design_tile_stats.yml` | bounded latency, elastic tagged join |
| 06 — Clock domains and CDC | `design_cdc.yml` | 3 clock domains, all 5 CDC kinds |
| 07 — Throughput, backpressure, and FIFOs | `design_packetizer.yml` | async FIFO, static/runtime throughput, occupancy, backpressure |
| 08 — Datasets and golden models | *(all of the above, revisited)* | dataset adapters, golden-model provider boundary |
| 09 — Full-functional design | `design_full_functional.yml` | single shared normalizer fan-out, real dataset scale |
| 10 — Platform integration | `design_platform_wrapper.yml` | 3-domain platform, runtime-configurable threshold |
| 11 — Inspect, report, and reproduce | `design_full_functional.yml` / `design_platform_wrapper.yml` | plan hash, provenance, DOT/SVG/explorer, offline report bundle |
| 12 — Diagnostics and negative fixtures | both negative fixtures | validation diagnostics, expected-failure fixtures |

`plugins/vision_pipeline_demo/tutorial.yml`'s manifest declares 27 distinct capability
tags across its 9 real steps (chapters 02/08 revisit existing steps rather than declaring
their own); every tag maps to at least one chapter above — verified by
`test_every_capability_is_in_the_vocabulary` in `test_tutorial_manifest.py`, not by manual
cross-reference.

## 4. Final ownership matrix

Five categories (`plugins/vision_pipeline_demo/tutorial/ownership.py::CATEGORIES`), each
classifying every real tracked file in the plugin (zero unclassified, verified against
`git ls-files`):

| Category | Meaning |
|---|---|
| PROJECT SOURCE | RTL, HLS, `modules.yml`, `design.yml`, interface contracts, datasets, golden models |
| FORGE GENERATED | Generated top-level RTL, verify-flow YAML/testbenches, stimulus, DOT/SVG/explorer, provenance |
| TOOLCHAIN OUTPUT | Vitis HLS/Vivado/XSim synthesis reports, generated HLS RTL, simulation work libraries |
| VERIFICATION RESULT | Golden-comparison records, throughput results, CDC verification results |
| TUTORIAL ASSET | `README.md`, `tutorial.yml`, small deterministic fixtures |

Rendered as badges (T7) in every chapter's file tables, and as a bracket-annotated file
tree on the tutorial landing page and every chapter that introduces new files.

## 5. Comment/reference cleanup (T1)

~430 `Phase N`/`Slice N.M`/`§N` matches removed across ~85 files, scoped to
`plugins/vision_pipeline_demo/**` plus the 2 public tutorial redirect pages (a blind sweep
over `plugins forge ci` hit 1,070 matches across 281 files, ~96% in FORGE core's own
pre-existing, unrelated docstring convention — deliberately not touched, see the T0 audit
and plan §19's own non-goals). Two more instances (a bare `"10.5's 64"` and a hyphenated
`"slice-10.7A"`, missed by T1's original regex) were found and fixed while writing chapters
09/10 in T3; `ci/vision_pipeline_demo_reference_check.sh`'s pattern was strengthened
accordingly. `ci/vision_pipeline_demo_reference_check.sh` (wired into `.gitlab-ci.yml` as
`forge:vision-pipeline-reference-check`) now prevents regressions in this scope.

## 6. Stable ADRs added

`docs/development/adr/0001-dataset-ownership-boundary.md`,
`0002-cdc-primitive-semantics.md`, `0003-vision-packet-format.md`,
`0004-golden-model-provider-boundary.md` — wired into the MkDocs Development nav, capturing
architectural rationale that used to live only in phase-plan section citations.

## 7. Generic FORGE (core) changes

- `forge/analyze/dashboards/attachments.py` (new): `ReportAttachment` dataclass,
  `ReportAttachmentProvider` protocol, registration API.
- `forge/analyze/dashboards/aggregator.py` / `renderer.py`: read/render project attachments
  generically; read/render `topology.svg`/`topology_explorer.html` into the dashboard (a
  gap that predated this pass — `forge report` always wrote those files but never linked
  them from `dashboard.html`).
- `forge/core/cli/groups/report.py`: new `--plugin` flag bootstrapping a plugin and
  collecting its report attachments, using the same bootstrap-fallback pattern
  `forge/verify/__main__.py::_bootstrap()` already established for `forge verify run --plugin`.
- `docs/reference/cli.md`: regenerated by `forge.docsgen` for the new flag (verified via
  `--check`, not hand-edited).
- `mkdocs.yml`: `extra_css`, 4 additional Material features, `theme.logo`/`theme.favicon`.
- `docs/assets/stylesheets/forge.css` (new), `docs/assets/images/{forge-logo,favicon}.png` (new).
- `forge/pyproject.toml`: `Pillow>=10.0` added to the `docs` extra.
- `ci/framework-base.yml`, `.gitlab-ci.yml`, `ci/framework-release.yml`: CI closure (§2.2).
- 22 new tests: `forge/tests/test_report_attachments.py` (11),
  `forge/tests/test_dashboard_topology.py` (5), `forge/tests/test_docs_visual_language.py` (6).

## 8. Plugin-specific (`vision_pipeline_demo`) changes

- `tutorial/ownership.py`, `tutorial.yml`, `tutorial/runner.py` (T2; `_clean()` evidence-
  preservation fix in T8), `tutorial/visualizations/image_panels.py` (T4/T5),
  `tutorial/generate_reference_assets.py` (T4/T5).
- `forge/verify/tools/report_attachment_provider.py` (T6, new).
- `docs/tutorials/vision-pipeline/` — 12 chapters + index + next-steps (T3), badges/step-strip/
  figure grid (T7).
- Real content defects found and fixed while writing/validating (not left as known issues):
  `tile_stats_hls` II=2→II=1 doc claim (T0/T1), a misattributed CDC comment block sitting
  above the wrong flow (T1), swapped async-FIFO depths in a header diagram (T3), a broken
  `forge inspect --dot` argument order in the old full-design page (T3), a wrong row-major
  tile-ID formula in the first `render_tile_overlay` draft (T4/T5), an undeclared Pillow
  dependency and a stale-evidence-deleting `_clean()` bug (T8, §2.1).
- `ci/vision_pipeline_demo_reference_check.sh` (T1).

## 9. Test counts (this session, `rename/forge`)

```
forge/tests:                                    1272 passed, 9 skipped
plugins/vision_pipeline_demo/.../tests:          101 passed
mkdocs build --strict:                           clean (main tree and clean checkout)
generate_reference_assets.py --check:            PASS (main tree and clean checkout)
Clean-checkout quickstart run:                   22.1s, 2/2 flows PASSED
Clean-checkout full run (--jobs=4):               72.7s, 10/10 flows/checks behaved as expected
```

## 10. Remaining optional deferrals

- **hls4ml** — explicitly out of scope for this pass (plan §19 non-goal); tracked
  separately, referenced from the tutorial landing page and
  `docs/explanation/project-scope.md`, never presented as covered.
- Everything else in plan §15's T0–T8 slice list has a commit and passing evidence above.

---

Related: `docs/internal/phase10/tutorial_productization_audit.md` (T0),
`docs/plan/FORGE_phase10_progressive_tutorial_productization_plan.md`.
