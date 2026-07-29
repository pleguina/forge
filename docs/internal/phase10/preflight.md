# Phase 10 — Reference Example: Preflight / Freeze

**Status: decisions resolved (v2 of this document). Tracked in git** —
moved here from the gitignored `docs/plan/` workspace specifically
because this is release-gating material, not scratch notes (see §0).

## Context

Phases 0-9 of the FORGE release plan (`docs/plan/FORGE_release_plan.md`)
are implemented. Phase 10 — a genuinely unrelated-domain reference
project, `vision_pipeline_demo` — is next, and is the release plan's own
last unmet P0 gate (`docs/development/release-readiness.md:182`).

This is a preflight/freeze pass, not Phase 10 itself: its job is to fix
the example's boundary before any module is implemented. **v1** of this
document (superseded, folded in below) ran a code-verified capability
audit against the current `rename/forge` branch and found several
capabilities the reference spec assumes are only declared, partially
implemented, or absent — most importantly, real CDC-kind coverage,
throughput/occupancy telemetry, and a golden-model abstraction. **v2**
(this document) resolves every decision v1 left open, and adds five
further corrections found in review: a packet-width arithmetic error, an
unstated metadata-join buffering problem, an unreconciled Sobel/
N-dimensional-binding claim, a dataset-command-path gap, and a
report-artifact structuring gap. Nothing below is a suggestion still
awaiting sign-off — this is the frozen plan Phase 10 module work builds
against.

## 0. Source documents and tracking

Three documents govern Phase 10:

- `docs/plan/FORGE_release_plan.md` — the master phase-ordering document.
  Its Phase 10 section has been updated to point here and to use the
  correct project name (`vision_pipeline_demo`, not the placeholder
  `image_pipeline_demo` it originally used) — see that file's Phase 10
  section.
- `docs/internal/phase10/vision_pipeline_reference_project.md` — the full
  2308-line module-by-module specification (moved from
  `docs/plan/FORGE_vision_pipeline_reference_project_v2.md`).
- `docs/internal/phase10/preflight.md` — this file.

**Both files under `docs/internal/phase10/` are now tracked in git**,
unlike their previous location under the entirely-gitignored `docs/plan/`
— a release-gating specification cannot live only in a local, untracked
file. `mkdocs.yml`'s `exclude_docs:` now excludes `internal/**` (alongside
the pre-existing `plan/**` and `development/release-readiness.md`
exclusions) so this material stays out of the public documentation site
without needing to stay untracked to do so.

## 1. Frozen base scope

The full mandatory base from v1 §1 stands, **with no A2/B2-style
descoping** — see §5. Every capability the spec's mandatory base names
(RTL+HLS, fixed/bounded/elastic latency, II/throughput, backpressure/FIFO
occupancy, clock/reset domains, all five CDC kinds, dataset adaptation,
golden-model comparison, provenance, generation-plan behavior, visual
explorer output, documentation/tutorial integration) must be real before
the example is called complete. hls4ml remains outside the mandatory gate
(spec §27, §2.1), unchanged from v1.

## 2. Framework-capability audit (unchanged from v1 — still the accurate baseline)

This table is preserved verbatim from v1; it is the evidence base
Decisions A-D below resolve against. Every row was checked against the
actual source and tests on the `rename/forge` branch, not assumed.

