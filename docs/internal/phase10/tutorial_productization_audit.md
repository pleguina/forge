# Phase 10 Tutorial Productization — T0 Audit

**Status:** T0 deliverable (evidence-based audit) for
`docs/plan/FORGE_phase10_progressive_tutorial_productization_plan.md`.
Internal-only; not linked from public MkDocs pages (`mkdocs.yml`'s
`exclude_docs: internal/**`).

**Method:** every fact below was verified against the `rename/forge`
branch as of commit `7606e25` (2026-08-05) by reading the named file or
running the named command — nothing here is inferred from the plan
document or from `preflight.md`'s prose alone. Where a claim in existing
tutorial prose was checked against code and found wrong, that is called
out explicitly (see §4).

No broad edits have been made yet. This document is the T0 gate; T1
(comment hygiene) starts only after this is reviewed.

---

## 1. Inventory

### 1.1 Designs (`plugins/vision_pipeline_demo/forge/designs/`)

| Design | Lines | Positive/negative | What it exercises |
|---|---|---|---|
| `design.yml` | 49 | positive | `pixel_normalizer` (HLS) → `threshold_rtl` (RTL), 1 clock domain |
| `design_pixel_result.yml` | 138 | positive | `window_builder_rtl` → `sobel_hls` ‖ delayed `threshold_rtl` → `edge_mask_merge_rtl`; fan-out, alignment delay, exact-cycle merge |
| `design_tile_stats.yml` | 123 | positive | `tile_stats_hls` (II=1) ‖ `tile_boundary_rtl` → `tile_summary_join_rtl`; bounded→elastic tagged join |
| `design_cdc.yml` | 149 | positive | 3 clock domains, all 5 CDC kinds (`level_sync`/`pulse_sync`/`mailbox_transfer`/`async_fifo`/`reset_sync`) |
| `design_packetizer.yml` | 325 | positive | both record kinds → `async_fifo` (depth 64) → `packetizer_rtl`; throughput/occupancy/backpressure |
| `design_full_functional.yml` | 328 | positive | single shared `norm` fan-out, 16×16/2×2-tile frame, full chain unified |
| `design_platform_wrapper.yml` | 340 | positive | real 3-domain platform assembly, status interface, runtime-configurable threshold via `mailbox_transfer` |
| `invalid_direct_bus_cdc.yml` | 50 | **negative** | undeclared clock-domain crossing; must be rejected by `gen-top --strict` (ATG023/ATG024), no verify flow exists for it |
| `invalid_fifo_depth_packetizer.yml` | 271 | **negative** | `design_packetizer.yml` clone with FIFO depth 4 instead of 64; passes `validate`, fails at simulated-burst time |

### 1.2 Verification flows (`plugins/vision_pipeline_demo/forge/verify/`)

9 flow directories, each with a generated `verify.flow.yml`/`tb_algo_top.sv`/`port_map.yaml`:
`pixel_normalizer_csim` (hls_csim, positive, HLS-only), `quickstart_pipeline_xsim`,
`pixel_result_xsim`, `tile_stats_xsim`, `packetizer_xsim`, `full_functional_xsim`,
`platform_wrapper_xsim` (all `full_chip_rtl`/xsim, positive), `invalid_fifo_depth_xsim`
(`full_chip_rtl`/xsim, **negative — expected FAIL**), `cdc_xsim` (`full_chip_rtl`/xsim,
positive, 3 clock domains). `invalid_direct_bus_cdc.yml` has no flow directory — rejected
before a flow can be generated. Shared TB support lives in `include/`, `src/`, `tests/`,
`tools/`, `schemas/data/`; the master flow contract is `design.verification.yml` (313 lines).

### 1.3 HLS/RTL modules (`plugins/vision_pipeline_demo/algo/`)

3 HLS (`pixel_normalizer`, `sobel_hls`, `tile_stats_hls`) + 16 RTL modules. Full list and
one-line purpose per module in the module registry (§1.4) — registry `latency`/`kind`
fields are the authoritative source, not this audit.

### 1.4 Module registry

`plugins/vision_pipeline_demo/forge/modules.yml` (313 lines, `registry_version: '1'`), 19
registered modules, each with `kind`, `top`, `latency`, `interface_contract`, `src`.
`defaults:` block: `part: xcvu9p-flga2104-2L-e`, `clock_period: 4.0`.

### 1.5 Datasets and adapters (`plugins/vision_pipeline_demo/datasets/`)

