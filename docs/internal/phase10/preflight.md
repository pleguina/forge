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
time, not a default assumed silently). **Resolved below in §9.1**:

- [x] Sobel gradient-magnitude equation: `abs(Gx) + abs(Gy)` vs.
  approximate/true Euclidean magnitude.
- [x] Saturation policy on `gradient_magnitude`/`normalized_pixel`.
- [x] Threshold comparison operator: `>=` vs. `>`.
- [x] Tile traversal order (row-major vs. other).
- [x] Tile-ID formula (the spec's own skeleton code uses
  `(y // tile_height) * 1024 + (x // tile_width)` — confirm or replace).
- [x] Border-handling policy for Sobel's edge tiles (via `window_builder_rtl`, §6.3).
- [x] Mean/variance rounding policy.
- [x] Population vs. sample variance.
- [x] Packet record-kind values and packing order within a 256-bit beat
  (§6.1/§6.2 — two kinds now, `record_kind` disambiguates).
- [x] Configuration-update timing: applied immediately vs. at the next
  frame boundary.

These directly affect bit-exact datasets and golden-model output —
changing any of them after RTL/HLS/datasets exist causes exactly the
churn the original spec warned about.

### 9.1 Checklist resolutions — frozen

Each item below is decided against evidence already in this repo — real
10.1 code, the spec's own fixture/flow names, or arithmetic forced by
already-frozen bit widths — not a fresh, ungrounded preference. Nothing
here is provisional; slice 10.2 implementation must match these exactly.

**Sobel gradient-magnitude equation: `abs(Gx) + abs(Gy)`.** True or
approximate Euclidean magnitude requires a square root with no
integer-exact definition reproducible identically bit-for-bit across
HLS, RTL, and the Python `GoldenModelProvider` — which would break the
same "bit-deterministic" requirement the spec already states for dataset
preprocessing (§18.14) and that `pixel_normalizer.cpp` already honors
with pure integer/clamp arithmetic (no floating point anywhere in the
quickstart tier). `abs(Gx) + abs(Gy)` is closed-form integer arithmetic
and the standard hardware-friendly Sobel approximation.

**Saturation policy: clamp to the field's full range, same shape as the
already-shipped `normalized_pixel` policy.** `pixel_normalizer.cpp`
(10.1, real and tested) already establishes the pattern: signed internal
intermediate, clamp to `[0, 255]`, unsigned saturated result on the
interface. `gradient_magnitude` (12 bits, unsigned per §9's signedness
freeze) gets the identical treatment: clamp to `[0, 4095]`. With the
standard Sobel kernel (`[-1,0,1;-2,0,2;-1,0,1]` and its transpose) on
8-bit pixels, `abs(Gx)+abs(Gy)` is bounded by 2040 — the clamp is a
safety net that's specified explicitly rather than an implicit "can't
happen," matching why the normalizer's clamp exists at all (it also
can't structurally overflow with the frozen `NORMALIZER_SCALE_NUM`/
`_DEN`, and is clamped anyway).

**Threshold comparison operator: `>=`.** Not actually an open decision —
`threshold_rtl.v` (10.1, real, shipped, and explicitly documented as
"reused unmodified when later slices assemble the fuller pipeline") already
implements `(s1_pixel >= THRESHOLD[WIDTH-1:0])`. This item is retroactively
confirmed by existing code, not decided fresh.

**Tile traversal order: row-major (raster) — left-to-right within a row,
top-to-bottom across rows.** This is the only order consistent with
`forge.pixel_stream.v1`'s own `end_of_line`/`end_of_frame` flags (frozen,
§9) — there is no other traversal those flags could describe — and it
matches the tile-ID formula's own row-major structure (row term is the
more-significant multiplicand), confirmed next.

**Tile-ID formula: confirmed as-is** —
`(y // tile_height) * 1024 + (x // tile_width)`. With `tile_height =
tile_width = 8` (frozen, §10) and every mandatory frame size in the
dataset strategy (8×8 through 32×32), the largest tile-row/tile-col index
is 3, giving a maximum `tile_id` of 3075 — nowhere near overflowing the
16-bit field. The `1024` constant reserves headroom to 1023 tile-columns
and 63 tile-rows before any overflow, comfortably covering the mandatory
scope with no replacement needed.

**Border-handling policy (`window_builder_rtl`, §6.3): zero-padding** —
out-of-frame taps in the 3×3 window read as 0. `window_builder_rtl` is
already frozen (§6.3) to "emit one 3×3 window per accepted output pixel,"
i.e. a 1:1 input:output pixel correspondence, matching the normalizer and
threshold stages before it. A drop-border policy would break that 1:1
count and would also break `tile_stats_hls`'s fixed-64-sample-per-tile
assumption (next item) — note this is a distinct concern from the
dataset-preprocessing `tiling.border: drop` convention at spec §18.5,
which governs the project-owned image adapter cropping a variable-size
*source image* to a tileable shape before it ever becomes a pixel
stream, not this RTL module's per-window behavior on a stream already
inside the mandatory fixed-shape contract (§10). Zero-padding is also the
only one of the standard three policies (zero-pad / edge-replicate /
drop) that needs no extra state beyond the line buffers
`window_builder_rtl` already owns, and reproduces identically in the
Python golden model with a trivial `numpy.pad`.

**Mean/variance rounding policy: exact power-of-two shift, no rounding
mode.** `mean = sum_of_64_pixels >> 6` (64 = 2^6, so this is a plain,
exact right-shift — no remainder-handling logic needed in HLS, RTL, or
Python). `variance = max(0, (sum_of_squares >> 6) - mean**2)` — the
standard single-pass streaming formula, using the *same* shifted `mean`.
The two independent floor operations can, in rare rounding-boundary
cases, undershoot the true non-negative population variance by 1; the
explicit `max(0, ...)` clamp handles that case cheaply, and is simpler
than a two-pass sum-of-squared-deviations alternative that would need a
second walk over the tile. One formula, no separate parameter — must
match identically in `tile_stats_hls`, the Python golden model, and any
documentation.

**Population variance, not sample variance** (divide by N=64, not
N-1). Each tile is the entire population being described — there is no
larger population it's a sample of — so Bessel's correction doesn't
apply semantically, and it would also break the exact divide-by-64 shift
above, forcing a real division by 63.

**Packet record-kind values and packing order (256-bit beat, §6.1/§6.2):**
`record_kind` values: `0 = pixel-result`, `1 = tile-statistics`, `2`/`3`
reserved — a beat carrying a reserved `record_kind` is a diagnostic
failure, not silently ignored, matching this document's own "dedicated
diagnostic code per failure mode" pattern (§5, Decision A). Packing:
bits `[127:0]` carry whichever record the packetizer's arbiter accepts
first that beat (from either upstream path — pixel-result or
tile-statistics), bits `[255:128]` carry the second; either kind may
occupy either half and a beat may carry two records of the same kind or
one of each, since `record_kind` alone disambiguates — slot position
carries no kind meaning. If only one record is ready when a beat must
flush (e.g. an end-of-frame drain), it is packed into bits `[127:0]`
alone, with `keep[15:0] = 16'hFFFF` and `keep[31:16] = 16'h0000`. Within
each 128-bit record, the field order already frozen in §6.1's field
lists is MSB-first — the first-listed field (`record_kind`) occupies the
highest bits of its record. No prior convention exists elsewhere in the
framework to conflict with this, since no code implements these
interface types yet (confirmed: no hits for `packet_stream`,
`edge_mask_stream`, or `tile_statistics` anywhere under `forge/` or
`plugins/`).

**Configuration-update timing: applied at the next frame boundary, not
immediately.** Three independent pieces of evidence converge on this: (1)
the spec's own mandatory dataset tier names a fixture
`config_update_between_frames.xml` (§18/§19) — a name that only makes
sense if configuration changes are defined relative to frame boundaries;
(2) `cdc_mailbox` is specified as providing "atomic configuration
updates" (§20) — for a per-pixel streaming design, the only instant at
which changing `threshold`/`kernel_mode` is unambiguously atomic with
respect to in-flight computation is between frames, since mid-frame the
Sobel and threshold branches run at different pipeline depths (8 cycles
vs. 2, §6.2/spec §11) and would otherwise observe the new config at
different pixels; (3) it composes with the border/tile decisions above —
a mid-frame config change would need to be attributed to a specific pixel
inside a tile whose statistics are still accumulating, which has no clean
semantics. The `mailbox_transfer` CDC primitive (§5, Decision A) still
updates its destination-side register atomically on its own schedule;
the pipeline itself only samples that register at each frame's
`end_of_frame`/next-frame-start boundary, not every cycle.

## 10. Dataset strategy, including the fixed-shape mandatory contract

Tiers unchanged from v1 (synthetic exact fixtures → functional/CDC/reset/
latency; synthetic long stream → throughput/backpressure/occupancy; user
image folder → adapter demonstration; BSDS500 → optional; Fashion-MNIST →
optional hls4ml only).

**New: the fixed-shape runtime-stimulus constraint (readmemh is
explicitly fixed-shape only, v1 §2 item 8) is written into the mandatory
dataset contract, not left implicit**:

- Quickstart acceptance frames: normalized to **8×8**.
- Full-functional acceptance frames: normalized to **16×16 — frozen at
  Slice 10.2 implementation time** (see §15's former deferral, now
  resolved). 32×32 was the alternative; 16×16 wins because it's the
  smallest size that still exercises multiple full 8×8 tiles in both
  dimensions (2×2 tiles) — the minimum needed to prove `tile_stats_hls`
  (10.3) and the tile-ID formula's row/column term both actually vary,
  not just the column term a single-tile-row 8×16 shape would exercise —
  while keeping xsim run time down for CI. Slice 10.2 itself does not yet
  build this dataset: it continues exercising `window_builder_rtl`/
  `sobel_hls`/`edge_mask_merge_rtl` at the existing 8×8 quickstart scale
  (extending `vision_pipeline_quickstart_golden.xml`'s infrastructure,
  not replacing it), since proving the new pixel-result-path modules
  work is 10.2's job, not assembling the full-functional acceptance
  suite — that's 10.6/10.7's.
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
| 10.2 | **DONE.** `window_builder_rtl`, Sobel, threshold, alignment delay, exact-cycle merge (pixel-result path only, §6.2) |
| 10.3 | **DONE.** `tile_stats_hls`, `tile_boundary_rtl`, tagged bounded/elastic join (tile-statistics path, §6.2) |
| 10.4 | **DONE.** Multiple domains, all five CDC kinds, reset synchronizers, negative CDC fixture (§5 Decision A) |
| 10.5 | **DONE.** Packetizer (multiplexing both record kinds, §6.1/§6.2), async FIFO, throughput, backpressure, occupancy, invalid rate/depth fixtures |
| 10.6 | **DONE.** Synthetic, image-folder, and NumPy adapters (via `DatasetService`), manifests, staleness tests |
| 10.7A | **DONE.** Full-functional design assembly: single shared normalizer fan-out, 16x16/2x2-tile scale, multi-tile-column `tile_stats_hls`, `release_acceptance` reporting |
| 10.7B | **DONE.** Generic platform fixture: real 3-domain assembly (control/pixel/output), real status interface, real runtime-configurable threshold via mailbox_transfer (spec §26) |
| 10.7C | **DONE.** Provenance/plan-hash CLI-workflow tests for this plugin; visual explorer (`forge inspect --explorer/--dot`) applied to this plugin for the first time |
| 10.7D | **DONE.** Documentation (tutorial pages beyond 10.1 scope), clean-user workflow (README/`run_vision_pipeline_demo.sh` covering all real flows), full CI wiring |
| 10.8 | Optional hls4ml, only after the base acceptance gate passes |

**10.7 sub-slice split (decided, this session)**: slice 10.7 as originally scoped in this table's own prior single row ("platform wrapper, provenance, plan hashes, visual explorer incl. the 10.0D CLI wiring, documentation, clean-user workflow, full CI") bundled together a genuinely new capability (10.7B), two CLI-workflow-application gaps never yet exercised for this plugin (10.7C), and a full docs/CI pass (10.7D) — comparable in total size to any two prior slices combined, not one sitting's worth. A fresh audit at the start of this session (Explore agent survey + direct code reads) additionally found five items already deferred to "10.6/10.7" by earlier slice notes (§6.1/§9.1/10.5's own scope note) were all still genuinely open: the 16x16 full-functional dataset, multi-tile-per-frame concurrency, unifying `design_packetizer.yml`'s `norm_px`/`norm_tile` split into the spec's real single-shared-normalizer fan-out, and exercising a packet beat that genuinely carries two records at once. Per user decision, 10.7 is split into 10.7A-D mirroring the 10.0A-D precedent, with 10.7A (this session) covering exactly those five deferred items plus `release_acceptance` reporting — the "assemble everything already built into one real full-scale design" deliverable — leaving 10.7B/C/D explicitly open for later sessions.

**10.7A scope note**: unlike 10.0B/10.0C's "real, multi-day core-framework engineering" framing, this slice's own prerequisites turned out to already be real FORGE mechanisms, just never yet exercised: fan-out from one producer to multiple consumers (`design_packetizer.yml`'s own `norm_px` already fed two consumers), a per-instance Verilog `parameters:` override (`forge/topgen/config.py`'s `Module.parameters` → a real `#(...)` emission in `structural_verilog.py`, dead code until this slice), and a design-level `latency:` override replacing a registry entry's default for one instance only (`forge/topgen/config.py`'s ref-merge precedence, also dead code until this slice) — all three confirmed working end-to-end via `forge topgen validate`/`forge analyze latency-check` (0 mismatches) before any RTL/HLS was touched, not assumed from reading the source alone. This slice's own real engineering was `tile_stats_hls`'s concurrent-tile-column extension and a real FIFO-depth-insufficiency bug, both described below.