| Example requirement | FORGE implementation | Verdict |
|---|---|---|
| Fixed latency | `LatencyDeclaration(kind="fixed")` → `ResolvedModuleDefinition.latency` (`forge/ir/model.py:145`) | **REAL, tested** (`forge/tests/test_module_timing.py`) |
| Bounded latency | `LatencyDeclaration(kind="bounded")`; `latency_static/checker.py`'s `check_merge_points` computes real `[min,max]` overlap, classifies `"bounded_skew"` | **REAL, tested**, genuine range-overlap semantics (`forge/tests/test_latency_checker.py`) |
| Elastic latency | `LatencyDeclaration(kind="elastic")`; checker marks merge `"elastic_buffer"`, never a mismatch | **REAL as a timing-model label only** — no wiring to any buffer/FIFO IR element |
| II / throughput | `HLSModuleReport.pipeline_ii/interval_min/interval_max` (`forge/analyze/hls_reports/extractor.py`) | **PARTIAL** — real fields, zero integration into `latency_static` topology analysis |
| Backpressure / FIFO occupancy | `HLSModuleReport.buffering_capacity/occupancy/backpressure/frame_rate` declared, no producer anywhere (`extractor.py:73-85`) | **RESERVED, NOT IMPLEMENTED** |
| Async FIFO transformation (RTL) | `ResolvedTransformation(kind="async_fifo")` structurally approved; no generated RTL body (`forge/ir/model.py:200-204`, `structural_verilog.py:1114-1123`) | **RESERVED, NOT IMPLEMENTED** |
| Clock/reset domains | `domains:`/`domain_map`, `ResolvedClockDomain`/`ResolvedResetDomain` | **REAL, tested** |
| CDC: level sync | `cdc: {kind: 2ff_sync}` → real `cdc_sync2ff` RTL, +2 cycle latency | **REAL, tested** |
| CDC: pulse sync | *(spec requires distinct pulse-synchronizer kind)* | **DOES NOT EXIST** — `KNOWN_CDC_KINDS = {"2ff_sync", "async_fifo"}` is the entire vocabulary |
| CDC: mailbox (coherent multi-bit) | *(spec requires atomic multi-bit req/ack kind)* | **DOES NOT EXIST** |
| CDC: stream (multi-bit ready/valid) | Closest match `async_fifo` — declared, no body | **RESERVED, NOT IMPLEMENTED** |
| CDC: reset-domain crossing | Folded into the same generic `cdc:` approval as clock-domain crossing | **REAL but not distinct** |
| Dataset adaptation (layer A/B) | `dataset_format.py` / `dataset_adapter.py` (real `Protocol` + registry) | **REAL, tested** |
| Golden-model comparison | *(spec requires independent reference-model stage)* | **DOES NOT EXIST anywhere in the repo** |
| Provenance | `forge.ir.provenance.ProvenanceManifest` | **REAL, tested** (Phase 5) |
| Generation-plan behavior | `forge build --plan/--apply/--accept-plan-hash` | **REAL, tested** (Phase 3/6) |
| Runtime event selection (readmemh) | `forge.verify.readmemh_stimulus.write_event_memory_file` | **REAL, explicitly fixed-shape only** |
| Structured results | `FlowResult`/`EventResult` | **REAL, tested** (Phase 7) |
| Explorer external-port rendering | `GraphNodeKind.EXTERNAL_PORT`, real materialized nodes | **REAL, tested** |
| Explorer latency overlay | `build_design_graph(..., latency_by_instance=...)` | **REAL as a function arg — not wired into any real CLI call** |
| Documentation/tutorial integration | `forge.docsgen` (Phase 9) auto-generates reference pages only | **PARTIAL** — tutorials are hand-authored, no generation tool |

## 3. Phase 7 dataset architecture (unchanged findings from v1, now acted on in §7)

The FORGE-owned/project-owned split is real and matches the intended
framing (see v1 §3 for the full eight-question walkthrough — all
preserved, none superseded). Three gaps found there are now resolved by
decision, not just documented: (1) the main `forge test run` CLI path
bypassing layer A/B — fixed by the unified `DatasetService` in §7; (2)
`preprocessing_hash` never being computed — fixed by
`compute_preprocessing_hash()` in §7; (3) no golden-model concept
existing — fixed by the `GoldenModelProvider` protocol in §5, Decision C.

## 4. Phase 8 visual model (unchanged from v1 — still accurate)

7 of 9 properties confirmed solid (external ports as real nodes, resolvable
edge endpoints, `DesignGraph` as a pure IR projection, typed/resolvable
diagnostic references, correct module-vs-instance diagnostic attachment,
explicit CDC-transformation rendering, portable paths — see v1 for full
citations, all still accurate). Two gaps, both actioned:
- Overlay data (latency, verification) not wired into `forge report`/
  `forge inspect` CLI calls — becomes **Slice 10.0D** (§11).