Canonical model (`model.py`, `DatasetEvent`), 3 real adapters (`adapters/synthetic.py`,
`adapters/image_folder.py`, `adapters/numpy_array.py`), manifest/staleness
(`manifest.py`), XML serialization (`serialize_xml.py`), CLI (`cli.py` —
example-local `generate-synthetic`/`import-images`, "pending a generic FORGE dataset
command", per its own docstring), 2 configs, `README.md`.

### 1.6 Golden-model providers

Single file, `forge/verify/tools/golden_model_provider.py`: 5 provider classes
(`QuickstartNormalizerThresholdProvider`, `PixelResultProvider`, `TileStatsProvider`,
`PacketizerProvider`, `PlatformWrapperProvider`), each registered via
`forge.verify.golden_model.register_golden_model_provider`.

### 1.7 Structured artifact schemas (FORGE core, `forge/`)

| Schema/type | Defined at |
|---|---|
| `forge.throughput_result` v1 | `forge/verify/throughput_result.py:36` |
| `forge.cdc_verification_result` v1 | `forge/verify/cdc_verification_result.py:28` |
| `forge.golden_comparison_result` v1 | `forge/verify/golden_comparison_result.py:30` |
| `FlowResult` | `forge/verify/results.py:214` |
| `ProvenanceManifest` | `forge/ir/provenance.py:52` |
| `DesignGraph` | `forge/analyze/design_explorer/graph_model.py:245` |

### 1.8 DOT/SVG/HTML generation

All in FORGE core, none plugin-local: `forge/analyze/design_explorer/{dot_renderer,
html_renderer,graph_model,escaping,verification_join}.py`, vendored
`vendor/cytoscape.min.js` for the interactive HTML. Invoked via `forge inspect
--dot/--explorer/--provenance` (`forge/core/cli/groups/inspect.py`) and rolled into
`forge report` (`forge/core/cli/groups/report.py`, see §1.11).

### 1.9 Current tutorial/internal docs

| File | Lines |
|---|---|
| `docs/tutorials/vision-pipeline-quickstart.md` | 159 |
| `docs/tutorials/vision-pipeline-full-design.md` | 154 |
| `docs/internal/phase10/preflight.md` | 1276+ |
| `docs/internal/phase10/vision_pipeline_reference_project.md` | 2308 |

`docs/tutorials/` also has 3 unrelated-plugin tutorials (`golden-path.md`,
`rtl-example.md`, `mixed-hls-rtl-example.md`) that must keep working.

### 1.10 `run_vision_pipeline_demo.sh` (297 lines, post-10.7D)

Flags: `--skip-hls`, `--no-clean`, `--jobs=N`, `-h`. 8 fixed stages (clean → validate →
HLS build → gen-top → verify generate → stimulus → doctor → run), all-or-nothing —
**no per-step/per-stage entry point exists today** (plan §8.2's `--step`/`--list`/`--all`
UX is net-new).

### 1.11 Existing report/dashboard infrastructure (directly relevant to plan §10)

`forge report` (`forge/core/cli/groups/report.py`, 497 lines) **already exists** and
already does most of what plan §10 (offline HTML report bundle) asks for: it orchestrates
maturity/compatibility, latency-check, HLS summary, runtime latency, provenance, JUnit
verification results, topology DOT/SVG + interactive explorer HTML, and a final
`dashboard.html`/`summary.md` via `forge.analyze.dashboards.{aggregator,renderer}`
(self-contained HTML, base64-embedded images, no external CSS/JS). It has never been run
end-to-end against any `vision_pipeline_demo` design. There is **no** existing
`ReportAttachmentProvider`-shaped extension point (`grep -rn "Protocol\b"` over
`forge/analyze/dashboards` and `report.py` returns nothing) — a project cannot currently
attach domain-specific figures (image panels, error maps) to this report without FORGE
core knowing about them. This is the one genuine new-core-surface gap plan §10.3 anticipates.

### 1.12 CI jobs touching the plugin

`.gitlab-ci.yml`: `forge:schema-validate` (validates registry + 7 real designs, not the
negative fixtures), `forge:python-unit-tests` (runs
`plugins/vision_pipeline_demo/forge/verify/tools/tests`, 35 tests post-10.7C),
`forge:verify-smoke` (dry-run generate + doctor, no simulator). `ci/framework-release.yml`:
`forge:vision-pipeline-release-gate` (full `run_vision_pipeline_demo.sh --jobs=4`, Vivado
runner). `ci/stale_reference_check.sh` scans `run_vision_pipeline_demo.sh` by name.

### 1.13 Internal phase/slice reference count

`rg` over `plugins forge ci` (excluding `docs/internal`) for
`Phase\s+[0-9]+|Slice\s+[0-9]+|spec\s*§|§[0-9]+|preflight|FORGE_release_plan`:
**1,070 matches across 281 files** — this is a codebase-wide FORGE-core convention
(release-plan phase/§ citations in docstrings), not specific to this plugin, and mostly
**out of scope** for this productization pass per plan §5.4 (only production/public paths
matter, and FORGE core's own internal engineering-history comments are a separate,
much larger cleanup not requested by this plan). Within scope — `vision_pipeline_demo`
production files and public docs:

| File | Matches | In scope for T1? |
|---|---|---|
| `plugins/vision_pipeline_demo/forge/verify/tools/golden_model_provider.py` | 24 | yes |
| `plugins/vision_pipeline_demo/forge/modules.yml` | 23 | yes |
| `plugins/vision_pipeline_demo/forge/verify/design.verification.yml` | 18 | yes |
| `plugins/vision_pipeline_demo/forge/designs/design_packetizer.yml` | 18 | yes |
| `plugins/vision_pipeline_demo/forge/designs/design_full_functional.yml` | 12 | yes |
| every other `design_*.yml` (7 more files) | 3–10 each | yes |
| **`docs/tutorials/vision-pipeline-quickstart.md`** | 4 | **yes — public page** |
| **`docs/tutorials/vision-pipeline-full-design.md`** | 16 | **yes — public page** |
| `docs/explanation/project-scope.md` | 5 | partial — check per-line |
| `docs/concepts/clock-and-reset-domains.md` | 5 | partial — likely legitimate FORGE-concept §-refs, verify before touching |

`forge/` core files (`forge/ir/model.py`, `structural_verilog.py`, `topgen.py`, etc.) are
**out of scope** for this plan — plan §5 targets `forge plugins ci scripts docs`
broadly, but plan §19 explicitly excludes "renaming every existing FORGE subsystem" and
this plan's mission (§1) is the tutorial/vision_pipeline_demo experience, not a
framework-wide comment rewrite. **Recommendation for T1: scope the cleanup to
`plugins/vision_pipeline_demo/**` and the two public tutorial pages only**, flag the
~275-file FORGE-core convention as an explicit non-goal/deferral in the T0 risk list
(§5 below), and let CI enforcement (plan §5.6) apply only to those paths going forward
so new violations don't creep back in without blocking on an unrelated framework-wide effort.

### 1.14 Generated artifacts outside the build root

Established build roots (`build_hls_vision_pipeline_demo/`, `gen-top/`) are correctly
outside the plugin tree and gitignored. **Gap:** sim-run artifacts (`xsim_work/`,
`stimulus/`) live inside `plugins/vision_pipeline_demo/forge/verify/<flow>/` next to
source — `xsim_work/` is gitignored (`.gitignore:40`), `stimulus/` is not explicitly
ignored but also not tracked. Several genuinely generated files (`tb_algo_top.sv`,
`port_map.yaml`, `verify.flow.yml`, `stimulus_current.svh`) **are tracked in git** despite
carrying "DO NOT EDIT — regenerate with..." headers — a real, pre-existing
generated-file/source-control tension plan §6.4/§6.5 will surface but that predates this
plan and is out of scope to restructure (moving generated-but-tracked flow artifacts out
of the plugin tree is a `forge verify generate` core-behavior change, not a docs change).

### 1.15 `mkdocs.yml`

120 lines. `strict: true`, `exclude_docs: [plan/**, internal/**,
development/release-readiness.md]`, theme `material` with `navigation.tabs`,
`navigation.sections`, `navigation.top`, `content.code.copy`, `toc.follow` already
enabled (plan §11.2's suggested feature list is a superset — `navigation.tracking`,
`content.code.annotate`, `content.tabs.link`, `search.highlight` are not yet on).
`extra.version.provider: mike`. Current Tutorials nav is flat, 5 entries, 2 of them
this plugin's quickstart/full-design pages.

---

## 2. Capability map

Public FORGE capability → existing design/flow → existing command → existing artifact →
current tutorial chapter → gap.

| Capability | Design/flow | Command | Artifact | Current chapter | Gap |
|---|---|---|---|---|---|
| Project/module registry | `modules.yml` | `forge topgen validate-registry` | — | quickstart §1 | none — covered |
| Contracts/roles/protocols/cardinality/matching | all designs | `forge topgen validate` | — | quickstart §1 | interface-contract concepts not taught in-plugin, only linked out |
| Validation/diagnostics | `invalid_*` fixtures | `forge topgen validate` | diagnostics (ATG023/024) | full-design "negative fixtures" | pedagogy exists but crammed at the end of one page |
| Canonical IR | all designs | (internal to `gen-top`/`build`) | — | not shown | no chapter exposes the IR itself |
| Deterministic build plan | all designs | `forge build` | plan JSON | **not taught anywhere in vision_pipeline_demo docs** | gap — `forge build`/plan-hash is CLI-tested (10.7C) but undocumented |
| Plan acceptance/hash | — | `forge build --accept-plan-hash` | — | not shown | gap, same as above |
| Provenance/staleness | `design_full_functional.yml` | `forge inspect --provenance/--explain-staleness` | provenance JSON | full-design "visual explorer" §, one paragraph | thin — no worked staleness example |
| RTL generation | all designs | `forge topgen gen-top` | `algo_top.v` | quickstart §3 | covered |
| HLS execution/report extraction | `pixel_normalizer`, `sobel_hls`, `tile_stats_hls` | `forge hls gen-tcl`/`run` | HLS reports | quickstart §2 | HLS report *extraction* (`forge/analyze/hls_reports`) never shown |
| Mixed RTL/HLS integration | `design.yml` | — | — | quickstart | covered |
| Fixed latency | most modules | `forge analyze latency-check` | — | not shown | latency-check command itself never demonstrated in this plugin's docs |
| Alignment delay | `design_pixel_result.yml` | — | — | not shown | real capability, zero tutorial coverage |
| Bounded latency | `tile_boundary_rtl` | — | — | not shown | same |
| Elastic/tagged join | `tile_summary_join_rtl` | — | — | not shown | same |
| Clock/reset domains | `design_cdc.yml`, `design_platform_wrapper.yml` | `forge topgen validate` | — | full-design "CDC design" § | covered but dense, single page |
| `level_sync`/`pulse_sync`/`mailbox_transfer`/`async_fifo`/`reset_sync` | `design_cdc.yml` | — | — | full-design | covered, but all 5 in one paragraph |
| Static throughput | — | `forge analyze throughput` (name TBD, verify) | — | not shown | gap |
| Runtime throughput | `full_functional_xsim` probe | `--probe-log` + `render_throughput_result*.py` | `throughput_result.json` | not shown | real artifacts exist (checked into flow dirs) but never surfaced in docs |
| FIFO occupancy/high-water | `packetizer_xsim` | `check_fifo_capacity.py` | — | not shown | real script, no tutorial coverage |
| Backpressure | `invalid_fifo_depth_xsim` | — | — | full-design, brief | real negative-fixture pedagogy exists, thin |
| Dataset loading | all `*_xsim` | `DatasetService` | XML datasets | mentioned, not walked | adapters never individually demonstrated |
| Project adapter registration | `datasets/adapters/*.py` | — | — | not shown | gap |
| Golden-model execution | `golden_model_provider.py` | `run_golden_model` | — | quickstart §4, one paragraph | thin |
| Structured verification results | all flows | `forge verify run` | `FlowResult`/JUnit | quickstart §6 | result *artifact* never opened/inspected in docs, only pass/fail narrated |
| DOT | `design_full_functional.yml` | `forge inspect --dot` | `.dot`/`.svg` | full-design, one code block | no rendered SVG shown inline |
| SVG | same | same | `.svg` | same | same |
| Interactive explorer HTML | same | `forge inspect --explorer` | `.html` | full-design, one code block | never linked to an actual generated example |
| Report generation | **none** | `forge report` | `dashboard.html` | **not used anywhere in this plugin** | biggest gap — plan §10's entire ask |
| Negative fixtures | 2 fixtures | — | — | full-design, end of page | real, needs its own chapter (plan §12) |
| CI/reproducibility | `.gitlab-ci.yml`, `ci/framework-release.yml` | — | — | not shown | CI jobs never referenced from docs |

Capabilities explicitly out of tutorial scope per plan §4.2's own note ("internal release
tooling such as `docsgen` implementation internals does not need to be taught"): `forge
docsgen` internals, `forge/tests/` internal test harnesses.

---

## 3. Current user-journey audit (plan §4.3)

- **Can the quickstart be followed independently?** Yes — commands are copied from
  live `--help`/README per its own header, and it correctly assumes only the
  golden-path tutorial as a prerequisite.
- **Does the full-design page jump too abruptly?** Yes. It goes from "6 steps, 1 clock,
  2 modules" straight to "8 designs, 19 modules, 3 clock domains, 5 CDC kinds, 2 negative
  fixtures" in one page with one worked example (`design_cdc.yml`). This is exactly the
  pattern plan §7 identifies and directs to be replaced with 12 chapters.
- **Are commands byte-accurate and executable?** Spot-checked and yes for the commands
  shown. However the prose around them has drifted from the code in at least one place
  (see next bullet) — commands being copy-pasted correctly does not guarantee the
  narrative text describing them stays accurate.
- **Found inaccuracy:** `vision-pipeline-full-design.md:25` states
  `tile_stats_hls (II=2)`. The actual, current, HLS-pragma-verified value is **II=1**
  (`algo/tile_stats/tile_stats_hls.cpp:20`, `#pragma HLS PIPELINE II=1`; the module was
  rewritten from II=2 to II=1 as a 10.5 follow-up, per that file's own header comment and
  `preflight.md`). The quickstart page's pipeline diagram and module list are otherwise
  accurate. **This must be fixed regardless of the broader chapter refactor** — it is a
  factual defect in currently-published content, not a productization gap.