- **`design_full_functional.yml`** (new, standalone, mirrors 10.2-10.5's own "one design per slice" precedent — `design_packetizer.yml` is untouched, stays as slice 10.5's own completion-evidence artifact): one `norm` (`pixel_normalizer`) instance fanning out to `winbld`/`thresh`/`tstats`/`tbnd` (unifying the two-instance split), reusing every other 10.2/10.3/10.5 module unmodified. `winbld` (`window_builder_rtl`) is instantiated at a real `FRAME_WIDTH=16` (`parameters: {FRAME_WIDTH: 16}`, a genuine per-instance Verilog `#(...)` override) with a correspondingly recomputed `latency: {cycles: 18}` design-level override (`FRAME_WIDTH+2`, replacing the registry's FRAME_WIDTH=8-sized `cycles: 10`); `merge`'s threshold-branch `delay_cycles` is recomputed to 20 (thresh's own fixed 2 cycles + 20 = 22, matching the sobel branch's real 18+4=22) — both figures confirmed via a real `forge analyze latency-check` run (`merge_points_checked: 3, mismatches: 0`), not assumed from the formula alone.
- **`tile_stats_hls.{h,cpp}`** (concurrent-tile-column extension): a 16-wide frame with 8-wide tiles means tile column 0 (x 0-7) and tile column 1 (x 8-15) are both live within the same 8-row band (row-major traversal switches tile every 8 pixels, back to the other tile 8 pixels later) — a single accumulator register set would be stomped every 8 pixels. Fixed by two independent NAMED register-set copies (`col0_*`/`col1_*`, never a real `[2]` array — the same lesson the slice 10.5 follow-up's 4-way sum-of-squares split already established: a variable-indexed static array inside a PIPELINE-only function gives Vitis's dependence analysis nothing to disambiguate and it serializes regardless of ARRAY_PARTITION), selected by a plain mux on `x[3]` (`TILE_COL_BIT`, `TILE_WIDTH=8`) — muxes, not branches, so this doesn't reintroduce the control-flow obstacle the 10.5 follow-up already removed. Real, re-verified csynth result: **II=1, LATENCY=1, unchanged from the single-column version** (`3.107ns` at the shared `4.00ns` HLS target) — the concurrent-tile-column extension needed no further correction, confirmed by a real Vitis HLS synth run, not assumed identical.
- **Golden model rewrite** (`golden_model_provider.py`): `TileStatsProvider` generalized from "exactly one tile per dataset" (its own prior docstring's explicit scope limit) to grouping `dataset.events` by `tile_id` and echoing each tile's own finalized record at every event belonging to that tile — the same "one real value, echoed at every index" convention, just scoped per tile instead of per whole dataset (backward-compatible: `tile_stats_xsim`'s own single-tile dataset still gets identical behavior). `PacketizerProvider` now packs each event's own tile record instead of a hardcoded `events[0]` singleton; a caller wanting real hardware emission order (one record per tile, in true completion order) derives it directly from each event's own x/y via `tile_stats_hls.h`'s own frozen completion test — see `gen_stimulus_full_functional.py`, no new provider API needed.
- **16x16 dataset** (`vision_pipeline_full_functional_golden`): a real checkerboard frame built via the slice 10.6 `DatasetService`/`SyntheticPatternAdapter` path (`datasets/cli.py generate-synthetic --width 16 --height 16 --pattern checkerboard --seed 42`), producing a real 2x2 tile grid (256 events, `tile_id` genuinely varying across all 4 tiles per the frozen formula) — registered in `design.verification.yml` alongside the quickstart dataset.
- **A real FIFO-depth-insufficiency bug, found and fixed** (not assumed): the first xsim run of `full_functional_xsim` at `design_packetizer.yml`'s own depth=64 failed with a real content mismatch at pixel-result record 156 (expected tag `(x=12,y=9)`, observed `(x=14,y=9)` — two real records silently lost). Root cause, traced to `packetizer_rtl.v`'s toggle-in-payload novelty detection plus `cdc_async_fifo.v`'s free-running read side (`rd_bin_next = rd_bin + (!empty)`, at most one dequeue per `clk_output` cycle): this design's pixel-result path writes continuously at a real 200MHz (one record/cycle) while the packetizer can drain at most one new pixel-result record per 125MHz `clk_output` cycle — a genuine, sustained (not transient) write-vs-read rate mismatch over the full 256-pixel back-to-back burst, which a 64-deep FIFO (sized for 10.5's own 64-pixel quickstart-scale burst, real high-water 28/64) genuinely overflows. Fixed by raising `prpack->pktz`'s real `cdc: {depth: 64}` to **128** in `design_full_functional.yml` (and correcting the design's own `tier2_probes` occupancy/high_water widths from 7 to 8 bits, matching `cdc_async_fifo.v`'s real `$clog2(DEPTH)+1`-bit port width at the new depth) — re-run confirmed **zero drops, real measured high-water mark 100/128** (hex-encoded probe CSV values, not decimal — a real read-format gotcha caught by cross-checking against `forge.analyze.throughput_runtime.probe.build_runtime_throughput_result`'s own `int(v, 16)` parsing after an initial manual decimal read gave a misleadingly low 64).
- **`gen_stimulus_full_functional.py`** (new, mirrors `gen_stimulus_packetizer.py`'s dual-clock-domain fork/join drive+check strategy): drives all 256 pixels through the single `norm` fan-out on `ap_clk`, concurrently polls `clk_output` for `pktz_packet_valid`, decodes each beat's occupied slot(s) by `record_kind`, checks against golden-model expectations by content. Real conservation confirmed: 256 pixel-result + 4 tile-statistics = 260/260 checks pass, zero dropped/duplicated (`conservation` check: `total_received === 260`).
- **Two-record-per-beat, genuinely observed** (closes the honest deferral 10.5 left open): all 4 tile-statistics records landed in the beat's high slot, each sharing that exact beat with a pixel-result record in the low slot (the pixel-result FIFO's own real, sustained backlog above means a pixel-result record is essentially always ready whenever a tile-statistics toggle also fires) — decoded and checked correctly in both slots, real evidence, not forced or synthetic.
- **`release_acceptance` reporting**: `forge topgen validate --cdc-result-json` (2 real crossings, both approved) + `forge report` with `--module-width`/`--probe-csv`/`--fifo-probe`/`--cdc-result-json` produce real `throughput.md` (bottleneck `tile_stats_hls`, real per-FIFO runtime rows for both crossings) and `cdc_verification.md` (2/2 passing) sections against this real design — a new pytest test, `test_report_release_acceptance_for_full_functional_design` (`forge/tests/test_report_cli_group.py`), covers this end-to-end. `--golden-comparison-json` is deliberately **not** wired for this flow: the standard `forge test run --golden-comparison-json` path regenerates a generic single-event `stimulus_current.svh`, which is structurally incompatible with this design's own hand-authored dual-clock-domain fork/join checker (found by trying it — it overwrote the real stimulus file and produced a compile failure) — the same scope boundary `packetizer_xsim` already had (never wired to that path either, for the identical structural reason), not a new gap this slice introduces. A standalone `render_throughput_result_full_functional.py` (mirroring slice 10.5's own `render_throughput_result.py`) produces the real, exactly-known-count `forge.throughput_result.v1` artifact (256/4 accepted+emitted, real 200MHz-clock-based predicted rate) the same way `packetizer_xsim`'s own artifact was produced — `forge report`'s own generic `--fifo-probe`/`--module-width` CLI path uses each module's HLS-estimated fmax and the generic accepted/emitted approximation instead (a pre-existing, framework-level limitation, not new to this slice).

**10.7A completion evidence**: `design_full_functional.yml` (real design, 11 instances, 94 connections, 2 real CDC crossings), `tile_stats_hls.{h,cpp}` (real concurrent-tile-column extension, real Vitis HLS synthesis: II=1, LATENCY=1 unchanged), and the `full_functional_xsim` verify flow — a real Vivado xsim run streaming the full 256-pixel 16x16 checkerboard frame through the single-fan-out normalizer and both output paths into the real depth-128 packetizer/async-FIFO path: **260/260 checks pass** (256 pixel-result + 4 tile-statistics records, every field, content-matched from whichever beat/slot each record actually landed in), conservation invariant confirmed (`total_received === 260`), real probe-confirmed zero overflow at a measured 100/128 high-water mark, and 4 real two-record beats observed and correctly decoded. `forge analyze latency-check` against the real design confirms zero mismatches. `forge report`'s release-acceptance bundle (throughput + CDC sections) renders real content against this design, covered by a new pytest test. The six pre-existing xsim flows (`quickstart_pipeline_xsim`/`pixel_result_xsim`/`tile_stats_xsim`/`cdc_xsim`/`packetizer_xsim`/`invalid_fifo_depth_xsim`) were re-run unchanged after the shared `tile_stats_hls.cpp`/`modules.yml`/`golden_model_provider.py`/`design.verification.yml` edits and still pass (`invalid_fifo_depth_xsim` still correctly FAILs, being a negative fixture) — the full `forge/tests` suite (1249 passed, 8 skipped, 0 failed — one more pass than 10.6's own 1248, matching exactly the one new report test added) and `plugins/vision_pipeline_demo/forge/verify/tools/tests` (27 passed) both confirm no regressions.

**10.7B scope note**: spec §26's "generic platform fixture" (control/pixel/output clock+reset, real input/output stream, status interface, "generate and compile the supported `payload.v` or equivalent wrapper without detector-specific terminology") turned out to need a real architectural decision, not a relabeling exercise — a fresh code audit (before writing any RTL) found `forge framework emit-payload` (the CLI group producing a literal `payload.v`, `forge/framework/payload_generator.py`) genuinely does not apply here: its generator hardcodes GT-transceiver/CSP-framing/single-clock semantics into its own control flow (dispatch is gated on literal ABI `kind` tokens like `gt_rx_tdata`/`gt_algo_clk`, every TX endpoint forced through a 67-bit CSP bit-pack, the algo instantiation always binds a single `logic_clk`) — not just its fixture content, confirmed by reading the generator itself, not assumed from its name. A genuinely detector-neutral ABI would either have to literally contain a Blobfish token to get any real wiring at all, or produce a non-functional stub. `forge topgen gen-top` — the same mechanism already used for every other design in this reference project — is the real, already-generic wrapper-generation path; per user decision, this became the real 3-domain platform design below rather than reaching for the Blobfish-specific machinery.

- **`design_platform_wrapper.yml`** (new, standalone): a genuine 3-domain assembly, not 10.7A's own 2-domain design with one clock doing double duty. `control` (`clk_control`, 50MHz) hosts a real configuration source; `pixel` (`ap_clk`, 200MHz, this design's primary domain) hosts the real 9-module pipeline reused unmodified from 10.2/10.3/10.5/10.7A (`winbld`/`sobel`/`merge`/`prpack`/`tstats`/`tbnd`/`tjoin`/`tspack`) plus the new `threshcfg`; `output` (`clk_output`, 125MHz) hosts `pktz`, reused unmodified. Real status interface: `merge.tag_mismatch`/`tjoin.join_mismatch`/`tjoin.out_end_of_frame` — three diagnostic/completion signals every prior design left dangling internally ("OUTPUTS LEFT OPEN" in every earlier `forge topgen gen-top` report) — exposed here as real top-level ports via a plain `external_out_ports:` declaration, no new RTL needed.
- **`threshold_configurable_rtl.v`** (new RTL, pixel domain): a real runtime-configurable sibling to `threshold_rtl.v` (which stays untouched, "reused unmodified" everywhere else) — takes its comparison threshold from a real `cdc: {kind: mailbox_transfer}` crossing instead of a compile-time Verilog parameter, latched into an `active_threshold` register only on each frame's own first sample (`in_valid && x==0 && y==0`), realizing preflight.md §9.1's frozen "applied at the next frame boundary, not immediately" decision for the first time anywhere in this repo (the existing `design_cdc.yml`/`pixel_sink_rtl.v` mailbox path only ever exposed the crossed value as a read-back status register, never fed it to a real downstream computation).
- **`ctrl_mailbox_platform_rtl.v`** (new RTL, control domain): the same real packing logic as `ctrl_mailbox_rtl.v` (slice 10.4), necessarily a separate module — Verilog port names can't be parametrized, and this design makes `pixel` (not `control`) its primary `ap_clk` domain, unlike `design_cdc.yml`, so control needs its own distinctly-named `clk_control`/`rst_control` pair while every reused pixel-domain module keeps its own already-shipped `ap_clk`/`ap_rst` naming untouched.
- **A real, previously-undiscovered framework bug, found and fixed**: `forge analyze latency-check` initially misreported two false mismatches assembling this design (`ctrl_mailbox → threshcfg` and `sobel`/`threshcfg → merge`). Root cause, confirmed by reading `forge/analyze/latency_static/{graph.py,checker.py}` directly: a `mailbox_transfer`/`async_fifo` edge's `LatencyEdge.latency` is `None` by design (§4.1's own "deliberately not folded in" honest-unknown treatment) — but `None` was indistinguishable from "this edge simply adds zero known cycles," so `check_merge_points`/`_upstream_chain_latency` silently treated a genuinely-unbounded CDC crossing as free, then (once threshcfg's own upstream chain was stopped short at its own multi-predecessor merge point, per existing, correct-in-isolation policy) lost track of real upstream latency (`norm`'s own 3 cycles) that a sibling straight-line branch (`sobel`) legitimately included, producing a fabricated 3-cycle "mismatch" and a `signal_delay` suggestion that would have *broken* real, already-correct hardware (hand-verified: both branches genuinely land at `norm`+3+... = 17 cycles from a shared origin). Invisible until this design — the first anywhere in this repo to feed a mailbox_transfer/async_fifo crossing into a node that itself has a real fixed-latency-declared sibling predecessor feeding a further downstream merge point. Fixed with a new `LatencyEdge.unknown_cdc` flag, propagated through both functions, real-verified against a hand-derived physically-correct calculation (not just "the tool now says pass") — `forge analyze latency-check` now correctly reports 0 mismatches, `merge_points_checked: 4`. Two new regression tests in `forge/tests/test_latency_checker.py` (16 total in that file, up from 14) lock in both the "don't fabricate zero cycles for an unknown CDC edge" and "still fold through the one real predecessor of a mixed real/async-side-channel node" behaviors. Confirmed via `git stash` comparison that this fix does not regress any other existing design's own latency-check result; `design_cdc.yml`'s own `pxsink` merge point does show a real, pre-existing mismatch (its three CDC-fed status inputs — level/pulse/mailbox — were never actually exact-cycle-aligned, unrelated to this fix, present identically before and after it, and arguably shouldn't be classified `exact_cycle` at all since they're three independent status latches, not a computed reconvergence) — noted honestly here as a real, out-of-scope-for-this-slice finding, not touched.
- **A real golden-model bug, found and fixed**: `TileStatsProvider` (generalized in 10.7A to group dataset events by `tile_id` for multi-tile-per-frame datasets) silently conflated two *different frames'* own samples whenever they reused the same `tile_id` — true for this design's own 2-frame, single-8x8-tile-per-frame dataset, where `tile_id` is `0` for both frames regardless of `frame_id` (10.7A's own 16x16/4-tile dataset never exposed this, since every tile there had a genuinely unique `tile_id` within its one frame). Found via a real xsim content mismatch (`mean`/`variance` off, `minimum`/`maximum` coincidentally still correct since both frames' true extrema happened to still bound the incorrectly-merged 128-sample set). Fixed by grouping on `(frame_id, tile_id)` instead of `tile_id` alone — backward-compatible with every earlier single-frame dataset's own behavior (`tile_stats_xsim`, `full_functional_xsim`).
- **A real stimulus-timing bug, found and fixed**: the first working version of `gen_stimulus_platform_wrapper.py` coordinated its three concurrent fork processes (control/pixel/output, three genuinely different clocks) using real-time `#delay` waits before the first `@(posedge ap_clk)`-gated pixel drive — a pattern no earlier `gen_stimulus_*.py` script in this plugin needed, since none had a third, real-time-only-paced process before. This produced a real, reproducible bug: pixel `(0,0)`'s own record never reached the output at all, every downstream tag arriving one pixel "ahead" — confirmed via a hierarchical debug `$display` trace (`dut.threshcfg.*`) showing `frame_start`'s own `x==0,y==0` sample was silently skipped, consistent with a same-time-step ordering ambiguity between a real-time delay's wakeup and a concurrent `@(posedge ap_clk)` in a separate process. Fixed by converting every cross-process wait to a plain per-clock cycle count (`repeat(N) @(posedge <that process's own clock>)`), the same idiom every other `gen_stimulus_*.py` script already uses — real elapsed time between domains is still reconciled, just via cycle-count conversion up front rather than mixed timing-control constructs at runtime.

**10.7B completion evidence**: `design_platform_wrapper.yml` (real design, 12 instances, 3 real clock/reset domains), `threshold_configurable_rtl.v`/`ctrl_mailbox_platform_rtl.v` (new RTL, real Verilog), and the `platform_wrapper_xsim` verify flow — a real Vivado xsim run streaming two real 8x8 frames (ramp then checkerboard, genuinely different per-frame thresholds T0=64/T1=160, neither equal to the compile-time-default 96) through the real mailbox-configured pipeline: **130/130 checks pass** (128 pixel-result + 2 tile-statistics records), conservation invariant confirmed (`total_received === 130`), and both real status-port checks pass (`merge_tag_mismatch`/`tjoin_join_mismatch` read `0` throughout) — the content match itself is the proof that a real mailbox-written threshold change reaches the pipeline's real output at the correct frame boundary, not a separate "did it compile" check. `forge analyze latency-check` against the real design confirms zero mismatches (after the framework fix above). All 7 pre-existing xsim flows were re-run after the shared `checker.py`/`graph.py`/`golden_model_provider.py` edits and still pass (`invalid_fifo_depth_xsim` still correctly FAILs, being a negative fixture) — the full `forge/tests` suite (1251 passed, 8 skipped, 0 failed — two more passes than 10.7A's own 1249, matching exactly the two new latency-checker regression tests added) and `plugins/vision_pipeline_demo/forge/verify/tools/tests` (27 passed) both confirm no regressions.

**10.7C scope note**: both halves of this slice's scope (§11's own row: "provenance/plan-hash CLI-workflow tests for this plugin" + "visual explorer applied to this plugin for the first time") turned out to need zero core-framework engineering — `forge build`'s plan-hash machinery, `forge inspect --provenance/--explain-staleness`, and `--dot/--explorer` are all real, already-shipped mechanisms (release-plan Phase 6/8/10), proven in general by `forge/tests/test_build_cli_group.py`/`test_inspect_cli_group.py` against `passthrough_demo`/`trigger_demo`. What was genuinely missing was this plugin's own coverage: no test file anywhere had ever driven these CLI workflows against `vision_pipeline_demo`'s own designs, so `forge build`/`forge inspect --dot/--explorer` had literally never been run against this plugin's richest topologies before this session. A new file, `plugins/vision_pipeline_demo/forge/verify/tools/tests/test_cli_workflows.py`, drives the real argparse entry point in-process (mirroring `test_build_cli_group.py`'s own pattern) against `design_full_functional.yml` (10.7A's 11-instance, 2-domain, two-real-async_fifo design) and `design_platform_wrapper.yml` (10.7B's 12-instance, 3-domain, real `mailbox_transfer` + two `async_fifo` design) — the two designs that most recently proved this plugin's real multi-CDC, multi-domain scope, and had accumulated the most untested CLI surface area.

One real, previously-unverified fact surfaced while building these tests (found by trying it, not assumed): every `kind: full_chip_rtl` flow in this plugin's `design.verification.yml` (`quickstart_pipeline_xsim` through `platform_wrapper_xsim`) declares `top_module: algo_top` — the generated top-level wrapper's own name, not a real module ref — so `forge.analyze.design_explorer.verification_join.join_flow_entry_points` (the 10.0D overlay this slice exercises against this plugin for the first time) honestly returns an empty join for every one of them, exactly per that module's own documented behavior for an unresolvable entry point, not a bug. `pixel_normalizer_csim` (`kind: hls_csim`, `top_module: pixel_normalizer`) is this plugin's one flow whose declared entry point genuinely resolves — to `norm`'s own `ref: pixel_normalizer` — real, non-synthetic evidence that the verification-flow-entry-point overlay's join logic works correctly against a design this plugin actually ships, confirmed via `design_platform_wrapper.yml` (`test_inspect_explorer_for_platform_wrapper_design_joins_real_verification_flow`) with an explicit regression guard proving the overlay stays empty without `--verify-design`/`--results-json` (`test_inspect_explorer_overlays_absent_without_verify_design_for_platform_wrapper_design`).

The `--dot` assertions are pinned to real, hand-verified rendered content rather than exit-code-only checks: `winbld`'s design-level `latency: {cycles: 18}` override (10.7A's own previously-unexercised per-instance override, §11's 10.7A scope note) renders as `latency: 18c` on its real node label; both real `async_fifo` crossings into `pktz` (`prpack`/`tspack`) render `async_fifo CDC reset-domain-crossing` on their edges (the reset-domain-crossing label was not anticipated going in — pixel and output are also independent reset domains here, confirmed by running the real command rather than assumed from the CDC kind alone); and `design_platform_wrapper.yml`'s real `mailbox_transfer` crossing (`ctrl_mailbox -> threshcfg`, control -> pixel) renders `mailbox_transfer CDC` with `ctrl_mailbox`'s own `clk: clk_control`/`latency: 1c` and `pktz`'s own `clk: clk_output`/`latency: 3c` all present verbatim. Every assertion in this file was checked against a real, just-run `forge build`/`forge inspect` invocation before being written into the test, not derived from reading the renderer's source alone (the same "verified, not assumed" discipline this document has followed since 10.2's own window-builder-latency correction).

**10.7C completion evidence**: `plugins/vision_pipeline_demo/forge/verify/tools/tests/test_cli_workflows.py` — 8 new tests, all passing: plan-hash determinism and `--accept-plan-hash` match/mismatch (`forge build`, `design_full_functional.yml`/`design_platform_wrapper.yml`), the plan-only-never-writes invariant re-checked against a real multi-CDC design, a real `--provenance`/`--explain-staleness` fresh-then-stale round trip (`forge inspect`), two `--dot` tests carrying the real latency/CDC content described above, and two `--explorer` tests (overlay-absent regression guard + the real `pixel_normalizer_csim` verification-flow join). `plugins/vision_pipeline_demo/forge/verify/tools/tests` as a whole: **35 passed** (27 pre-existing + 8 new, zero regressions). `plugins/trigger_demo/forge/verify/tools/tests` (110 passed) and the full `forge/tests` suite (**1251 passed, 8 skipped, 0 failed** — identical to 10.7B's own count, confirming this slice's plugin-local-only test file caused zero drift in the core framework suite) both re-run clean.

**10.7D scope note**: like 10.7C, this slice needed zero core-framework engineering — every command `run_vision_pipeline_demo.sh` now runs (`forge topgen validate`/`gen-top`, `forge hls run`, `forge verify generate`/`doctor`/`run`) was already real, already-shipped machinery. The genuine gap was that no single artifact ever exercised all 8 real designs / 9 flows this plugin had accumulated by 10.7C — `run_vision_pipeline_demo.sh` itself was still frozen at its original slice-10.1 quickstart-only scope (2 designs, 2 flows), and `docs/tutorials/` had no page describing anything past 10.1. Rewriting the script surfaced one real, previously-undocumented behavioral fact about the `invalid_direct_bus_cdc.yml` negative fixture (spec §8.5): that fixture's own header claims "`forge topgen validate` reports both ATG023 and ATG024 as warnings, and `forge topgen gen-top --strict` hard-fails" (10.4's completion evidence, worded ambiguously) — confirmed by actually running both commands that plain `forge topgen validate` (non-strict) only *warns* on the undeclared crossing and exits **0**; only `gen-top --strict` genuinely hard-fails (exit 1, no RTL written). The script's first draft used plain `validate` and was itself silently wrong as a negative-fixture check (it "passed" a design that should have been rejected) until caught by actually running it end to end, not by re-reading the header comment alone — fixed by switching the check to `gen-top --strict`, matching the already-correct wording in `invalid_direct_bus_cdc.yml`'s own file header.

- **`run_vision_pipeline_demo.sh`** (rewritten): now drives all 8 real designs (quickstart, pixel-result, tile-statistics, packetizer, full-functional, platform-wrapper, CDC, plus the `invalid_fifo_depth_packetizer.yml` negative fixture) and all 9 real flows through the same 8-stage sequence documented in the script's own header — clean → validate (registry + every real design) → HLS build (csim for `pixel_normalizer` only, its one C-sim-tested module; synth for all 3 HLS modules) → the `invalid_direct_bus_cdc.yml` negative fixture (`gen-top --strict`, expected to fail) → gen-top (8 designs) → verify generate → `gen_stimulus_*.py` per flow → doctor → verify run (9 flows). Both negative fixtures are checked for their *expected* failure rather than treated as regressions (`EXPECTED_FAIL_FLOWS` array for `invalid_fifo_depth_xsim`; a dedicated `die`-on-unexpected-pass check for `invalid_direct_bus_cdc.yml`) — a real design decision, not an oversight: a script that merely reported "9/9 passed" without checking *which* 9 would have silently regressed the moment a negative fixture started passing for the wrong reason.
- **Documentation**: `docs/tutorials/vision-pipeline-full-design.md` (new) — a runnable walkthrough of all 8 designs/9 flows beyond the quickstart tier, including a full manual walkthrough of `design_cdc.yml` (the smallest genuinely multi-domain design) and both negative fixtures, wired into `mkdocs.yml`'s nav. `docs/tutorials/vision-pipeline-quickstart.md` and `plugins/vision_pipeline_demo/README.md` updated to stop claiming `run_vision_pipeline_demo.sh` only covers the quickstart tier's own 6 steps. `docs/explanation/project-scope.md` updated — it previously stated "only the first slice is built and CI-real today," which as of this slice is stale; corrected to reflect the full design being built and CI-real, with only the optional hls4ml extension (slice 10.8) still open. One real link-hygiene catch (`mkdocs build --strict`, which this branch already treats as a hard gate, `forge:docs-build`): a markdown link from the new tutorial page into `docs/internal/phase10/preflight.md` would have been a dead link in the published site, since `mkdocs.yml`'s own `exclude_docs:` physically excludes `internal/**` — fixed by using the same plain-backtick-path convention (not a clickable link) every other tutorial page already uses for internal-only references.
- **CI wiring**: `ci/framework-release.yml` gets a second release-gate job, `forge:vision-pipeline-release-gate` (vivado runner, same rule shape as `forge:framework-release-gate`, runs `run_vision_pipeline_demo.sh --jobs=4`). `.gitlab-ci.yml`'s `forge:schema-validate` now validates all 7 of this plugin's non-negative-fixture designs plus its registry; `forge:python-unit-tests` now runs `plugins/vision_pipeline_demo/forge/verify/tools/tests` (35 tests) alongside `trigger_demo`/`passthrough_demo`'s own suites; `forge:verify-smoke` now dry-run-generates the two quickstart-tier flows and doctors this plugin's `design.verification.yml`, matching spec §29's Fast MR CI "quickstart generation" bullet. `ci/stale_reference_check.sh`'s `SCAN_PATHS` now includes `run_vision_pipeline_demo.sh` (previously only `run_trigger_demo.sh` was scanned by name — `plugins`/`docs`/`ci` were already scanned as directories, so this only closes the repo-root script's own gap). All new/changed CI YAML re-verified to parse (a `!reference`-aware PyYAML loader, since plain `yaml.safe_load` doesn't know GitLab's own tag) — `yamllint`/GitLab's own CI linter weren't available in this environment to double-check further.

**10.7D completion evidence**: a full, real, from-scratch `./run_vision_pipeline_demo.sh --jobs=4` run (real Vitis HLS 2024.1 + Vivado xsim 2024.1 on `PATH`) — all 7 positive flows PASSED (`pixel_normalizer_csim`, `quickstart_pipeline_xsim`, `pixel_result_xsim`, `tile_stats_xsim`, `packetizer_xsim`, `full_functional_xsim`, `platform_wrapper_xsim`), both negative fixtures behaved exactly as designed (`invalid_direct_bus_cdc.yml` correctly rejected by `gen-top --strict` with ATG023/ATG024; `invalid_fifo_depth_xsim` correctly FAILed its own scoreboard check), script exit code 0 ("All 9 flows behaved as expected (1 negative fixture correctly failed)"). `mkdocs build --strict` passes clean against the new/edited docs pages and nav. The full `forge/tests` suite re-run clean: **1250 passed, 9 skipped, 0 failed**. `plugins/vision_pipeline_demo/forge/verify/tools/tests` (35 passed), `plugins/trigger_demo/forge/verify/tools/tests` (110 passed), and `plugins/passthrough_demo/forge/verify/tests` (one pre-existing, unrelated failure — `test_single_flow_declared` — predates this branch's Phase 10 work entirely, confirmed again this slice, still untouched) all re-run as expected — zero regressions.

**10.2 scope note**: the spec's "alignment delay" (threshold branch, +6
cycles, spec §11) and "exact-cycle merge" (`edge_mask_merge_rtl`, spec
§11) are not new framework capabilities to build — both are already
real, tested FORGE machinery, confirmed by reading `forge/ir/model.py`
and `forge/analyze/latency_static/checker.py` directly rather than
assumed: a plain `Connection.delay_cycles: N` (no `boundary` tag)
resolves to the existing `latency_delay` transformation kind, generating
a real `signal_delay` register chain; and `check_merge_points` already
classifies a merge point `exact_cycle` whenever every predecessor
declares fixed/hint/hls_report latency, checking their cycle counts for
exact overlap (`delta == 0`) with no per-example-project code involved.
10.2 is therefore project-level work only — three new
`vision_pipeline_demo` modules and their design-level wiring — not
core-framework engineering, unlike 10.0B/10.0C. **One correction found
during implementation**: `check_merge_points` turned out to only sum a
merge point's *immediate* predecessor's own latency, never walking back
further through a straight producer chain — invisible until this slice
wired a genuine 2-hop branch (`window_builder_rtl -> sobel_hls`) into a
real merge point for the first time (every existing merge point in both
reference designs is single-hop). Fixed in
`forge/analyze/latency_static/checker.py` (`_upstream_chain_latency`),
with three new tests including a real-design regression proof against
this exact design — see that commit. So 10.2 *did* end up touching core
framework code, just not the part anticipated above; the anticipated
parts (`Connection.delay_cycles`, `exact_cycle` classification itself)
needed no changes.

The spec's own "+6" figure assumed its *original* single monolithic
`sobel_hls` (spec §11, latency 8, predating this document's §6.3
correction). Once that's split into `window_builder_rtl` + `sobel_hls`,
the real total differs: `window_builder_rtl`'s own latency is
`FRAME_WIDTH+2` (10 for this slice's FRAME_WIDTH=8 — not `FRAME_WIDTH+1`,
an initial hand-derivation that a standalone xsim check against an
independent numpy zero-pad model caught and corrected; see
`algo/rtl/window_builder_rtl.v`'s header), plus `sobel_hls`'s own 4-cycle
compute latency, for a sobel-branch total of 14. The threshold branch's
`delay_cycles` is therefore set to 12 (2 + 12 = 14), not the spec's
original 6 — computed from the actual built modules' real declared
latencies, not copied from the spec's now-superseded module split.

**10.2 completion evidence**: `design_pixel_result.yml` (real design),
`window_builder_rtl`/`sobel_hls`/`edge_mask_merge_rtl` (real RTL/HLS,
real Vitis HLS synthesis for `sobel_hls`), `PixelResultProvider` (real
golden model, reconstructing each frame's zero-padded window from the
dataset itself), and the `pixel_result_xsim` verify flow — a real Vivado
xsim run streaming the full 64-pixel 8×8 quickstart-scale frame through
every module, checked against the golden model: 448/448 checks pass
(64 pixels × normalized_pixel/gradient_magnitude/threshold_mask/out_x/
out_y/out_valid/tag_mismatch), including all 64 `tag_mismatch` checks
confirming the Sobel and threshold branches land on
`edge_mask_merge_rtl` on the exact same cycle for every pixel in the
frame, not just in the static `delta==0` check.

**10.3 scope note**: builds the tile-statistics path (§6.2) as a
standalone design, `design_tile_stats.yml` — its own `norm` instance
fanning out to `tile_stats_hls` and `tile_boundary_rtl`, both feeding
`tile_summary_join_rtl` — mirroring `design_pixel_result.yml`'s own
precedent of demonstrating one slice's capability set in isolation
rather than extending an already-passing design. Three new modules:

- `tile_stats_hls` (HLS): a real per-tile streaming statistics
  accumulator (running min/max/sum/sum-of-squares over a static-variable
  accumulator, the standard Vitis HLS idiom for persistent state under
  `PIPELINE`), emitting the frozen population mean/variance formula
  (§9.1) on the tile's last accepted sample. **One correction found
  during implementation**: the spec's own text (§11/§15.2) frames this
  module as "bounded 6..10 cycles" — first attempted here as a real
  `#pragma HLS LATENCY min=6 max=10` constraint, on the theory that the
  tile-final sample's extra mean/variance compute (shift, square,
  subtract, clamp) would schedule to a genuinely different depth than a
  plain accumulate-only sample. The real csynth report came back
  `min==max==6`: a plain `PIPELINE`-scheduled function with no
  memory-latency-bound stall folds every control path into one static
  schedule with muxes, not a real per-invocation range — so this is
  `kind: fixed, cycles: 6` in modules.yml, not `bounded`, matching the
  real synthesis report rather than the spec's uncorrected placeholder
  (the same "verified, not assumed" correction 10.2's own
  window_builder_rtl/threshold_rtl figures went through). Also
  genuinely `II=2` (a real scheduling consequence of the static
  accumulator's read-modify-write dependency, not a chosen throughput
  target) — the stimulus drives pixels 2 cycles apart to respect it
  (`gen_stimulus_tile_stats.py`), unlike the pixel-result path's
  back-to-back streaming.
- `tile_boundary_rtl` (RTL): the "tile-end summary" — an independent
  re-derivation (not a tap off `tile_stats_hls`'s own output) of the
  tile-last event from the same fan-out tag stream, verifying at runtime
  that the accumulator and the geometry actually agree (the same role
  `edge_mask_merge_rtl`'s `tag_mismatch` established in 10.2). Holds its
  tag for a declared bounded `[1,17]`-cycle window rather than using an
  ack handshake back to the join — a real cyclic module-to-module
  connection has no precedent anywhere in this repo's design-graph
  tooling, and a self-timed bounded hold gets the same correctness
  property (provably still valid when `tile_stats_hls`'s own latency
  arrives) without needing one.
- `tile_summary_join_rtl` (RTL): the "tagged elastic join" — matches the
  two branches by **tile_id/frame_id**, not cycle position (spec §15.3:
  "must not be reported as fixed"), with `join_mismatch` as the real
  runtime diagnostic. `forge.analyze.latency_static.check_merge_points`
  classifies this merge point `bounded_skew` (at least one predecessor —
  `tile_boundary_rtl` — declares `kind: bounded`) and confirms the two
  declared windows genuinely overlap (`[1,17]` covers the real,
  chain-folded `9` cycles `tile_stats_hls`'s path accumulates) with zero
  mismatches — no framework changes were needed to get this real
  bounded-latency-declaring module wired up (unlike 10.2's
  `_upstream_chain_latency` fix), though this is also the first time
  any interface in this repo has actually declared `kind: bounded` or
  `kind: elastic` in a real module registration, not just a unit test.

Multi-tile-per-frame concurrency (needed once `FRAME_WIDTH >
TILE_WIDTH`, interleaving more than one tile's samples within a shared
tile-row band) is explicit future work, deferred alongside the 16×16
full-functional dataset itself (10.6/10.7) — the same scope boundary
10.2's own scope note already drew for `window_builder_rtl`/`sobel_hls`
at the current 8×8 quickstart scale.

**10.3 completion evidence**: `design_tile_stats.yml` (real design),
`tile_stats_hls`/`tile_boundary_rtl`/`tile_summary_join_rtl` (real
RTL/HLS, real Vitis HLS synthesis for `tile_stats_hls`),
`TileStatsProvider` (real golden model, computing population mean/
variance over the normalized pixel stream with the identical
power-of-two-shift arithmetic `tile_stats_hls.cpp` implements), and the
`tile_stats_xsim` verify flow — a real Vivado xsim run streaming the
full 64-pixel 8×8 quickstart-scale frame (pixels spaced 2 cycles apart
for `tile_stats_hls`'s real `II=2`) through every module, checked
against the golden model: 8/8 checks pass (minimum/maximum/mean/
variance/tile_id/frame_id/out_valid/join_mismatch), including
`join_mismatch == 0`, confirming `tile_boundary_rtl`'s independently
re-derived tag and `tile_stats_hls`'s own accumulator agree on both
`tile_id` and `frame_id` for the real tile. `forge analyze
latency-check` against the real design confirms zero mismatches at the
`bounded_skew`-classified merge point. The pre-existing
`pixel_result_xsim`/`quickstart_pipeline_xsim` flows were re-run
unchanged and still pass, confirming no regression from the shared
`modules.yml`/`golden_model_provider.py`/`design.verification.yml`
edits this slice made.

**10.3 follow-up correction (release-plan Phase 10, slice 10.5
follow-up)**: `tile_stats_hls`'s II=2 was real and correctly derived at
the time — a single loop-carried sum-of-squares accumulator genuinely
bound to a 3-cycle-latency DSP48 MACC — but it was not, in fact, a hard
limit of the algorithm itself. Requested directly by the user after
reviewing this document ("can't the module be II=1?"), two real,
independent obstacles were found and removed together (confirmed
empirically at each step, not assumed): (1) splitting the single
accumulator into 4 named-scalar round-robin partial sums (enough slack
between each partial's own successive updates to hide the DSP's
latency) plus forcing the accumulate-add itself off the DSP48 (Vitis
fuses a trailing add into the same MACC by default, which silently
re-imposes the same bottleneck the split was meant to remove — found by
testing the split alone first and seeing II=2 persist unchanged); (2)
even with (1) fixed, II stayed at 2 until the function's own `if`/`else`
control flow was removed entirely in favor of branch-free, pure-select
dataflow — the real second obstacle turned out to be Vitis's
branch-driven basic-block splitting, not any remaining arithmetic (every
operation involved was already confirmed 0-latency combinational logic
by the time this was found). The real, csynth-confirmed result: fixed
1-cycle latency, real II=1, at both the registry's shared 4.0ns HLS
target and the real 200MHz/5.0ns clock `design_packetizer.yml` actually
runs this module at. See `algo/tile_stats/tile_stats_hls.cpp`'s own
header for the full derivation and `modules.yml`'s `tile_stats_hls`
entry for the corrected latency declaration. Cascaded through:
`gen_stimulus_tile_stats.py`/`gen_stimulus_packetizer.py` (both paths
now drive back-to-back, no more II=2-spaced pacing), `design_tile_stats
.yml`/`design_packetizer.yml`'s own header comments, and a full
regression re-run — `tile_stats_xsim` (8/8 checks, unchanged pass) and
`packetizer_xsim` (66/66 checks, unchanged pass) both still pass, real
`forge/tests/` suite still green (181 passed). One further consequence:
`render_throughput_result.py`'s real bottleneck for
`design_packetizer.yml` changed from `tile_stats_hls` to
`pixel_normalizer` (arbitrary tie-break — all three profiled modules
now sit at an identical real 200 Mrecord/s, II=1, since nothing in this
plugin's own module catalogue is II>1 anymore), and the §17.4 negative
fixture's real II=2 example no longer exists in this codebase — see the
10.5 section below for how that fixture was revised.

**10.4 scope note**: unlike every prior slice, 10.4's own core-framework
prerequisite (Decision A originally framed CDC work as "real, multi-day
core-framework engineering") turned out to be *mostly already done*:
slice 10.0B (framework history predating this session) already gave all
five CDC kinds real RTL-generating primitives
(`cdc_sync2ff`/`cdc_pulse_sync`/`cdc_mailbox`/`cdc_async_fifo`/
`cdc_reset_sync`), unit-tested in isolation. What 10.4 actually needed,
and what genuinely was still missing, only surfaced by trying to wire a
real multi-domain design end-to-end for the first time:

- **Real multi-clock xsim support** — `forge.verify.gen_sim`'s
  testbench generator only ever drove one clock (`ap_clk`) into the
  DUT. Added `SimulationDefaults`/`FlowDeclaration.extra_clocks`
  (`{net_name: period_ns}`) and `.extra_resets`, rendered as independent
  free-running clock generators; `StimulusEmitter.tick(clock=...)` for
  cross-domain-paced stimulus; and a per-flow `clk_period_ns` override
  (the primary clock's period had no per-flow override at all before
  this — every flow in a plugin silently shared one global value).
- **Two real reset_sync miscompiles**, both invisible to every existing
  text-only unit test and found only by actually simulating a design
  with genuinely independent domains: a `wire rst_sync_<name>`
  declared *after* its first use (Xilinx xvlog silently creates a
  separate, permanently-undriven implicit net for the earlier
  reference); and a CDC data synchronizer's own `src_rst`/`dst_rst`
  binding to the raw, unsynchronized domain net instead of the real
  `cdc_reset_sync` output. Both fixed with real regression tests, see
  the "Fix two real reset_sync miscompiles" commit.
- **ATG027**: `forge.topgen.generators.structural_verilog`'s `cdc_map`
  is keyed by `(src_module, dst_module)` alone, not per-pin — this
  design's own natural architecture (control sourcing three *different*
  CDC kinds to pixel on three different pins) is the first thing in
  this repo to ever attempt that, and the second declaration silently
  overwrote the first for every pin between that module pair, with zero
  diagnostic. `DesignConfig.load` now raises a real ATG027 error
  instead; `design_cdc.yml` itself is restructured (one small module
  per crossing kind on the *source* side — `ctrl_level_rtl`/
  `ctrl_pulse_rtl`/`ctrl_mailbox_rtl`, `outsink_level_rtl`/
  `outsink_pulse_rtl` — rather than one bigger register bank per
  domain) to route around the constraint; a genuinely per-pin `cdc_map`
  is a larger, separate refactor this slice does not attempt.

`design_cdc.yml` itself: three domains at genuinely different real
rates (control 50MHz/ap_clk, pixel 200MHz/`clk_pixel`, output
125MHz/`clk_output`, not same-period stand-ins), all five CDC kinds
(level_sync/pulse_sync twice each — both control→pixel and, spec
§8.1/§8.2's own named crossings, output→control — plus one
mailbox_transfer and one async_fifo), and two real
`reset_domains.*.sync: reset_sync` destinations synchronized from the
same external `ap_rst` (spec §7). Standalone design, same "demonstrate
one slice's capability set in isolation" precedent 10.2/10.3 already
established.

**10.4 completion evidence**: `design_cdc.yml` (real design, 6
instances, 6 CDC-declared connections), `ctrl_level_rtl`/
`ctrl_pulse_rtl`/`ctrl_mailbox_rtl`/`pixel_sink_rtl`/`outsink_level_rtl`/
`outsink_pulse_rtl` (real RTL), and the `cdc_xsim` verify flow — a real
Vivado xsim run with three genuinely independent free-running clocks,
checked directly (no golden-model provider — see `gen_stimulus_cdc.py`'s
own header for why a scalar control-plane test doesn't fit that mold):
6/6 checks pass (`error_level_status`/`frame_done_count`/
`enable_status`/`apply_count`/`mailbox_status`/`result_status`),
confirming every one of the five CDC kinds actually carries a value
correctly across a real clock-domain boundary, not just that the
generator emits the right instance name. The three pre-existing xsim
flows (`quickstart_pipeline_xsim`/`pixel_result_xsim`/`tile_stats_xsim`)
were re-run unchanged and still pass. `invalid_direct_bus_cdc.yml`
(spec §8.5) is a real negative fixture: `forge topgen validate` reports
both ATG023 and ATG024 as warnings, and `forge topgen gen-top --strict`
hard-fails (exit code 1, no RTL generated) — confirmed via the real
commands, not assumed. Spec §8.5's other two invalid-design cases are
explicitly not built, for real reasons documented in that fixture's own
commit: `invalid_missing_reset_sync` is intentionally not something
`verify_cdc` flags (a single `cdc:` declaration approves both clock-
and reset-crossing for its connection, by that module's own design);
`invalid_bus_scalar_sync` has no sound enforcement path at all (the
framework cannot structurally distinguish a bus that genuinely needs
atomic coherence from legitimate independent per-bit signals like
one-hot, from width alone).

Negative fixtures land incrementally alongside each capability (the
spec's own 10-item invalid-design matrix), not batched at the end —
unchanged from v1's recommendation, now explicitly attached to the slice
that introduces the corresponding positive capability (e.g. the three
CDC negative fixtures land in 10.4, throughput/FIFO-depth negative
fixtures land in 10.5).

**10.5 scope note**: unlike 10.2-10.4, this slice's own core-framework
prerequisite genuinely was needed, but only two small, targeted additions
surfaced by actually wiring a real (non-constant-valued) record producer
through `cdc: {kind: async_fifo}` for the first time — every prior
async_fifo usage (design_cdc.yml, slice 10.4) held its source value
constant for the whole test, explicitly deferring "FIFO fill/drain
dynamics" to this slice (see `pixel_sink_rtl.v`'s own header).

- **Real per-record write-enable** (`cdc.write_enable_pin`,
  `forge.topgen.config`/`forge.topgen.generators.structural_verilog`):
  `cdc_async_fifo` previously wrote a fresh entry every write-domain
  cycle unconditionally (its documented, correct-for-a-level-value
  behavior) — for a genuine record stream this floods the FIFO with
  duplicate entries whenever the write clock is faster than the read
  clock, making occupancy/high-water/overflow telemetry reflect clock
  speed, not real record production, defeating Decision B's own point.
  Fixed by an opt-in `write_enable_pin` connection field (defaults to
  `1'b1`, byte-identical to every existing design's behavior when
  unset) plus a real `wr_en` port on `cdc_async_fifo` itself. Both
  `pixel_result_packer_rtl`/`tile_stats_packer_rtl` expose a real
  `out_record_valid` pulse (their own `in_valid`, registered on the
  same edge as the record itself) as this write-enable.
- **Registered, `!empty`-gated `dout`** (`cdc_async_fifo.v`): found
  immediately after the write-enable fix, via a real, reproducible
  off-by-one — `dout`'s original combinational `mem[rd_bin]` read can
  preview an in-flight write's value *before* the Gray-code-synchronized
  `empty` flag confirms it (`mem[]` itself isn't part of the CDC
  synchronization path, only the pointer comparison is), so a
  destination sampling `dout` every cycle with no `empty` of its own
  (this slice's own toggle-based novelty scheme, below) can silently
  "catch up" to a toggle transition before the FIFO calls it real,
  permanently losing that one transition. Registering `dout`, updated
  only on a real `!empty` read, closes the window — one extra cycle of
  latency, well within this primitive's own already-documented
  "fill/drain latency is data-dependent" framing. A second, related
  robustness fix in the same commit: `do_write` uses case-equality
  (`wr_en === 1'b1`) rather than a plain logical AND, since a real
  upstream HLS-generated signal (`tile_stats_hls`, reused unmodified)
  reads as `X` on cycles it never issues on — harmless everywhere that
  compares it against a clean 0, but poisonous once it reaches a
  register with no X-recovery (`wr_bin`), which is exactly what a plain
  `&&` let happen. Both fixes are additive to the shared primitive
  (`plugins/trigger_demo/algo/rtl/cdc_async_fifo.v`) with no port
  removed and identical default behavior — confirmed via a full
  regression re-run of every pre-existing xsim flow
  (`quickstart_pipeline_xsim`/`pixel_result_xsim`/`tile_stats_xsim`/
  `cdc_xsim`, all still pass) plus the full `forge/tests/` topgen/CDC
  suite (83 passed).

Everything else is real, project-level work, following 10.2/10.3's own
precedent of a standalone design demonstrating one slice's capability
set:

- **`pixel_result_packer_rtl`/`tile_stats_packer_rtl`** (new RTL, pixel
  domain): pack `edge_mask_merge_rtl`/`tile_summary_join_rtl`'s existing
  fields into the frozen 128-bit record layouts (§6.1), each also
  emitting a 1-bit toggle appended to the crossed payload (129 bits
  total) — real novelty detection with no core-framework valid/ready
  concept needed on the *destination* side of the crossing (FORGE's
  structural wiring routes no `empty`/valid to a connection's
  destination instance; a toggle bit embedded in the payload itself
  survives that, once paired with the registered-`dout` fix above).
- **`packetizer_rtl`** (new RTL, `output` domain — `clk_output`/
  `rst_output`, real 125MHz/8ns, spec §6): the real multiplexing point
  §6.2 describes ("multiplexed AT THE PACKETIZER, not merged before
  it") — two independent `cdc: {kind: async_fifo, depth: 64}` crossings
  feed it directly, no shared arbiter/FIFO upstream. 3-stage pipeline,
  fixed latency 3 / II=1, matching spec §11's module-catalogue entry.
  `packet_last` (not previously frozen by any decision) is this design's
  own chosen policy: the pixel-result record's own `end_of_frame` field
  when one occupies the beat, else 0 — documented in the module's own
  header, not silently assumed.
- **`design_packetizer.yml`**: two independent `norm_px`/`norm_tile`
  instances (not one shared fan-out source) feeding the reused-unmodified
  10.2 pixel-result subgraph and 10.3 tile-statistics subgraph
  respectively, `pixel` as the real 200MHz primary domain (unlike
  10.2/10.3's own informal 4.0ns testing clock — this slice cares about
  real domain rates for throughput reporting). Originally also a pacing
  necessity (back-to-back vs. `tile_stats_hls`'s then-real II=2, see the
  10.3 follow-up correction above); both paths pace identically now, but
  the two-instance structure is kept anyway as a deliberate scope
  choice, not an oversight: unifying the spec's real single-normalizer
  fan-out is the same single-shared-source topology work 10.2/10.3
  already deferred to the full-acceptance assembly (10.6/10.7, alongside
  the 16×16 dataset and multi-tile-per-frame concurrency).
- **Verification strategy**: unlike every dataset-driven flow so far,
  this design's real CDC-crossing timing is clock-phase-dependent, not
  statically derivable by hand (the same reason slice 10.4's own
  `cdc_xsim` flow uses no golden-model provider). `gen_stimulus_packetizer.py`
  drives `norm_px`/`norm_tile` from one `fork`/`join` process (`ap_clk`)
  while a second, concurrent process (`clk_output`) polls
  `pktz_packet_valid` every cycle, decodes whichever 128-bit slot(s) are
  occupied via `record_kind`, and checks each against the *next* expected
  record of that kind — both FIFOs are individually order-preserving, so
  "the k-th received record of a given kind" is unconditionally
  dataset-order index k, no exact-cycle timing needed. This checks real
  conservation (§17.3: every accepted record emitted exactly once, none
  dropped/duplicated) and real content correctness together. A real,
  reproducible off-by-one was found and fixed this way before the
  `cdc_async_fifo` fixes above were even identified as the root cause
  (an initial guess — a fixed output-domain "settle" warmup before
  polling — was tried and found *not* to be the cause, since removing it
  entirely reproduced the identical failure; the real fix was the
  registered-`dout` change).
- **Throughput/occupancy reporting** (`render_throughput_result.py`,
  real `forge.throughput_result.v1` artifact): real
  `StaticThroughputAnalysis` from this plugin's own already-synthesized
  HLS reports (`pixel_normalizer`/`sobel_hls`/`tile_stats_hls`, all at
  the real 200MHz pixel-domain rate) plus real `RuntimeThroughputResult`
  from a real Tier 2 probe CSV of both FIFOs' occupancy/high-water/
  full/overflow signals. Two honestly-documented, found-not-assumed
  limitations, both in the script's own docstring: (1) Tier 2 probe CSV
  sampling is tied to a single clock (`ap_clk`) regardless of a probe's
  own native domain — accurate for the write-domain signals (share
  `ap_clk`), an oversampled approximation for the read-domain ones
  (`*_empty`/`*_underflow`, ~1.6x oversampled at the 8ns/5ns ratio); (2)
  `forge.analyze.throughput_runtime.probe`'s generic accepted/emitted
  approximation ("a non-full/non-empty cycle is a real
  accepted/emitted transaction") assumes continuous per-cycle writes —
  correct for 10.4's constant-held async_fifo demo, invalidated by this
  slice's own real write-enable gating (most non-full cycles are now
  genuinely idle, not real writes). This design's own real record counts
  are already known exactly from its functional verification (64
  pixel-result + 1 tile-statistics, zero dropped/duplicated), used
  directly for `accepted`/`emitted` instead of the generic approximation,
  while occupancy-derived fields (high-water, full events, dropped)
  still come from the real probe CSV. Neither limitation is a
  core-framework fix attempted this slice — both are reporting-quality
  concerns, orthogonal to the functional correctness already verified
  exhaustively above.
- **Negative fixtures** (§17.4/§17.5, landing with this slice per §21's
  own incremental-attachment convention):
  - `check_throughput_sustainability.py` (§17.4): compares a real
    module's already-synthesized `StaticThroughputAnalysis` (this
    plugin's own `pixel_normalizer`, II=1, real 200 Mrecord/s) against
    an explicitly-labeled *synthetic* II=2 consumer. Originally a real
    2:1 mismatch against `tile_stats_hls`; revised after the 10.3
    follow-up correction above made `tile_stats_hls` real II=1 too —
    every module in this plugin's own registry is now II=1, so no real
    production-code pair demonstrates a mismatch anymore. Building a
    dedicated slow HLS module purely to keep this fixture "real" was
    considered and rejected per explicit direction: it would need
    wiring somewhere to stay buildable, risking exactly what it must
    not do — touch the real architecture or pollute
    `design_packetizer.yml`'s own real throughput/FIFO measurements
    below. A synthetic consumer, clearly labeled as such in the
    script's own docstring, checks the identical real comparison logic
    against a real producer without that risk. Fails with
    producer/consumer rate, ratio, and suggested remedies, exactly per
    spec.
  - `invalid_fifo_depth_packetizer.yml` + `check_fifo_capacity.py`
    (§17.5): identical to `design_packetizer.yml` except the
    pixel-result crossing's depth is 4 instead of 64 — still a valid
    power of two (passes `forge topgen validate`'s ATG022/ATG026;
    depth *sufficiency* for a burst pattern is a measured, not a static,
    property). The real `invalid_fifo_depth_xsim` flow genuinely
    overflows (real `overflow_attempt` events, real dropped data, a real
    functional-checker mismatch at `pixel_result_record[4]`) —
    `check_fifo_capacity.py` turns that into a clear release-gate
    failure, comparing the configured depth (4) against this exact
    design's own real, non-saturating measured requirement (28, from
    `design_packetizer.yml`'s own real depth=64 run).

**10.5 completion evidence**: `design_packetizer.yml` (real design, 12
instances, two real independent `cdc: {kind: async_fifo, depth: 64}`
crossings), `pixel_result_packer_rtl`/`tile_stats_packer_rtl`/
`packetizer_rtl` (real RTL), and the `packetizer_xsim` verify flow — a
real Vivado xsim run streaming 64 back-to-back pixel-result-path pixels
and 64 back-to-back tile-statistics-path pixels (both paths pace
identically since the 10.3 follow-up correction above — see that note)
through two independent 200MHz-to-125MHz CDC crossings into a real
multiplexing packetizer: 66/66 checks pass (64 pixel-result records + 1
tile-statistics record, every field, plus the conservation-invariant
total), decoded from whichever beat/slot each record actually landed in
— no exact-cycle assumption anywhere. `--probe-log` captured real
occupancy telemetry: the pixel-result FIFO reached a real high-water
mark of 28 (out of depth 64, zero overflow — write rate genuinely
exceeds read rate for this burst, exactly the real backpressure
scenario Decision B exists to report on), the tile-statistics FIFO
peaked at 1 (its own single record). `render_throughput_result.py`
produced a real `forge.throughput_result.v1` artifact
(`packetizer_xsim/throughput_result.json`) with real static
(HLS-report-derived) and runtime (probe-derived) sections,
`bottleneck='pixel_normalizer'` (an arbitrary tie-break —
`pixel_normalizer`/`sobel_hls`/`tile_stats_hls` are now all real II=1 at
an identical 200 Mrecord/s each), `predicted_rate=200,000,000
records/s`. Both negative fixtures produce real, verified failures with
the diagnostics spec §17.4/§17.5 require (see above). The four
pre-existing xsim flows (`quickstart_pipeline_xsim`/`pixel_result_xsim`/
`tile_stats_xsim`/`cdc_xsim`) were re-run unchanged after the shared
`cdc_async_fifo.v` fixes and still pass, alongside the full
`forge/tests/` topgen/CDC/throughput suite (83 + 74 passed).

**Honest deferrals from this slice**: a beat genuinely carrying two
records (§9.1's "2 records/beat" packing case) was not forced or
observed at this quickstart scale — both async_fifo crossings are
independent, so whether their toggle transitions ever land on the same
`clk_output` cycle depends on real, unengineered CDC-crossing phase
timing; `packetizer_rtl` handles the case generically (verified by
inspection, not by a passing test that exercises it), and demonstrating
it deterministically is left to the higher-sustained-rate throughput/
backpressure acceptance dataset (10.6/10.7). The single-shared-normalizer
fan-out topology (spec §5's real single-source diagram) and a per-probe
Tier 2 sampling-clock extension (this slice's own found gap, above) are
both explicit future work, not attempted here.

**10.6 scope note**: unlike 10.0B/10.0C, this slice's own core-framework
prerequisite (§7's `DatasetService`/`ProjectDatasetAdapter` protocol) was
already real (slice 10.0A) — 10.6 is project-level adapter work only:
three new real `datasets/` package modules
(`plugins/vision_pipeline_demo/datasets/`, mirroring spec §18.13's own
layout) plus their thin FORGE-registered wrappers. BSDS500/Fashion-MNIST
(Tier C, spec §18.4) remain explicitly out of scope (unchanged from
§15's deferral) — nothing below builds them.

- **`SyntheticPatternAdapter`** (`datasets/adapters/synthetic.py`):
  constant/horizontal_edge/vertical_edge/corner/checkerboard/ramp/noise
  patterns, no external dependencies (stdlib `random`, seeded — only
  `noise` actually consumes the seed, every other pattern is a pure
  function of (x, y, width, height) and is seed-invariant by
  construction).
- **`ImageFolderAdapter`** (`datasets/adapters/image_folder.py`):
  PNG/PGM/JPEG/BMP via Pillow (decode only — never Pillow's own
  grayscale/resize, per spec §18.5's "do not rely on library defaults").
  Grayscale is the frozen integer-luma formula
  (`(77*R + 150*G + 29*B) >> 8`); resize is a hand-rolled
  nearest-neighbor (`sx = ox*src_w // dst_w`), not `Image.resize`, so
  behavior can't shift between Pillow versions. Records both the
  pre-preprocessing source-image hash and the post-preprocessing
  converted-image hash per spec §18.4.
- **`NumpyArrayAdapter`** (`datasets/adapters/numpy_array.py`): `.npy`/
  `.npz` under an explicit `HW`/`NHW`/`NHWC` layout; rejects any
  ndim/channel-count mismatch outright rather than guessing the axis
  mapping (spec §18.4's own explicit requirement). NHWC's 3-channel case
  reuses the identical frozen luma formula, so an equivalent NumPy source
  and image-folder source normalize identically.
- **`serialize_xml.py`**: `flatten_events()` is the single place
  canonical `DatasetEvent`s become FORGE's existing per-pixel event-dict
  shape (one `<event>` per pixel, not per frame) — both the on-disk XML
  writer and the FORGE-registered wrappers below call it, so there is
  exactly one flattening implementation. Round-trip-verified
  byte-identical against `forge.verify.dataset_format.XmlDatasetLoader`'s
  own output.
- **`manifest.py`**: the real `forge.dataset_manifest` v1 sidecar (spec
  §18.6) plus `check_staleness()` — a pure content-hash comparison
  against recorded source-file hashes, never modification-time-based
  (matching `content_hash.py`'s own already-stated principle). Also adds
  an `adapter_config` field beyond spec §18.6's representative example —
  the exact adapter constructor kwargs, needed for `rebuild` to
  re-materialize a dataset from the manifest alone.
- **FORGE-registered wrappers** (`forge/verify/tools/dataset_adapter.py`):
  `vision_pipeline.synthetic` / `vision_pipeline.image-folder` /
  `vision_pipeline.numpy-array`, each a thin
  `ProjectDatasetAdapter.materialize()` around the corresponding
  `datasets/adapters/*.iter_events()` call plus `flatten_events()` — no
  adapter logic duplicated, per spec §18.9. All three verified reachable
  through the real `forge.verify.dataset_service.DatasetService`, the
  same path `gen_stimulus.py`/`forge test run` already use.
- **`cli.py`**: `generate-synthetic`/`import-images`/`import-numpy`/
  `inspect`/`validate`/`rebuild` (spec §18.9's example-local command
  set; `import-bsds500`/`import-fashion-mnist` are not wired up, Tier C
  out of scope). `rebuild` re-runs the recorded adapter and reports a
  clear mismatch if the result no longer matches the manifest (source
  data or adapter behavior drift), rather than silently overwriting.

**Two real bugs found and fixed during implementation** (both confirmed
empirically, not assumed):

1. **A `forge` package shadowing bug.** The original approach anchored
   `datasets/`'s absolute imports by adding the plugin root
   (`plugins/vision_pipeline_demo/`) to `sys.path`. That directory also
   contains this plugin's own `forge/` asset subtree (`forge/designs/`,
   `forge/verify/`, no `__init__.py` — verification configs, not a Python
   package). Once on `sys.path`, Python's `PathFinder` resolves top-level
   `forge` as a *namespace* package rooted there — `PathFinder` is tried
   before the real `forge` editable-install's own meta-path finder
   (registered via `sys.meta_path.append(...)`, i.e. strictly after
   `PathFinder` in resolution order) — so `import forge.verify.results`
   failed with `ModuleNotFoundError: No module named 'forge.verify.results'`
   from a direct `python3 datasets/cli.py ...` invocation, even though the
   identical import worked fine from every other entry point in this
   repo. Fixed by loading the `datasets` package via
   `importlib.util.spec_from_file_location(..., submodule_search_locations=...)`
   in both `cli.py` and `dataset_adapter.py`'s wrapper module, never
   touching `sys.path` — the same "avoid a bare-name collision" shape
   `plugins/trigger_demo/forge/verify/tools/tests/conftest.py`'s own
   `_load_by_path` already established, extended here to a real package
   with submodules rather than one flat module.
2. **A manifest relocation-hash bug.** `manifest.py`'s
   `compute_canonical_events_hash()` originally hashed each
   `DatasetEvent`'s full dict representation, including
   `source_metadata` — which `ImageFolderAdapter` populates with the
   image's absolute local path. A real test (copying a source image
   directory to a new path and re-materializing) caught this directly:
   the hash changed even though every pixel was byte-identical, violating
   spec §18.6's own explicit requirement ("local absolute source paths
   must not affect the portable semantic dataset hash"). Fixed by
   excluding `source_metadata` from the hash input — the same exclusion
   `forge.verify.dataset_format.SemanticMetadata` already makes for
   `EnvironmentMetadata`, just applied at this layer too.

**10.6 completion evidence**: 27 new tests
(`plugins/vision_pipeline_demo/forge/verify/tools/tests/test_dataset_adapters.py`),
covering every item in spec §18.12's required-test list that applies to
this slice's scope (determinism, filesystem-order independence, source
relocation, changed-pixel/changed-preprocessing hash sensitivity,
corrupt-file/ambiguous-shape rejection, XML round-trip) plus manifest
round-trip and staleness (content-change detected, mtime-only-touch and
byte-for-byte restoration both correctly report fresh) — all 27 pass.
Verified end-to-end outside pytest too: `cli.py generate-synthetic` /
`import-images` / `import-numpy` each produce a real `.xml` +
`.manifest.yml` pair; `inspect`/`validate` read them back correctly;
`validate --source-root` and `rebuild --source-root` both correctly
detect and report a real source-content change (exit code 1, no silent
pass) after a source image was directly edited. The three FORGE-registered
wrappers were exercised through the real `DatasetService.materialize()`
path (not just unit-tested in isolation). Pre-existing regressions
re-run clean: `plugins/trigger_demo/forge/verify/tools/tests` and
`plugins/passthrough_demo/forge/verify/tests` (one pre-existing,
unrelated failure in the latter — `test_single_flow_declared` — predates
this branch's Phase 10 work entirely, per `git log`, and is untouched by
this slice) and the full `forge/tests` suite (1248 passed, 8 skipped,
0 failed).

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
- `design_cdc.yml`'s `pxsink` merge point reports a real, pre-existing
  `forge analyze latency-check` mismatch (its three CDC-fed status
  inputs — level_sync/pulse_sync/mailbox_transfer — were never actually
  exact-cycle-aligned with each other) — found while validating the
  10.7B `unknown_cdc` framework fix (confirmed via `git stash` that this
  predates the fix, not caused by it). Likely a misclassification
  (`exact_cycle` may not even be the right alignment requirement for
  three independent status latches, unlike a real computed
  reconvergence), not touched — out of scope for 10.7B/design_cdc.yml's
  own already-shipped, passing `cdc_xsim` flow.
- Breadth beyond one deterministic 16x16 pattern for the "full-functional"
  dataset (spec's own "all valid image patterns" framing, §20) — 10.7A
  built one real checkerboard frame; exercising the other synthetic
  patterns (constant/horizontal_edge/vertical_edge/corner/ramp/noise) at
  this scale is not attempted yet.
- Per-probe Tier 2 sampling-clock extension (`forge.verify.gen_sim` only
  ever samples on `ap_clk`) — unchanged from the 10.5 finding, still
  applies identically to 10.7A's own dual-clock design.