- Physical-port-array expansion is static display only, not an
  interactive UI operation — remains an explicit **non-blocker** deferral
  (§15); nothing in the frozen scope requires it.

## 5. Decisions A-D — resolved

### Decision A — CDC kind expansion: chosen (A1), with `reset_sync` added

Five first-class CDC kinds, implemented in `forge.topgen.ip.cdc` before
any vision-pipeline module lands:

- **`level_sync`** — exactly 1 bit, stable-level semantics, fixed
  destination-domain latency. `2ff_sync` is preserved as a
  **backwards-compatible alias** for `level_sync` (existing designs using
  `2ff_sync` keep working unchanged; new designs use the semantic name).
- **`pulse_sync`** — exactly 1 event signal, a declared minimum
  source-event spacing, no loss/duplication guaranteed within that
  contract.
- **`mailbox_transfer`** — coherent multi-bit payload, request/
  acknowledge handshake, atomic destination-side update, one outstanding
  transaction supported initially (no pipelining of mailbox transfers in
  this phase).
- **`async_fifo`** — multi-bit stream, ready/valid, parameterized width/
  depth, order-preserving, independent reset handling on each side.
- **`reset_sync`** — reset only, asynchronous assertion, synchronous
  deassertion in the destination domain. Promoted to its own first-class
  transformation kind rather than folded into a generic data-crossing
  declaration, since the design requires per-destination-domain
  asynchronous-assert/synchronous-deassert behavior specifically — a
  reset crossing is not "data that happens to cross domains."