- **Can the reader identify authored vs. generated files?** Not systematically — no
  ownership legend or annotated tree exists anywhere in current docs (plan §6 gap, confirmed).
- **Are expected outputs shown?** No — both pages describe what commands do in prose but
  never show a captured terminal excerpt or artifact snippet.
- **Does each intermediate design have a clear purpose?** Yes, in `preflight.md`'s
  internal slice framing — but the two public pages present that purpose in
  internal-slice language (`(slice 10.2)`, `(slice 10.7A)`) rather than capability
  language, which plan §5/§7.5 explicitly forbid on public pages.
- **Is visual output meaningful rather than merely linked?** No visuals exist in either
  page today — no image panels, no rendered SVG, no report screenshot. Plan §9's entire
  figure system is net-new for this plugin.
- **Are negative fixtures explained pedagogically?** Partially — `vision-pipeline-full-
  design.md`'s "Negative fixtures" section is reasonably good prose, but it's one
  subsection of an already-overloaded page rather than a guided lesson (plan §12).
- **Is the tutorial usable without reading internal planning documents?** Mostly, but
  both pages link/reference `docs/internal/phase10/preflight.md` and use phase/slice
  numbers as the primary way of explaining *why* a design exists, which leans on internal
  framing even where the reader never opens the linked file.
- **Is the full path realistic for a clean external checkout?** Untested by this audit —
  T0 did not run a clean-checkout `./run_vision_pipeline_demo.sh` (that's plan §17.6/T8's
  job); flagged as a risk below since HLS/Vivado toolchain availability is an environment
  assumption baked throughout both pages.

---

## 4. Explicit migration risks

1. **Scope creep into FORGE-core comment cleanup.** §1.13 found 1,070 phase/slice
   references across 281 files, 96% of them in FORGE core, not this plugin. T1 must be
   scoped to `plugins/vision_pipeline_demo/**` + the 2 public tutorial pages, or this
   single slice balloons into a framework-wide effort the plan's own §19 non-goals
   ("renaming every existing FORGE subsystem") argue against.
2. **`forge report` has never been run against this plugin.** It's the direct
   implementation vehicle for plan §10 (offline HTML bundle) and already does most of
   the work, but zero evidence exists yet that it produces sane output for a
   multi-CDC, multi-domain design. First real run against `design_full_functional.yml`/
   `design_platform_wrapper.yml` should happen early (T5/T6), not be assumed to "just work."
3. **No `ReportAttachmentProvider`-shaped extension point exists.** Plan §10.3's shape is
   illustrative, not mandated ("do not implement this exact API blindly") — but *some*
   extension mechanism is a genuine new FORGE-core surface, which cuts across plan §5's
   "do not move vision-domain semantics into FORGE core" boundary. This needs a design
   decision (own ADR candidate) before T4/T6, not ad-hoc implementation.