Each kind requires, uniformly: schema validation (contract-verifier
rules for the kind's required fields), permitted signal width/protocol
constraints, real generated RTL, explicit reset semantics, a latency
declaration for its destination-domain effect, source/destination-domain
validation (reusing `forge.topgen.ip.domains.resolve_domain_nets`), a
dedicated diagnostic code per failure mode, and both a positive and a
negative fixture.

This becomes **Slice 10.0B** (§11) — real, multi-day core-framework
engineering, done before module implementation, not hidden inside
`vision_pipeline_demo`.

### Decision B — Backpressure/occupancy: chosen (B1), with static/runtime metrics kept separate

Real generic async-FIFO RTL generation, plus real occupancy telemetry —
**not** placed in `HLSModuleReport` (HLS synthesis reports structurally
cannot carry simulation-derived high-water occupancy; conflating the two
would misrepresent a static-analysis artifact as runtime data). Two
distinct result types instead:

- **`StaticThroughputAnalysis`** — module II, clock frequency, records/
  cycle, width, nominal capacity, predicted bottleneck, required
  buffering. Derived from HLS reports + declared clock frequencies —
  available without running any simulation.
- **`RuntimeThroughputResult`** — accepted transactions, emitted
  transactions, stall cycles, FIFO occupancy, high-water mark, full/empty
  events, measured throughput, dropped/duplicated transactions. Derived
  from real simulation.

The generated `async_fifo` RTL exposes generic instrumentation signals/
probes (`occupancy`, `high_water`, `full`, `empty`, `overflow_attempt`,
`underflow_attempt`) — omittable from the production external interface,
enabled for verification builds. Both result types get real schemas
(§8), not free-text report sections.

This becomes **Slice 10.0C** (§11), alongside the structured-schema work
from §8.

### Decision C — Golden model: project-owned algorithm, FORGE-owned provider/runner protocol

Adjusted from v1's "project-owned only" recommendation. The algorithm
itself stays entirely project-owned (`vision_pipeline_demo.golden_model`)
— FORGE gets one minimal execution protocol, not a domain-aware
abstraction:

```python
class GoldenModelProvider(Protocol):
    provider_id: str
    provider_version: str

    def evaluate(
        self,
        dataset: CanonicalDataset,
        config: Mapping[str, object],
    ) -> ExpectedDataset:
        ...
```

FORGE owns: provider resolution (a registry, mirroring
`register_dataset_adapter`'s existing pattern), deterministic invocation,
input dataset identity, expected-output serialization, output hashing,
cache/staleness (reusing the Phase 5 provenance/content-hash machinery),
comparison orchestration, and reporting. The project owns: what
"expected behavior" means for its domain. Rationale: without this, the
vision project would build a plugin-local runner that no second project
could reuse — the same "protocol + registry, not a domain-specific base
class" shape already proven by `ProjectDatasetAdapter`.

This is scoped into **Slice 10.0A** (§7, §11), alongside the dataset
service.

### Decision D — Release-plan reconciliation: accepted, plus document tracking (done)

`FORGE_release_plan.md`'s Phase 10 section now points at this document
and the spec, and no longer states `image_pipeline_demo` as the
recommended name without qualification (see §0). Both planning documents
are now tracked in git under `docs/internal/phase10/`, excluded from the
public MkDocs site via `exclude_docs:`, rather than living only in the
gitignored `docs/plan/` workspace.

## 6. Spec corrections (found in review, frozen here)

### 6.1 Packet-width arithmetic — corrected

The original spec's implied packing (`edge_mask_stream` payload at 77
bits — `normalized_pixel{8} + gradient_magnitude{12} + threshold_mask{1}
+ x{12} + y{12} + frame_id{16} + tile_id{16}` — into a 64-bit
`packet_stream.data`, "2 records/beat") is physically impossible: 2×77 >
64, and even 1×77 > 64. **Corrected design**:

- **Pixel-result record** (128 bits): `record_kind{2}`,
  `normalized_pixel{8}`, `gradient_magnitude{12}`, `threshold_mask{1}`,
  `x{12}`, `y{12}`, `frame_id{16}`, `tile_id{16}`, `end_of_line{1}`,
  `end_of_frame{1}`, `reserved{47}` = 128 bits.
- **Tile-statistics record** (128 bits): `record_kind{2}`, `tile_id{16}`,
  `minimum{8}`, `maximum{8}`, `mean{16}`, `variance{24}`,
  `frame_id{16}`, `reserved{38}` = 128 bits.
- **`forge.packet_stream.v1` frozen at**: `data{256}`, `keep{32}`,
  `last{1}` + ready/valid — carrying 2×128-bit records per 256-bit beat.

At 125MHz, 2 records/cycle × 125 MHz = 250 Mrecord/s, which genuinely
exceeds the 200 Mrecord/s pixel-domain input rate — the throughput story
is preserved, correctly, at the corrected width. (A single-128-bit-record
interface at a faster output clock is a valid alternative architecture,
but 256 bits at 125 MHz is the frozen choice, since it cleanly preserves
the existing 2-records/beat narrative.) **This must be frozen before any
RTL, dataset, golden-model output, or throughput test is written.**

### 6.2 Metadata-join restructuring — corrected

The original topology's implied per-pixel join against per-tile
statistics would require buffering and replaying an entire tile (≥64
pixel records for an 8×8 tile) before any output could be emitted — an
unstated buffering requirement the spec never froze. **Corrected
architecture**: two independent output paths, multiplexed at the
packetizer, not merged before it:

- **Per-pixel path**: normalized pixel → Sobel → threshold → exact-cycle
  merge → pixel-result packet records (kind: pixel-result).
- **Per-tile path**: tile-end summary + `tile_stats_hls` → tagged elastic
  join → one tile-statistics packet record per tile (kind:
  tile-statistics).
- **Packetizer** multiplexes both record kinds onto the corrected
  256-bit `packet_stream` (§6.1), using `record_kind` to disambiguate.

This still exercises every capability the original design intended
(exact-cycle reconvergence, bounded statistics latency, elastic
tag-based join, multiple record kinds, output buffering, backpressure)
without requiring whole-tile pixel buffering. **Frozen as the topology
before implementation** — the original preflight (v1) did not freeze
this cardinality relationship; this correction closes that gap.

### 6.3 Sobel / N-dimensional binding — reconciled: `window_builder_rtl` chosen

The spec wants an N-dimensional 3×3 physical binding, but a scalar
pixel-stream input (as the topology implies) cannot expose a 3×3 port
array by itself — this was an implicit, unreconciled contradiction, not a
choice actually made anywhere in the spec. **Resolved**: introduce
`window_builder_rtl` between the normalizer and Sobel:

```
pixel_normalizer_hls -> window_builder_rtl -> [3x3 window interface] -> sobel_hls
```

`window_builder_rtl` (RTL) owns line buffers, border policy, x/y
tracking, and emits one 3×3 window per accepted output pixel — this is
where the N-dimensional (`raw_port_tpl`, `dims:[3,3]`) physical binding
actually lives. `sobel_hls` becomes a small, pure 3×3-compute HLS module
with no internal state. This is the chosen design (not the alternative
of `sobel_hls` owning its own line buffers, which would mean dropping the
N-dimensional-binding claim entirely) — it additionally exercises stream
input, RTL state, N-dimensional contract binding, HLS compute, and
explicit inter-branch latency all at once, which is exactly what the
example exists to prove. **The two paths (window-builder vs.
self-contained Sobel) must not both be attempted implicitly** — this
document freezes the window-builder path as the one to implement.

## 7. Dataset service and golden-model runner (Slice 10.0A, expanded)

Beyond the adapter-seam completion v1 already called for, two further
framework-core additions are frozen here:

**A unified `DatasetService`**, so every command path uses the same
layer A/B machinery instead of the current split (only the readmemh path
uses the real adapter; `forge test run`'s default event loop still
parses XML directly — v1 §3, still-open finding):

```python
class DatasetService:
    def load(self, source: DatasetSource) -> SerializedDataset: ...
    def list_events(self, dataset: CanonicalDataset) -> list[str]: ...
    def select_events(self, dataset: CanonicalDataset, ids: list[str]) -> CanonicalDataset: ...
    def materialize(self, source: DatasetSource, adapter_id: str, config: Mapping[str, object]) -> CanonicalDataset: ...
    def compute_identity(self, dataset: CanonicalDataset) -> str: ...
```

Every command path — `forge test run`, readmemh preparation, golden-model
execution (via the `GoldenModelProvider` runner, §5 Decision C), dataset
inspection, report generation — routes through this service. `test.py`'s
direct-XML-parse path (`_enumerate_xml_event_ids`) is retired as a
parallel path, not left to coexist with it.

**A new hashing utility**, `compute_preprocessing_hash(config)`, added to
FORGE's generic hashing utilities (alongside `forge.core.utils.content_hash`).
The project declares its own preprocessing semantics; FORGE canonicalizes
and hashes them, populating the `preprocessing_hash` field that already
exists in `SemanticMetadata` but has never had a real producer (v1 §3,
item 4). **Excludes**: local source path, timestamp, hostname. **Includes**:
adapter ID/version, resize method, grayscale method, rounding policy,
saturation policy, tiling parameters, border behavior, and source-content
hashes.

## 8. Structured result artifact schemas (new — replaces v1's "no report changes needed")

v1 assumed the existing `forge report` Markdown format needed only new
sections. That understates the gap: FORGE currently has no structured
(non-Markdown) artifact type for throughput, CDC behavior, or
golden-model comparison results — injecting these directly into Markdown
would lose exactly the kind of machine-readable, versioned-schema
discipline every other FORGE artifact already has (`FlowResult`,
`ProvenanceManifest`, `DesignGraph`). Three new versioned schemas, frozen
at `v1`:

- **`forge.throughput_result.v1`** — carries both `StaticThroughputAnalysis`
  and `RuntimeThroughputResult` (§5, Decision B) plus per-FIFO occupancy
  records, e.g.:
  ```json
  {
    "schema": {"name": "forge.throughput_result", "version": "1.0"},
    "design_hash": "...", "dataset_hash": "...", "scenario_hash": "...",
    "predicted_rate": 200000000, "observed_rate": 198750000,
    "bottleneck": "source",
    "fifos": [{"object_id": "xform:result_stream:async_fifo", "depth": 64, "high_water": 31, "overflow_attempts": 0}]
  }
  ```
- **`forge.cdc_verification_result.v1`** — one entry per CDC crossing
  (kind, source/destination domain, pass/fail, the specific property
  checked).
- **`forge.golden_comparison_result.v1`** — per-event pass/fail against
  the `GoldenModelProvider` output, plus the provider id/version and
  expected-output hash used.

`forge report` reads these artifacts and renders sections from them —
it does not replace or duplicate their schemas, and the existing
`CommandEnvelope` may reference them by path/hash without embedding their
full content. This is real, scoped work, folded into **Slice 10.0C**.

## 9. Interface/protocol schema freeze

Corrected schemas per §6.1:

- `forge.pixel_stream.v1` — unchanged from spec: `data{8}, x{12}, y{12}, frame_id{16}, tile_id{16}, end_of_line{1}, end_of_frame{1}` + ready/valid.
- `forge.edge_mask_stream.v1` — unchanged field list, now understood as feeding the corrected 128-bit pixel-result packet record (§6.1), not directly into a 64-bit beat.
- `forge.tile_statistics.v1` — unchanged field list, feeds the corrected 128-bit tile-statistics record.
- `forge.configuration_mailbox.v1` — `threshold{8}, kernel_mode{2}, frame_limit{16}` + request/acknowledge (not ready/valid). Confirm `"request-acknowledge"` is added to `forge.topgen.ip.contract_verifier.KNOWN_PROTOCOLS` if not already present — needed regardless for `mailbox_transfer` CDC (§5, Decision A).
- **`forge.packet_stream.v1` — corrected**: `data{256}, keep{32}, last{1}` + ready/valid (§6.1).

**Signedness — decided**: all external/interface fields are unsigned.
Sobel's internal `Gx`/`Gy` intermediates are signed (never exposed on any
interface); `gradient_magnitude` is unsigned, computed post-absolute-value/
saturation.

**Remaining freezes required before RTL/HLS/dataset/golden-model
implementation** (checklist — each needs an explicit answer at Slice 10.0
time, not a default assumed silently):

- [ ] Sobel gradient-magnitude equation: `abs(Gx) + abs(Gy)` vs.
  approximate/true Euclidean magnitude.
- [ ] Saturation policy on `gradient_magnitude`/`normalized_pixel`.
- [ ] Threshold comparison operator: `>=` vs. `>`.
- [ ] Tile traversal order (row-major vs. other).
- [ ] Tile-ID formula (the spec's own skeleton code uses
  `(y // tile_height) * 1024 + (x // tile_width)` — confirm or replace).
- [ ] Border-handling policy for Sobel's edge tiles (via `window_builder_rtl`, §6.3).
- [ ] Mean/variance rounding policy.
- [ ] Population vs. sample variance.
- [ ] Packet record-kind values and packing order within a 256-bit beat
  (§6.1/§6.2 — two kinds now, `record_kind` disambiguates).
- [ ] Configuration-update timing: applied immediately vs. at the next
  frame boundary.

These directly affect bit-exact datasets and golden-model output —
changing any of them after RTL/HLS/datasets exist causes exactly the
churn the original spec warned about.

## 10. Dataset strategy, including the fixed-shape mandatory contract