4. **Tracked-but-generated files under `forge/verify/<flow>/` are a pre-existing tension**
   (§1.14) that this plan's ownership-annotation work (§6) will make newly visible to
   readers ("why is this generated file in git, with edits pending after every run?").
   Recommend documenting this honestly in the ownership legend rather than silently
   restructuring `forge verify generate`'s output location, which is out of this plan's
   stated scope.
5. **`run_vision_pipeline_demo.sh` has no per-step entry point today** (§1.10) — plan
   §8.2's `--step`/`--list` UX is a real behavior change to the stable external entry
   point, not a docs-only change, and needs its own test coverage before chapters can
   claim "run only this chapter" (plan §17.1) truthfully.
6. **Known content defect already found** (§3): the II=2/II=1 tutorial inaccuracy should
   be fixed as part of T1 regardless of chapter restructuring, since it's wrong today
   independent of this plan.
7. **Clean-checkout reproducibility is unverified** (§3, last bullet) — T0 did not
   execute a clean-checkout run; T8 must not assume the documented commands work outside
   the current dev environment without doing so at least once.
8. **`docs/concepts/clock-and-reset-domains.md` and similar cross-referenced concept
   pages** were flagged with legitimate-looking §-references in §1.13 — verify each
   individually before any blanket phase-reference removal touches shared/non-plugin pages.