Tiers unchanged from v1 (synthetic exact fixtures → functional/CDC/reset/
latency; synthetic long stream → throughput/backpressure/occupancy; user
image folder → adapter demonstration; BSDS500 → optional; Fashion-MNIST →
optional hls4ml only).

**New: the fixed-shape runtime-stimulus constraint (readmemh is
explicitly fixed-shape only, v1 §2 item 8) is written into the mandatory
dataset contract, not left implicit**:

- Quickstart acceptance frames: normalized to **8×8**.
- Full-functional acceptance frames: normalized to **16×16** (or 32×32 —
  confirm at Slice 10.1/10.2 implementation time, not here).
- Throughput acceptance: a synthetic, fixed-width stream (no image
  decoding in the hot path).
- Every event within one dataset shares the same record shape and a
  fixed maximum control schedule; output record width is fixed per
  scenario.
- User-provided images are resized to the configured mandatory dimensions
  by the project adapter (§7's `DatasetService.materialize`) — variable-
  size image events remain an explicit future dataset-package extension,
  not attempted in Phase 10's mandatory base.

## 11. Sub-slice sequence (revised — fully committed, no conditional branches)

| Slice | Scope |
|---|---|
| 10.0 | Track and reconcile specifications (this document); freeze interfaces (§9), packet format (§6.1), tile/statistics record semantics (§6.2), and the Sobel/window-builder topology (§6.3) |
| 10.0A | Unified `DatasetService` (§7), project-adapter support, real `compute_preprocessing_hash`, `GoldenModelProvider` protocol + runner (§5 Decision C) |
| 10.0B | CDC primitive family: `level_sync` (alias `2ff_sync`), `pulse_sync`, `mailbox_transfer`, `async_fifo` (real RTL body), `reset_sync` (§5 Decision A) |
| 10.0C | Static throughput model, runtime occupancy/backpressure results, the three structured result schemas (§8, §5 Decision B) |
| 10.0D | Wire `latency_by_instance`/`verification_flow_entry_points` overlays into real `forge inspect`/`forge report` CLI calls (§4) |
| 10.1 | One-clock quickstart: normalizer HLS → threshold RTL → sink, synthetic 8×8 data, exact comparison |
| 10.2 | `window_builder_rtl`, Sobel, threshold, alignment delay, exact-cycle merge (pixel-result path only, §6.2) |
| 10.3 | Per-tile bounded statistics (`tile_stats_hls`) and elastic tile-summary join (tile-statistics path, §6.2) |
| 10.4 | Multiple domains and all five CDC kinds (§5 Decision A), reset synchronizers, negative CDC fixtures |
| 10.5 | Packetizer (multiplexing both record kinds, §6.1/§6.2), async FIFO, throughput, backpressure, occupancy, invalid rate/depth fixtures |
| 10.6 | Synthetic, image-folder, and NumPy adapters (via `DatasetService`), manifests, staleness tests |
| 10.7 | Platform wrapper, provenance, plan hashes, visual explorer (incl. the 10.0D CLI wiring), documentation, clean-user workflow, full CI |
| 10.8 | Optional hls4ml, only after the base acceptance gate passes |

Negative fixtures land incrementally alongside each capability (the
spec's own 10-item invalid-design matrix), not batched at the end —
unchanged from v1's recommendation, now explicitly attached to the slice
that introduces the corresponding positive capability (e.g. the three
CDC negative fixtures land in 10.4, throughput/FIFO-depth negative
fixtures land in 10.5).

## 12. CI tiers (unchanged from v1)

Fast MR CI → Standard acceptance CI → Scheduled HLS CI → Optional hls4ml
CI, per the spec's own §29 — no changes needed.

## 13. Release acceptance command

Adopt the spec's own `release_acceptance` flow, surfaced via `forge
report`, now reading the three new structured artifacts (§8) alongside
the existing `FlowResult`/`ProvenanceManifest`/`DesignGraph` — content
list unchanged from v1 (functional result, latency/throughput/CDC
summaries, dataset identity, plan hash, provenance, backend, visual
explorer, generated artifacts), but throughput/CDC/golden-comparison
sections now render from real versioned artifacts instead of being
computed ad hoc into Markdown.

## 14. Scale-benchmark migration (unchanged from v1)

After 10.7, replace Phase 8's `trigger_demo`-based "largest reference
design" baseline with the vision-pipeline design for graph-construction
performance, DOT/HTML generation, edge completeness, visual search/filter
responsiveness, deterministic output size, and path/diagnostic
resolution. Record baseline measurements first; no aggressive wall-clock
threshold up front.

## 15. Honest deferral list (updated)

- Interactive physical-port-array expansion in the visual explorer (§4) —
  optional, not a blocker, unchanged from v1.
- hls4ml extension — deferred to Slice 10.8, unchanged.
- BSDS500/Fashion-MNIST real-image tiers — optional, Tier C, no-network
  by default, unchanged.
- Variable-size image events (beyond the fixed-shape mandatory contract,
  §10) — explicit future dataset-package extension, not Phase 10.
- A code-generation tool for tutorial pages — not attempted; standard
  hand-authored MkDocs pages remain the convention.
- Mailbox-transfer pipelining (more than one outstanding transaction,
  §5 Decision A) — explicitly out of scope for the first `mailbox_transfer`
  implementation.
- The exact frame size for "full-functional" acceptance (16×16 vs. 32×32,
  §10) — left to Slice 10.1/10.2 implementation time, not frozen here.