---

## 5. Target page tree (adopted from plan §7.1, no changes needed after inspection)

```text
docs/tutorials/vision-pipeline/
  index.md
  01-quickstart.md
  02-project-structure-and-contracts.md
  03-mixed-rtl-hls.md
  04-parallel-paths-and-latency.md
  05-bounded-and-elastic-processing.md
  06-clock-domains-and-cdc.md
  07-throughput-backpressure-and-fifos.md
  08-datasets-and-golden-models.md
  09-full-functional-design.md
  10-platform-integration.md
  11-inspect-report-and-reproduce.md
  12-diagnostics-and-negative-fixtures.md
  next-steps.md
```

Mapping existing designs to chapters (verified filenames, per §1.1):

| Chapter | Design | Flow |
|---|---|---|
| 01 | `design.yml` | `quickstart_pipeline_xsim` + `pixel_normalizer_csim` |
| 03–04 | `design_pixel_result.yml` | `pixel_result_xsim` |
| 05 | `design_tile_stats.yml` | `tile_stats_xsim` |
| 06 | `design_cdc.yml` | `cdc_xsim` |
| 07 | `design_packetizer.yml` (+ `invalid_fifo_depth_packetizer.yml`) | `packetizer_xsim` (+ `invalid_fifo_depth_xsim`) |
| 09 | `design_full_functional.yml` | `full_functional_xsim` |
| 10 | `design_platform_wrapper.yml` (+ `invalid_direct_bus_cdc.yml`) | `platform_wrapper_xsim` |
| 12 | both negative fixtures, revisited pedagogically | — |

`docs/tutorials/vision-pipeline-quickstart.md` and `-full-design.md` become compatibility
redirect stubs pointing into the new tree (plan §7.1), not deleted.

---

## 6. Artifact source map (plan §9.3, verified locations)

| Structured artifact | Producer | Consumer for tutorial figures |
|---|---|---|
| `forge.throughput_result` v1 | `forge/verify/throughput_result.py` + `render_throughput_result*.py` | throughput/occupancy figures (T5) |
| `forge.cdc_verification_result` v1 | `forge/verify/cdc_verification_result.py` | CDC summary figures (T5) |
| `forge.golden_comparison_result` v1 | `forge/verify/golden_comparison_result.py` | pass/fail + error-map figures (T4/T5) |
| `FlowResult` | `forge/verify/results.py` | verification summary section (T6) |
| `ProvenanceManifest` | `forge/ir/provenance.py` | provenance/staleness section (T6) |
| `DesignGraph` | `forge/analyze/design_explorer/graph_model.py` | topology SVG/explorer (T5) |
| dataset/expected/observed XML | `datasets/serialize_xml.py`, `schemas/data/*.xml` | image-domain figures (T4, plugin-owned renderer) |

---

## 7. Recommendation

Proceed to T1 (comment/reference hygiene), scoped per §1.13's recommendation
(`plugins/vision_pipeline_demo/**` + the 2 public tutorial pages only), fixing the
II=1 defect (§3) in the same pass since it's in the same files. T2 (ownership vocabulary
+ tutorial manifest) and the `forge report` dry run (§4 risk 2) should follow before
committing to the full T3 chapter rewrite, since both inform what each chapter can
truthfully show.
