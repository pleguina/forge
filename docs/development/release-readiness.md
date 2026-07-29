# FORGE Public-Release Readiness — Working Checklist

This is the living tracking document for the public-release program defined
by two source documents:

- `docs/plan/FORGE_public_release_review_and_recommendations.md` — the
  original recommendation plan (§1–§21).
- `docs/plan/FORGE_RELEASE_AUDIT.md` — an evidence-based audit of that plan
  against the actual codebase, which **overrides** the recommendation plan
  wherever the two disagree (see audit §1 for the specific corrections).

Status legend: `DONE` / `REVISED AND DONE` / `PARTIALLY DONE` / `NOT DONE` /
`NOT APPLICABLE` (with justification). An item may only be marked `DONE` or
`REVISED AND DONE` here if it has executable evidence (a test, a command
that runs, a file that exists) — not a plan or an intention.

This file is updated incrementally as work lands, phase by phase, per the
audit's own ordered implementation plan (audit §4) and dependency graph
(audit §3). It is not a one-shot artifact — treat every future session that
touches release-readiness work as a session that also updates this file.

---

## How to read this file

Each row cross-references:
- the original recommendation doc section (`Rec §`),
- the audit's finding (`Audit status` — carried forward from the audit
  unless this session's work changed it, in which case `Updated status`
  reflects the new state),
- evidence (files, tests),
- what's left, referencing the audit's dependency graph so it's clear
  *why* something is sequenced where it is.

---

## Phase 0 — cheap wins (this session)

These are the audit's own "Phase 0 — cheap wins, no architecture
prerequisite" items (audit §4). All items below were implemented and
tested in this session.

| Item | Rec § | Audit status (before) | Updated status | Evidence |
|---|---|---|---|---|
| Fix `topgen gen-top --dry-run` write violation | 4.5 | PARTIALLY DONE, 1 confirmed violation | **DONE** | `forge/core/cli/groups/topgen.py` (`cmd_gen_top`): ip_info collection now stays in memory under `--dry-run` instead of being written+read back; `--output`'s parent directory is no longer `mkdir`'d under `--dry-run`. Test: `forge/tests/test_topgen_cli_commands.py::test_gen_top_dry_run_lists_without_writing` snapshots the full directory tree before/after `--dry-run` and asserts zero diff (including `ip_info.yaml`, previously the one documented exception). Invariant documented in `docs/MINIMAL_CONSUMER_QUICKSTART.md` ("The `--dry-run` invariant"). Manually verified against `plugins/passthrough_demo` — empty scratch directory after a real `--dry-run` invocation. |
| Structured coordinates (additive to `partition:`) | 8.3 | NOT DONE — confirmed anti-pattern in production | **REVISED AND DONE (scoped)** | New module `forge/topgen/ip/coordinates.py` (`coordinate_key`/`coordinate_label`) normalizes either a legacy scalar `partition: <string>` or a structured `coordinates: {axis: value, ...}` mapping to the same comparable key. Wired into `forge/topgen/config.py` (`InstanceAssign`), `forge/topgen/ip/contract_loader.py` (role dicts now carry `coordinates`), `forge/topgen/ip/topology_deriver.py` (`_resolve_instance_partition`, `_resolve_auto_match`), and `forge/topgen/ip/contract_verifier.py` (`verify_topology_groups`, now also detects duplicate coordinates across consumer roles — previously silent). Existing `partition:`-based designs (`plugins/trigger_demo`) need no changes. Tests: `forge/tests/test_coordinates.py` (unit), new cases in `forge/tests/test_topology_deriver.py` and `forge/tests/test_contract_verifier.py`. **Not done**: declarative cardinality (exactly-one-producer, forbidden fan-out, required completeness) — that's coupled to the matcher/IR work in later phases; see "Deferred" below. |
| Resolve `clock_secondary`/`reset_secondary` dead stub | — (audit-identified, decision item #3) | NOT DONE — declared but unused, misleading | **DONE (marked reserved, not removed)** | `forge/topgen/ip/canonical_roles.yaml`: both roles now carry `status: reserved` plus an explicit description of what "reserved" means. `ContractVerifier.verify()` (`forge/topgen/ip/contract_verifier.py`) now emits an explicit warning (not an error) whenever a contract declares a reserved role, naming it and stating it has no functional effect. Documented in `docs/IP_INTERFACE_POLICY.md` ("Reserved roles"). Tests: `forge/tests/test_contract_verifier.py::TestReservedRoles`. Decision (confirmed with maintainer): keep as reserved/experimental rather than remove, since removal would be a breaking schema change for zero current benefit (grep confirms no plugin uses either role). |
| Scope wording (AMD/Xilinx explicit) | 3.1/3.3 | PARTIALLY DONE — scoped but not explicit | **DONE** | `README.md` now opens with the exact recommended positioning quote and a "Current scope" table (toolchains, simulation backends, dataset limitation, clock-domain model, explicitly unsupported vendors). |
| Architecture diagram in README | — (audit gap list #17) | NOT DONE | **DONE (honest, not aspirational)** | `README.md` "Architecture (canonical IR — first slice landed)" — a Mermaid diagram depicting today's reality: an IR now exists and is consumed by `forge inspect`, but generation/latency/testbench-param extraction still independently re-derive facts from the same YAML. Updated again as of the Phase 1 slice 1 session below (see "Phase 1 (slice 1)"). |
| CODEOWNERS bus-factor risk | — (audit gap list #17) | NOT DONE — flagged as real risk | **NOT DONE — documented, not fabricated** | `CODEOWNERS` unchanged (single `@pleguina` entry). Per explicit maintainer decision this session: do not invent reviewer names. This is a people/organizational decision the audit itself flags as outside an audit's authority to resolve (audit §6 item 5). **Action needed from a human**: add at least one additional CODEOWNERS reviewer before public release. |
| Distribution-name decision documented publicly | 16.1 | REVISE — already resolved internally, not surfaced | **DONE** | `README.md` now points to the closed git-install-only decision inline (previously only reachable via `CONTRIBUTING.md`). No PyPI rename attempted — the audit is explicit this is a closed decision, not an open item. |
| Re-run `pytest --cov` for a trustworthy baseline | 15 (coverage numbers) | PARTIALLY DONE — `htmlcov` flagged as possibly stale | **DONE** | Baseline (before this session's changes): `372 passed, 9 skipped`, `49.39%` total coverage (`pytest --cov=forge --cov-fail-under=49`, run inside a fresh venv against `forge/pyproject.toml`'s `[project.optional-dependencies].dev`). This confirms the audit's suspicion that `htmlcov` was stale — freshly generated numbers matched the CI floor exactly, meaning the checked-in `htmlcov` artifact should not be trusted without regenerating it. Post-change run recorded below under "Phase 0 exit state". |

## Phase 1 (slice 1) — Canonical resolved design IR + `forge inspect`

Following Phase 0, this session began Phase 1 of `docs/plan/FORGE_release_plan.md`
(the highest-priority architectural item per the audit). Phase 1 as written
spans a 9-step subsystem migration; per its own instruction ("do not rewrite
all subsystems simultaneously... retain compatibility adapters... test old
and new paths until migration is complete") and rule 5 (avoid a coordinated
full rewrite), this session implements **migration steps 1–3 only**, plus
IR plumbing and a CLI consumer — a real, tested, additive slice with zero
changes to `gen-top`/`verify`/`analyze`'s existing code paths.

| Item | Status | Evidence |
|---|---|---|
| IR data model (`ResolvedProject`, `ResolvedDesign`, `ResolvedModuleDefinition`, `ResolvedInstance`, `ResolvedLogicalInterface`, `ResolvedInterfaceMember`, `ResolvedPhysicalBinding`, `ResolvedEndpoint`, `ResolvedConnection`, `ResolvedTransformation`, `ResolvedClockDomain`, `ResolvedResetDomain`, `ResolvedVerificationPlan`, `SourceLocation`, `DiagnosticReference`) | **DONE** | `forge/ir/model.py`. Pure dataclasses, no CLI/generator dependency. `IR_SCHEMA_VERSION = "0.1.0"`, independent of the package version (same pattern as the README's "two independent version numbers" note). |
| Deterministic construction | **DONE** | `forge/ir/build.py`: modules/instances/connections sorted by stable string keys before insertion. Test: `test_ir_build.py::test_build_is_deterministic` (same inputs twice → identical hash). |
| JSON export | **DONE** | `forge/ir/serialize.py::to_json_dict`/`to_json_str` (via `dataclasses.asdict`, `sort_keys=True`). Tests: `test_ir_model.py`. |
| Content hashing | **DONE** | `forge/ir/serialize.py::content_hash` — sha256 over the `design` subtree only, deliberately excluding `generated_from` (absolute local paths) and `forge_version` so the hash answers "did the resolved design change", not "did the tool version or caller's filesystem layout change". Test: `test_hash_ignores_generated_from_and_forge_version`. |
| Stable object identifiers | **DONE** | Instance IDs use the same convention as `forge.topgen.ip.matcher._inst` (so they line up with the existing matcher's `conn_map` keys); connection IDs are `{src_inst}.{src_port}->{dst_inst}.{dst_port}` or `external:{signal}->{inst}.{port}` for global-net fan-out. |
| Schema version | **DONE** | `ResolvedProject.schema_version` defaults to `IR_SCHEMA_VERSION`. |
| Source-location tracking | **PARTIALLY DONE** | File-level only (`SourceLocation.file`) — no line/column, since the underlying YAML loaders (`DesignConfig`, `LoadedContract`) don't retain parse positions. Documented as a known limitation in `model.py`'s `SourceLocation` docstring. |
| References to originating contracts/registry entries | **DONE** | `ResolvedModuleDefinition.contract_path`. |
| No CLI-rendering dependency | **DONE** | `forge/ir/model.py`/`build.py`/`serialize.py` import nothing from `forge.core.cli`; verified by inspection (`grep -rn "core.cli" forge/ir/` → no hits). |
| No generator-specific mutation | **DONE** | `build_project_ir` only reads (`DesignConfig.load_relaxed`, `load_contracts_for_design`, `collect_all`/`load_ip_info`/`synthesize_ip_info`, `auto_match_ports`) — never calls any generator. |
| Normalized matching results | **PARTIALLY DONE (upgraded in slice 2)** | Connections come from the existing matcher's `conn_map`/`global_nets` output. As of slice 2, per-connection `wiring_method` (`contract_wiring`/`port_map_ranges`/`port_map`/`auto_match`/`topology_group`/`heuristic`) is populated on every `ResolvedConnection` via `MatchReport.connection_evidence` (new field, `forge/topgen/ip/matcher.py`). Still not populated: rejected candidates, coordinates/protocol/width/cardinality/clock-domain results on the evidence itself — tracked as still-open below. |
| Generated transformations | **PARTIALLY DONE** | `ResolvedTransformation` captures `register_stages`/`delay_cycles` from `Connection`, applied per module-pair (coarse — not per physical-port-pair). Distinguishing pipeline-register/SLR/CDC/adapter kinds is Phase 3.3, not this slice. |
| Diagnostics attached to design objects | **DONE** | `DesignValidator` warnings/errors, reserved-role warnings (reusing `contract_verifier.load_canonical_role_vocab`, newly extracted as a shared helper), and unresolved-module/unresolved-connection diagnostics are all attached via `DiagnosticReference`. |
| `forge inspect --json` | **DONE** | `forge/core/cli/groups/inspect.py`, registered as a top-level command (same pattern as `forge doctor`) in `forge/core/cli/main.py`. Test: `test_inspect_cli_group.py::test_inspect_json_output_is_well_formed`. |
| `forge inspect --emit-ir <path>` | **DONE** | Writes the IR JSON to the given path — the *only* file this command ever writes; confirmed by tree-snapshot test `test_inspect_emit_ir_writes_exactly_the_requested_file`. |
| `forge inspect --diff <previous.ir.json>` | **DONE (coarse)** | `forge/ir/serialize.py::diff_projects` — hash-equality flag + added/removed/changed instance/connection IDs. Deep semantic diffing (e.g. "this instance's clock domain changed" as a labeled field-level diff) is explicitly out of scope for this slice. Test: `test_inspect_diff_against_itself_reports_no_changes`. |
| Read-only invariant (`forge inspect` never writes without `--emit-ir`) | **DONE** | Mirrors the Phase 0 `--dry-run` fix exactly: `build_project_ir` never calls `write_summary`/writes `ip_info.yaml`. Tests: `test_ir_build.py::test_build_never_writes_ip_info`, `test_inspect_cli_group.py::test_inspect_never_writes_without_emit_ir` (tree-snapshot technique). Manually verified against `passthrough_demo` and `trigger_demo`. |
| Real designs resolve end-to-end | **DONE** | `plugins/passthrough_demo` (1 module) and `plugins/trigger_demo` (7 modules, `--contracts-from` only, no HLS build artifacts present in this environment) both resolve fully — `test_ir_build.py::test_trigger_demo_resolves_all_seven_modules_from_contracts_alone`. |
| Graceful degradation for unresolved modules | **DONE** | A module with neither a contract nor a build artifact gets `ports_resolved=False` + a diagnostic instead of crashing. Test: `test_ir_build.py::test_unresolvable_module_gets_diagnostic_not_a_crash`. |

### Incidental bug found — fixed at the source (slice 2)

While testing the unresolved-module degradation path, `forge.topgen.ip.matcher.auto_match_ports`
was found to **crash with a bare `TypeError`** (`'NoneType' object is not
subscriptable`, `matcher.py`'s `_collect_heuristic`, and several other
`ip_info[key]["ports"]` call sites) whenever any module's ip_info entry is
`None` (e.g. a real design with a module missing HLS build artifacts and no
`--contracts-from`). This was flagged in slice 1 as a pre-existing bug,
worked around locally in `forge/ir/build.py` rather than fixed. **Slice 2
fixes it at the source**: a new `_mod_ports`/`_mod_port_dicts` helper in
`forge/topgen/ip/matcher.py` returns an empty result (plus a one-time
`report.warnings` entry) instead of subscripting a `None` ip_info entry,
used at all 5 previously-unguarded call sites in the global-net (clock/
reset/control-signal) section. `forge/ir/build.py`'s local workaround was
then removed — it now just calls `auto_match_ports` directly, since the
matcher itself degrades gracefully. Tests: `test_matcher_unresolved_ip_info.py`
(3 tests, including a mixed resolved+unresolved-module case), plus
`test_ir_build.py::test_unresolvable_module_gets_diagnostic_not_a_crash`
updated to assert the new (better) diagnostic message. Explicit connections
in `cfg.connections` that reference an unresolved module still raise a
`ValueError` — that remains deliberate existing validation (an explicit
connection genuinely can't be resolved without port metadata), not a bug.

### What Phase 1 still needs (not started, or only a slice of the full item)

- Migration steps 5–9 (Verilog/VHDL/BD *generation* itself, manifests/port
  maps, latency analysis, visualization, verification planning/bindings)
  — none of these subsystems consume the IR yet; they still independently
  re-derive facts from the same source YAML. Step 4 (topology matching) is
  now shared for `gen-top --mode verilog` (slice 3, below) — vhdl/
  block-design modes still compute independently.
- Rejected-candidates matching evidence, coordinates/protocol/width/
  cardinality/clock-domain results attached per-connection — `connection_evidence`
  (slice 2) gives the *selected* wiring method per connection, but not
  candidates considered and rejected; needed before the design explorer
  (§6) can show full "why was this connection made" detail.
- Full clock/reset domain model + CDC (Phase 3) — this slice's IR only ever
  produces one `"default"` domain.
- Protocol field (Phase 2.2), declarative cardinality (Phase 2.4), interface
  member grouping beyond 1:1 (Phase 2.5) — the IR's shape supports these
  (`ResolvedLogicalInterface.protocol`, `.members`) but they're unpopulated.
- Generation-plan artifact + hash (`forge build --plan/--apply/--accept-plan-hash`,
  Phase 3.4) — needs transformation-kind distinction (Phase 3.3) first.

---

## Phase 1 (slice 2) — matcher fix, matching evidence, provenance, import-linter

Continuing directly from slice 1's "next recommended session" list. All
four items landed, tested, with zero regressions (438 passed, 9 skipped,
up from slice 1's 412).

| Item | Status | Evidence |
|---|---|---|
| Fix `matcher.py` `None`-ip_info crash | **DONE** | See "Incidental bug found — fixed at the source" above. |
| Per-connection matching evidence (`wiring_method`) | **DONE** | `MatchReport.connection_evidence: Dict[(src_inst,src_port,dst_inst,dst_port), str]`, populated at both `lst.append(...)` sites in `matcher.py` (regular connections and topology_groups), reusing the existing per-connection wiring-method classification (no new heuristics). `forge/ir/build.py` looks this up per `ResolvedConnection` (module-to-module pairs) and separately classifies global-net/external connections as `contract_wiring`/`heuristic` via `MatchReport.contract_wired_roles`/`compat_mode_modules`. Manually verified against `trigger_demo`: 20 `contract_wiring`, 16 `topology_group`, 9 `port_map` connections, all correctly attributed. Tests: `test_matcher_connection_evidence.py` (3), `test_ir_build.py::test_connections_carry_wiring_method_evidence`. |
| Content-hash provenance for the IR | **DONE (bounded — see scope note)** | New `forge/core/utils/content_hash.py` (`hash_file`/`hash_files`, generalizing `port_signature.py`'s canonicalize→sha256 pattern per the audit's own suggestion) + `forge/ir/provenance.py` (`ProvenanceManifest`: schema version, FORGE version, IR schema version + content hash, per-source-file content hashes, command options, `generated_at` informational-only). `forge inspect --provenance <path>` writes it; `forge inspect --explain-staleness <path>` compares against a previous manifest and prints every reason they differ (changed input file, changed IR content, changed FORGE/schema version, changed command options, added/removed input files) — release-plan §5.1/§5.2/§5.3. **Scope note**: this is a new, parallel capability attached to the IR, not a replacement of the existing mtime-based `forge/core/stale_detection.py`/`forge/verify/stale_artifact.py` used by `gen-top`/`verify` — those remain untouched (see "still needs" below). Manually verified: fresh → mutate a real interface-contract file → correctly reports `changed input: .../passthrough.interface.yaml`. Tests: `test_content_hash.py` (5), `test_ir_provenance.py` (9), `test_inspect_cli_group.py` (+3: writes-manifest, fresh-then-stale, missing-file-guided). |
| CI import-linter (§19 revised recommendation) | **DONE** | `ci/import_direction_check.sh` — a grep-based linter in the same style/culture as `ci/agnosticism_check.sh`, enforcing: (1) nothing outside `forge/core/cli/` imports `forge.core.cli`; (2) `forge/topgen`/`forge/verify` don't cross-import; (3) `forge/analyze` doesn't import `forge.core.cli`; (4) `forge/ir` (the new canonical IR) doesn't import `forge.core.cli`, `forge.topgen.generators`, or `forge.verify` — encoding the "no CLI-rendering dependency, no generator-specific mutation" IR requirements from slice 1 as an enforced CI rule, not just a docstring claim. Wired into `.gitlab-ci.yml` as `forge:import-direction-check` (validate stage), matching the existing `agnosticism-check`/`stale-reference-check` job pattern. Passes cleanly on the current tree; verified it actually catches a planted violation (both via a manual negative test and `test_import_direction_check.py::test_import_direction_check_detects_a_real_violation`, which copies the script into a scratch tree — the script self-locates its repo root from its own path, so `cwd` alone can't be used to sandbox it). No physical 5-package split performed, per the audit's "premature" finding. |

### What's still open after slice 2

- The mtime-based `stale_detection.py`/`stale_artifact.py` used by
  `gen-top`/`verify` are still mtime-only — slice 2's content-hash
  provenance is a new parallel capability on the IR, not a replacement of
  those. Migrating `gen-top`/`verify` onto content-hash staleness is
  itself gated on those subsystems consuming the IR (migration steps 5+),
  not yet done.
- Rejected-candidates matching evidence (still open, see above).
- Migration steps 4–9 (still open, see above) — the import-linter's rule 4
  keeps `forge/ir` honest about staying read-only while more of these land.

---

## Deferred out of this pass (tracked, not attempted)

Per audit §4/§5 and this session's explicit scoping decision, the following
are **not** attempted here. Each is independently substantial engineering
(the audit's own estimate), and fabricating "DONE" evidence for any of them
would violate the governing rule that every checklist item needs concrete,
executable evidence.

| Item | Rec § | Audit status | Status here | Blocked by / rationale |
|---|---|---|---|---|
| Canonical design IR (`ResolvedProject`/`ResolvedDesign`/etc., JSON export, hash, diff, `forge inspect`) | §7 | NOT DONE — root cause of most other gaps | **PARTIALLY DONE (Phase 1 slice 1 — see below)** | First slice landed: `forge/ir/` (`model.py`/`build.py`/`serialize.py`) + `forge inspect --json/--emit-ir/--diff`. Covers migration steps 1–3 (config loading, contract loading, IP/RTL port metadata) plus read-only consumption of the existing matcher's connections. Does **not** yet cover generation/manifests/latency/visualization/verification (steps 5–9), full clock/reset domains, protocol, generation-plan hashing, or matching-evidence/rejected-candidates. Still upstream-blocking for the design explorer and most of §14 until further migration steps land — this slice removes the "doesn't exist at all" blocker, not the full dependency. |
| `protocol` field on interfaces | §8.1/§8.7 | NOT DONE — narrower gap than original doc implies (role/direction already exist) | **DONE (Phase 2.2)** | See the "Phase 2 — Contract schema stabilization" section below — implemented independently of clock/reset domain work, which turned out not to be a hard prerequisite after all (protocol is descriptive metadata; compatibility *validation* across domains is deferred, not the field itself). |
| Clock/reset domain model + CDC | §9 | NOT DONE — largest pure feature gap | **NOT DONE** | This session only resolved the *dead-stub honesty* problem (marked reserved). Building an actual single-domain model, let alone CDC, is the audit's largest scoped feature gap — explicitly P1, not P0, per audit §5. |
| Declarative cardinality (exactly-one-producer, forbidden fan-out, coverage) | §8.4 | NOT DONE as declarative schema | **PARTIALLY DONE** (unchanged) | Structured coordinates (done this session) is a prerequisite groundwork step, not the cardinality schema itself. Full cardinality constraints need the matcher/IR work. |
| Edge/adapter/CDC latency, alignment tolerance | §11 | PARTIALLY/NOT DONE | **NOT DONE** | Directly blocked on protocol + clock/reset domain work (audit §3). |
| Generation-plan artifact + hash (`forge build --plan/--apply/--accept-plan-hash`) | §10 | NOT DONE | **NOT DONE** | Needs the canonical IR to generalize the one existing template (SLR-crossing manifest) into something IR-shaped; premature before Phase 1. |
| Content-hash provenance for the IR | §12 | NOT DONE | **DONE (bounded — see Phase 1 slice 2)** | `forge/core/utils/content_hash.py` + `forge/ir/provenance.py` + `forge inspect --provenance/--explain-staleness`. Does not yet replace the existing mtime-based `stale_detection.py`/`stale_artifact.py` used by `gen-top`/`verify` — see Phase 1 slice 2 section above for the exact scope boundary. |
| `forge init/inspect/build/test/report` golden-path CLI | §4.1 | NOT DONE | **NOT DONE** | `init` needs the two existing scaffolders (`topgen init-plugin` + `verify init-plugin`) merged, which the audit recommends sequencing *after* the `plugin`→`extension` terminology decision (§3, dependency graph) — an open maintainer decision, not resolved here. |
| Verilator/GHDL/cocotb/VUnit as real simulation backends | §13 | NOT DONE — Verilator is lint-only today | **NOT DONE** | Correctly scoped to P1 per audit §5; xsim/csim-only is an honest v1 story, now stated explicitly in README's "Current scope" table. |
| Dataset adapter API beyond XML | §13 | NOT DONE | **NOT DONE** | Same as above — explicitly scoped down for v1, stated in README. |
| Static SVG / interactive HTML design explorer | §6 | NOT DONE | **NOT DONE** | Blocked on the canonical IR's matching-evidence/rejected-candidates data (audit §3) — building this now would mean throwaway work. |
| MkDocs site | §5 | NOT DONE | **NOT DONE** | Content largely exists and is good (audit's own assessment) — re-homing into Diátaxis buckets is real work but not attempted this session; recommend as an early Phase-4-equivalent session since it's mostly reorganization. |
| Genuinely unrelated-domain reference project (e.g. image-processing pipeline) | §15.4 | NOT DONE — P0 gate unmet | **NOT DONE** | The audit calls this the *cheapest P0 gap relative to the other P0 items* — not cheap in absolute terms. A trimmed, honest version (a handful of RTL/HLS modules, one topology, one verify flow) is still a multi-day effort with its own datasets, docs, and CI wiring. Not attempted this session to avoid a placeholder/half-built example, which the governing rules explicitly forbid. |
| Property-based tests (hypothesis), JUnit XML, full versioned-JSON-schema coverage | §15/§14 | NOT DONE | **NOT DONE** | Correctly P1 per audit §5. |
| Plugin terminology rename (`plugin`→`extension`/`project`) + entry-points-based registration | §4.3/§18 | REVISE — real but underestimated cost | **NOT DONE** | Explicit audit recommendation: do this deliberately in its own cycle with a compat shim, not squeezed into a release push (audit §6 item 2). Needs an explicit maintainer go/no-go. |
| 5-package repository split (`forge-model/sdk/backends/analysis/cli`) | §19 | REVISE — premature | **REVISED AND DONE (lower-complexity alternative)** | Implemented the audit's own recommended alternative instead of the disruptive split: `ci/import_direction_check.sh` (Phase 1 slice 2) — a CI-enforced dependency-direction linter. No physical package split performed; not needed per the audit's finding that the dependency direction is already clean. |

## Governing-rule notes (why some original recommendations are not being implemented as originally written)

Per governing rule 2/3 of the release program, the following original
recommendations are **not** blindly implemented, because the audit found
them `NOT APPLICABLE`, `REVISE`, or premature:

- **§16.1 distribution name** — `NOT APPLICABLE` as an open decision; it's
  already closed (git-install-only, documented in `CONTRIBUTING.md`). This
  session only made the closed decision more visible (README pointer), it
  did not reopen or resolve a new naming question.
- **§19 five-package split** — `REVISE`: replaced in this checklist's
  recommendation with "add a CI import-linter," not attempted yet but
  explicitly not slated for the disruptive full split.
- **§8.1 "split `wiring_kind` into role/type/protocol/direction"** —
  `REVISE`: `direction` already exists as its own field and `wiring_kind` is
  already documented as "not a protocol type" in
  `docs/IP_INTERFACE_POLICY.md`. The real gap is adding `protocol` alone —
  tracked as such above, not as a 4-field schema rewrite.
- **§15.4 "unrelated domain example ≈ `passthrough_demo`"** — `REVISE`:
  the audit correctly notes `passthrough_demo` is vocabulary-neutral, not
  domain-unrelated. This checklist tracks a real unrelated-domain project
  as still `NOT DONE`, not `DONE` via `passthrough_demo`.
- **`clock_secondary`/`reset_secondary` removal** — considered and
  explicitly rejected this session in favor of marking reserved (see Phase
  0 table above) — a product-honesty call per audit §6 item 3, resolved by
  the maintainer rather than assumed.

## Phase 0 exit state

- Test suite: run inside a fresh `venv` (system Python 3.9; repo's
  `pyproject.toml` declares `requires-python = ">=3.8"`) with
  `pip install -e ".[dev]"`.
- Baseline (before this session's changes): `372 passed, 9 skipped`,
  coverage `49.39%`.
- After this session's changes: `389 passed, 9 skipped` (+17 new tests:
  8 in `test_coordinates.py`, 3 in `test_topology_deriver.py`, 6 in
  `test_contract_verifier.py`), coverage `49.45%`, **0 failures, 0
  regressions** in the pre-existing suite. `htmlcov` was regenerated fresh
  as part of this run (not reused from a stale checked-in copy) —
  confirms the audit's caveat that the previously-committed `htmlcov`
  should not be trusted without regenerating it first.

## Phase 1 (slice 1) exit state

- After this session's IR/`forge inspect` changes: `412 passed, 9 skipped`
  (+23 new tests over the Phase 0 exit state: 9 in `test_ir_model.py`, 6 in
  `test_ir_build.py`, 7 in `test_inspect_cli_group.py`, plus a 1-line fix to
  a pre-existing CLI help-consistency test that needed `inspect` added to
  the `--group` help string), coverage `52.41%`, **0 failures, 0
  regressions**.
- Manually verified: `forge inspect` against both `plugins/passthrough_demo`
  and `plugins/trigger_demo` (human output, `--json`, `--emit-ir`, `--diff`
  against itself — hash-equal, no changes reported); tree-snapshot
  before/after a plain `forge inspect` call from within a scratch directory
  confirmed zero files written.

## Phase 1 (slice 2) exit state

- After the matcher fix, matching evidence, provenance, and import-linter
  changes: `438 passed, 9 skipped` (+26 new tests over the slice 1 exit
  state: 3 in `test_matcher_unresolved_ip_info.py`, 3 in
  `test_matcher_connection_evidence.py`, 1 in `test_ir_build.py`
  (`test_connections_carry_wiring_method_evidence`), 5 in
  `test_content_hash.py`, 9 in `test_ir_provenance.py`, 3 in
  `test_inspect_cli_group.py` (provenance/explain-staleness coverage), 2 in
  `test_import_direction_check.py`), coverage `52.95%`, **0 failures, 0
  regressions**.
- Manually verified: `auto_match_ports` no longer crashes on a `None`
  ip_info entry (direct call, no design.yml needed); `trigger_demo`'s 45
  connections all carry a real, correctly-attributed `wiring_method`
  (20 `contract_wiring`, 16 `topology_group`, 9 `port_map`); `forge inspect
  --provenance` → mutate a real interface-contract file → `--explain-staleness`
  correctly reports `changed input: .../passthrough.interface.yaml`;
  `ci/import_direction_check.sh` passes on the current tree and correctly
  fails on a planted violation.

## Phase 1 (slice 3) — migration step 4: `gen-top` shares matching/config computation with the IR

Chose the "retain compatibility adapters, test old and new paths" reading
of migration step 4 (per the plan's own instruction, and per the note
above that this is the natural next step and the first one touching
`gen-top`'s existing code path): rather than having `topgen gen-top`
*consume* `forge/ir`'s output (which would mean the generators take a
`ResolvedProject` instead of `cfg`/`ip_info`/`conn_map` — a step-5-sized
change to the code that actually produces `algo_top.v` content), this slice
has `gen-top` and the IR **share one computation** instead of two
independent ones, and emit the IR as a new artifact — zero change to any
existing generated output.

| Item | Status | Evidence |
|---|---|---|
| Split `forge/ir/build.py` into a loader (`build_project_ir`) + a shared assembler (`assemble_project_ir`) | **DONE** | `build_project_ir` is now a thin wrapper: load everything from scratch, then call `assemble_project_ir` — behavior-preserving (all 35 pre-existing IR/`forge inspect` tests pass unchanged). `assemble_project_ir` takes already-resolved `cfg`/`contracts`/`ip_info_data`/`conn_map`/`global_nets`/`match_report` and does only the IR-assembly work. |
| `topgen gen-top --mode verilog` (non-dry-run) emits `design.ir.json` | **DONE** | `forge/core/cli/groups/topgen.py::cmd_gen_top` calls `assemble_project_ir` with the exact objects it already computed (no second load) right after `maturity_report.json`, writing `design.ir.json` next to the other manifests. `--mode vhdl`/block-design are **not** covered by this slice — tracked below. |
| Cross-validation: shared-computation IR == fresh-reload IR | **DONE** | Manually verified on both `passthrough_demo` (1 module) and `trigger_demo` (7 modules, 45 connections): `gen-top`'s emitted `design.ir.json` content hash is byte-identical to a fresh, independent `build_project_ir(...)` call resolving the same design from scratch. Automated: `test_topgen_cli_commands.py::test_gen_top_design_ir_matches_fresh_inspect`. (Note: this surfaced a real, pre-existing path-resolution difference between `gen-top` — resolves `--contracts-from` relative to the consumer root — and `build_project_ir` — resolves it relative to CWD; not a bug in either individually, just something to pass equivalent/absolute paths for when comparing the two directly.) |
| Dry-run invariant preserved | **DONE** | `design.ir.json` added to the dry-run preview artifact list (verilog mode only); the existing whole-tree-snapshot dry-run test (`test_gen_top_dry_run_lists_without_writing`) already covers it structurally, and its explicit not-exists artifact list was extended for parity. |
| Golden-path regression check | **DONE** | `test_gen_top_verilog_full_pipeline` extended to assert `design.ir.json` exists with the correct schema version and module count — a new *addition* to the golden path's expected outputs, not a change to any existing one. Full suite: `439 passed, 9 skipped` (+1 over slice 2), coverage `55.23%` (up from `52.95%` — `forge/ir/build.py` is now exercised by both `forge inspect` and real `gen-top` runs), **0 regressions**. |

### What migration step 4 still needs

- `--mode vhdl` and block-design (`--mode bd`) do not yet emit `design.ir.json`
  — only verilog mode does. Extending to vhdl is a small, mechanical repeat
  of the same pattern (same `cfg`/`ip_info`/`conn_map`/`global_nets`/
  `match_report` are already computed before the mode branch); not done
  this slice to keep the diff small and reviewable.
- This was, at the time, still not migration step 5 — see slice 4
  immediately below, which closes that gap for verilog mode.

---

## Phase 1 (slice 4) — migration step 5: `write_structural_verilog` consumes the IR

The highest-risk step so far — the first to touch code that produces real
RTL content. Investigating it surfaced a concrete finding *before* any code
changed: `write_structural_verilog` (`forge/topgen/generators/structural_verilog.py`)
emits wire declarations and pipeline-register/signal-delay instance names
(`reg_stage_0`, `reg_stage_1`, `delay_0`, ...) by iterating
`conn_map.items()`/`pairs` in order, and those counters become literal
instance names in the generated file. The IR's stored `connections` list is
sorted by `id` (for reproducible hashing/diffing/visualization — correct
for that purpose) — a different order than `conn_map`'s natural
construction order. Naively switching generation to iterate the IR
directly would have silently renamed `reg_stage_N`/`delay_N` instances and
reordered wire declarations: functionally identical RTL, but a real diff
against anything referencing those names. Confirmed with the user before
proceeding: add an explicit ordering field used *only* to reproduce the
legacy order for RTL emission; keep ID-sort for everything else; prove
byte-identical generated output before switching anything.

| Item | Status | Evidence |
|---|---|---|
| `ResolvedConnection.emission_order: int` | **DONE** | `forge/ir/model.py`. Assigned in `assemble_project_ir` in the exact `conn_map`-then-`global_nets` construction order, *before* the existing `connections.sort(key=lambda c: c.id)` call — the stored list's ID-sort order is unchanged; `emission_order` just travels with each object. `forge/core/cli/groups/inspect.py`'s `--diff` JSON reconstruction (`_project_from_json`) updated to round-trip this new field too (a pre-existing round-trip helper that needed the same update every new IR field gets — caught by `test_inspect_diff_against_itself_reports_no_changes` failing until fixed). |
| `forge/ir/project.py::project_to_conn_map` | **DONE** | Pure function: sorts `project.design.connections` by `emission_order`, splits `$external`-producer connections into `global_nets` and the rest into `conn_map`, rebuilding both via ordered dict insertion — the exact inverse of `assemble_project_ir`'s construction. |
| Structural equivalence proof (ordered, not just set) | **DONE** | `test_ir_project.py`: `list(conn_map.items()) == list(projected_conn_map.items())` (and same for `global_nets`) on both `passthrough_demo` and `trigger_demo` (45 connections) — plain dict `==` would have hidden exactly the ordering bug this step is worried about, so the tests compare via `list(...)` explicitly. |
| Golden-generation byte-identical proof | **DONE** | `test_generation_ir_equivalence.py`: calls `write_structural_verilog` twice per design (direct `conn_map`/`global_nets` vs. IR-projected), asserts `direct_bytes == ir_bytes`. Passes on both reference designs, including `trigger_demo` where `reg_stage_N`/`delay_N` naming would be the first thing to break if ordering had diverged. |
| Switch `cmd_gen_top`'s verilog branch to be IR-driven | **DONE** | `forge/core/cli/groups/topgen.py::cmd_gen_top`: `project = assemble_project_ir(...)` now runs *before* `write_structural_verilog`, and `conn_map_ir, global_nets_ir = project_to_conn_map(project)` are what's actually passed to the generator — not the original `conn_map`/`global_nets`. `design.ir.json`'s emission (previously a second, redundant `assemble_project_ir` call after generation) now just reuses this one `project` object. `--mode vhdl`/`bd` untouched — still use the original `conn_map`/`global_nets` directly, out of scope. |
| Manual real-world confirmation | **DONE** | Regenerated `trigger_demo` post-switch and diffed against a pre-switch capture: `algo_top.v` **byte-identical**; `build_manifest.json` identical except expected non-semantic fields (absolute output path, timestamp); `design.ir.json`'s `connections` differ *only* by the newly-added `emission_order` field (expected — the pre-switch capture predates that field), confirmed by comparing with `emission_order` stripped from both. |
| Full suite | **DONE** | `444 passed, 9 skipped` (+5 over slice 3: 3 in `test_ir_project.py`, 2 in `test_generation_ir_equivalence.py`), coverage `56.41%`, **0 regressions**. |

### What migration step 5 still needs (as of slice 4)

- `--mode vhdl`/`bd`: `write_structural_vhdl`/`write_bd_tcl` still take the
  original `conn_map`/`global_nets` directly — not switched this slice
  (verilog-only scope, per an earlier explicit decision). **Closed in
  slice 5, below.**
- The generator's *internal* logic (`write_structural_verilog` itself) is
  completely unchanged — it still takes `conn_map`/`global_nets`-shaped
  arguments; only where those arguments come from changed. A deeper
  migration (the generator taking a `ResolvedProject`/`ResolvedDesign`
  view directly, eliminating the projection round-trip entirely) remains
  future work, and would only make sense once `reg_stage_N`/`delay_N`
  naming is made content-addressed rather than counter-based (a
  separately worthwhile improvement — counter-based names are already
  fragile to any upstream reordering, e.g. adding a connection earlier in
  `design.yml` shifts every later counter).
- Migration steps 6–9 (manifests/port maps consuming the IR directly rather
  than being computed from `cfg`/`ip_info` alongside it, latency analysis,
  visualization, verification planning/bindings) are all still untouched.

---

## Phase 1 (slice 5) — migration step 5, extended to `--mode vhdl`/`bd`

Same investigate-first, prove-then-switch methodology as slice 4, applied
to the two remaining generation modes.

**Investigation finding**: unlike `write_structural_verilog`,
`write_structural_vhdl` and `write_bd_tcl` have **no counter-based instance
naming** (`grep`-confirmed: zero `reg_stage`/`delay_stage`/`counter`
occurrences in either file) — so the specific `reg_stage_N`/`delay_N`
renaming risk that drove slice 4's design doesn't apply here. Still ran
the full proof (not just skip-switch on that basis alone), since wire/
signal declaration order could still textually change even without
instance renaming.

| Item | Status | Evidence |
|---|---|---|
| Golden-generation byte-identical proof, vhdl | **DONE** | `test_generation_ir_equivalence.py::test_{passthrough_demo,trigger_demo}_vhdl_generation_is_byte_identical` — `write_structural_vhdl` called with direct vs. IR-projected `conn_map`/`global_nets`, byte-identical on both reference designs. |
| Golden-generation byte-identical proof, bd | **DONE** | `test_generation_ir_equivalence.py::test_{passthrough_demo,trigger_demo}_bd_generation_is_byte_identical` — same proof for `write_bd_tcl`. |
| Switch `cmd_gen_top`'s vhdl branch to be IR-driven | **DONE** | Same pattern as verilog: `project = assemble_project_ir(...)` before `write_structural_vhdl`, fed `project_to_conn_map(project)`'s output. Now also emits `design.ir.json` (previously verilog-only). |
| Switch `cmd_gen_top`'s bd branch to be IR-driven | **DONE** | Same pattern; `write_bd_tcl` now fed the IR-projected structures; also now emits `design.ir.json` (previously no mode besides verilog did). |
| Dry-run invariant preserved, all three modes | **DONE** | Dry-run preview list updated: vhdl gets `design.ir.json` alongside its existing preview entries; bd gets its own new `design.ir.json` preview line (bd previously had no additional-artifact preview at all, matching that it previously generated only the one output file). Manually verified zero files written under `--dry-run` for both vhdl and bd on `passthrough_demo`. |
| Manual real-world confirmation | **DONE** | Regenerated `trigger_demo` in both vhdl and bd modes post-switch and diffed against pre-switch captures: both **byte-identical**. |
| Full suite | **DONE** | `448 passed, 9 skipped` (+4 over slice 4: the 4 new vhdl/bd equivalence tests), coverage `59.28%`, **0 regressions**. |

### What's left after slice 5

All three generation modes (`verilog`/`vhdl`/`bd`) are now IR-driven —
migration step 5 is complete for topology matching feeding generation.
Still open, unchanged from before:
- The generators' *internal* logic is unchanged in all three cases — they
  still take `conn_map`/`global_nets`-shaped arguments, just sourced from
  the IR instead of the matcher directly. A deeper migration (generators
  taking a `ResolvedProject` view natively) remains future work.
- Migration steps 6–9 (manifests/port maps, latency analysis,
  visualization, verification planning/bindings) are all still untouched.
- Rejected-candidates matching evidence, full clock/reset domain model +
  CDC, protocol field, declarative cardinality — all as before.

---

## Phase 1 (slice 6) — migration step 6: `generate_port_map` stops re-parsing generated Verilog

Investigating step 6 found that `generate_build_manifest`/`generate_port_map`/
`generate_probe_map`/`generate_tb_bindings` (all in
`forge/core/cli/groups/topgen.py`, not `forge/topgen/generators/`) don't
take `conn_map` at all, so steps 4/5's "swap the input source" pattern
didn't directly apply. The clearest, most valuable real instance of
"independent reinterpretation" (release-plan §1.4) among them:
`generate_port_map` discovered the top-level `algo_top` port list by
**re-parsing the Verilog file `write_structural_verilog` had just
written** (`_scan_verilog_ports`, regex-based) — the generator's `report`
return value only carried counts, not the port list itself, so there was
nothing else to consume. Confirmed with the user to do the real fix
(extend the generator's return contract, add a matching IR concept), not
just a smaller consumption swap — `generate_build_manifest` stayed
explicitly out of scope (needs *resolved absolute build-artifact paths*
the IR doesn't model at all — a different, separate modeling problem).

| Item | Status | Evidence |
|---|---|---|
| `write_structural_verilog` returns `report["top_ports"]` | **DONE** | `forge/topgen/generators/structural_verilog.py`: a `top_ports: List[Dict]` list is built alongside the existing `module_ports` text-declaration list, at every one of the 5 places a top-level port is decided (clock/reset, control signals, global nets, per-module external/aliased ports, debug ports) — purely additive; `lines`/`emit()` (what's actually written to `algo_top.v`) is untouched. |
| Proof: `top_ports` exactly matches what re-parsing would find | **DONE** | `test_generator_top_ports.py::test_{passthrough_demo,trigger_demo}_top_ports_matches_scan` — `report["top_ports"]` converted to `_scan_verilog_ports`'s `{name: (dir, width)}` shape compared for **exact equality** against `_scan_verilog_ports(output_path)`'s actual result on the generated file, both reference designs. |
| `generate_port_map` accepts pre-computed `ports`, falls back to re-parsing | **DONE** | New optional `ports: dict | None = None` parameter; `ports=None` (the default) behaves byte-for-byte identically to before. Proof: `test_generator_top_ports.py::test_{passthrough_demo,trigger_demo}_port_map_is_byte_identical` — calls `generate_port_map` twice (old re-parse path vs. new structured-data path), asserts byte-identical `port_map.yaml`. |
| `cmd_gen_top` wires the two together | **DONE** | Verilog branch converts `report["top_ports"]` to the expected shape and passes it to `generate_port_map(..., ports=...)` instead of letting it re-parse. |
| `ResolvedTopLevelPort` / `ResolvedDesign.top_ports` | **DONE** | `forge/ir/model.py`. **Deliberately not populated by `assemble_project_ir`** (stays `[]`, including for `forge inspect`, which never runs the generator) — the top-level port list is a *result* of the generator's lifting logic, not a pre-generation fact, and computing it independently in the IR builder would duplicate that logic in two places instead of one. `cmd_gen_top` attaches it to the already-built `project` object right after generation, before `design.ir.json` is written — the one place both pieces of information exist together. `forge/core/cli/groups/inspect.py`'s `--diff` JSON reconstruction updated to round-trip this field too (same lesson as slice 4's `emission_order`). |
| Golden-path cross-check | **DONE** | `test_gen_top_verilog_full_pipeline` extended: `design.ir.json`'s `top_ports` names exactly match `port_map.yaml`'s port names from the same real run. |
| Regression fix in an existing slice-3 test | **DONE** | `test_gen_top_design_ir_matches_fresh_inspect` updated: `top_ports` is the one *expected* divergence between gen-top's IR (has it) and a fresh, generation-free `build_project_ir()` call (never can) — now compared separately instead of folded into the overall hash-equality assertion, with both directions explicitly asserted (gen-top's is non-empty, fresh's is empty). |
| Manual real-world confirmation | **DONE** | Regenerated `trigger_demo` post-change and diffed against a pre-change capture: `algo_top.v` **byte-identical**; `port_map.yaml` identical except the expected non-semantic `source_file` path; `design.ir.json`'s `top_ports` populated with real data (12 ports on `trigger_demo`). |
| Full suite | **DONE** | `452 passed, 9 skipped` (+4 over slice 5: the 4 new `test_generator_top_ports.py` tests), coverage `59.31%`, **0 regressions**. |

### What's left after slice 6

- `generate_build_manifest` is **not done** — still iterates `cfg.modules`
  directly for source-file/build-artifact info. Doing this properly needs
  the IR to model *resolved* build paths (`module.abs_src`, discovered IP
  Verilog directories under `ip_root`), which it doesn't today — a
  separate, real modeling extension, not attempted this session.
- `generate_probe_map`/`generate_tb_bindings` take `port_map_data` (already
  structured, already downstream of this session's fix) — not
  independently re-parsing anything, so no further action needed there.
- vhdl/bd modes don't produce a `port_map.yaml`-equivalent at all today, so
  this fix is verilog-only by nature, not by choice.
- Migration step 7 (latency analysis) is now done (see slice 7, below).
  Steps 8–9 (visualization, verification planning/bindings) are still
  untouched.

---

## Phase 1 (slice 7) — migration step 7: shared registry-loader latency metadata + `LatencyGraph` off its independent re-parse

`forge/analyze/latency_static/graph.py::build_graph()` was a clean, real
case of "independent reinterpretation" (§1.4): it `yaml.safe_load`ed
`design.yml`/`modules.yml` directly as raw dicts, completely bypassing
`DesignConfig`/`Module`. Fixing it surfaced a real gap: `latency_cycles`/
`latency_hint`/`variable_latency` were already recognized by
`RegistryValidator`'s `_KNOWN_MODULE_KEYS` but silently discarded by
`DesignConfig`'s registry loader — `Module`/the IR had no latency data at
all. Per explicit direction: fixed at the shared loader (not a second,
narrower read inside `forge/ir/`), in a separate typed field (not
flattened into identity/pass-through semantics), with real validation.

| Item | Status | Evidence |
|---|---|---|
| `ModuleTiming` (separate, validated, optional field on `Module`) | **DONE** | `forge/topgen/config.py`: `latency_cycles`/`latency_hint`/`variable_latency`, with `__post_init__` rejecting the contradictory fixed+variable combination. `Module.timing: Optional[ModuleTiming] = None` — absent for any module that doesn't declare timing, identical to every pre-existing consumer. `_pop_timing()` helper converts raw registry/design-level keys into it, called once (after the `ref:` merge, so design-level inline overrides correctly win over the registry, matching every other field's precedence) before `Module(**m)` construction. Added to `IDENTITY_FIELDS` as recognized pass-through keys (confirmed by inspection: `Module` is never compared/hashed/deduplicated by value anywhere in the codebase, so this doesn't touch identity/equality semantics despite the name). |
| Validation | **DONE** | `RegistryValidator._validate_timing_fields` (`forge/topgen/validation.py`) reports the fixed+variable conflict as a structured `add_error` — same reporting path as every other registry rule, not a raised exception. `variable_latency` added to `_KNOWN_MODULE_KEYS` (`latency_hint`/`latency_cycles` were already there). Manually verified: a conflicting `modules.yml` entry produces a clean structured error via `forge topgen validate-registry`, not a stack trace. |
| Canonical IR gains latency fields | **DONE** | `ResolvedModuleDefinition.latency_cycles`/`.latency_hint`/`.is_variable_latency`/`.ip_info_key` (`forge/ir/model.py`), populated directly from `mod.timing`/`mod.ip_info_key` in `assemble_project_ir` — no re-parsing, same `Module` objects `cfg.modules` already is. Deliberately does **not** model the `hls_report` latency-source tier (a runtime overlay from actual build artifacts, external to registry/design data). `forge/core/cli/groups/inspect.py`'s `--diff` JSON reconstruction updated to round-trip these fields too (same lesson as slices 4/6). |
| `LatencyGraph.build_graph()` no longer independently re-parses | **DONE, with an important scope correction** | Initial attempt routed through the full matched IR (`forge.ir.build_project_ir`), but this **broke latency analysis's "usable before synthesis" property** — the full IR requires `ip_info`/contracts to resolve successfully (`auto_match_ports` hard-fails otherwise for explicit connections), which latency analysis never needed. Corrected to stop at `DesignConfig`/`Module` (the same shared loader the IR itself is built from) via a new `_build_graph_from_ir()` — module-level topology (`Connection`/`TopologyGroup` already carry singular, fan-out-expanded `from_`/`to` names) needs no IP/port matching at all. `LatencyNode`/`LatencyEdge`'s dataclass shape is **unchanged**, so `checker.py`/`reporter.py` needed zero changes — blast radius contained entirely to `graph.py`. |
| Compatibility fallback for the `modules_yml_path` override | **DONE** | `build_graph()` resolves design.yml's own `registry:` field and compares it against a given `modules_yml_path` override; when they differ (never observed in real/documented usage, but the parameter's public contract allows it), falls back to `_build_graph_legacy()` — the pre-migration implementation, kept verbatim. Test: `test_build_graph_falls_back_to_legacy_for_a_genuinely_different_override` proves the fallback path is actually exercised (a deliberately different alt-registry's value is visible in the result). |
| Proof: IR-driven vs. legacy produce identical nodes/edges | **DONE** | `test_latency_graph_ir_equivalence.py`, both reference designs: `nodes` dataclass-equal, `edges` set-equal. `test_trigger_demo_report_is_byte_identical`: full `latency_check.md` output byte-identical between the two paths. Manually confirmed via the real `forge analyze latency-check` CLI on `trigger_demo` too. |
| Full suite | **DONE** | `475 passed, 9 skipped` (+23 over slice 6: 16 in `test_module_timing.py`, 5 in `test_latency_graph_ir_equivalence.py`, 2 in `test_ir_build.py`), coverage `58.34%`, **0 regressions**. |

### What's left after slice 7

- `generate_build_manifest`'s resolved-build-path gap (unchanged from
  before this slice).
- Migration steps 8–9 (visualization, verification planning/bindings) are
  still untouched — `forge.analyze`'s HTML dashboard/report renderer and
  `forge.verify`'s `VerifyDesignContract` still independently re-derive
  facts from source YAML.
- The `hls_report` latency-source tier remains, correctly, external to the
  IR — it's runtime data from actual HLS build artifacts, not a
  registry/design fact.

---

## Phase 2 — Contract schema stabilization

Phase 1's migration reached a natural pause (steps 1–7 done; step 8 has no
subject matter to migrate yet since no visualization exists; step 9 is its
own future session). Moving to Phase 2 per the release plan/audit.

Status of Phase 2's 8 sub-items as of this session:

| Item | Status | Evidence |
|---|---|---|
| 2.1 Preserve `wiring_kind` | **DONE (no-op)** | Never touched — `wiring_kind` retained as-is throughout Phases 0/1, per the audit's own finding that it's already documented and semantically clear. |
| 2.2 Protocol semantics | **DONE (this session)** | See below. |
| 2.3 Structured coordinates | **DONE (Phase 0)** | `forge/topgen/ip/coordinates.py` — already substantially complete before Phase 2 started. |
| 2.4 Declarative cardinality | **DONE (this session)** | See below. |
| 2.5 Interface members | **DONE (this session)** | See below. |
| 2.6 Logical interface vs. physical binding | **DONE (Phase 1)** | The IR's `ResolvedLogicalInterface`/`ResolvedPhysicalBinding` split already cleanly separates these — done before Phase 2 started. |
| 2.7 Schema versioning | **DONE (this session)** | See below. |
| 2.8 Migration tooling | **DONE (this session)** | See below. |

### Protocol semantics (2.2)

The audit's own finding: `block_protocol` (`chain`/`hs`/`none`) is a
coarse, design-global AP-control toggle, not a per-interface concept — the
real, narrowly-scoped gap is adding `protocol` alone (not the 4-field
`wiring_kind` split the original recommendation proposed). Low-risk and
purely additive — unlike Phase 1's migration steps, this doesn't touch
matching, generation, or RTL content at all.

| Item | Status | Evidence |
|---|---|---|
| `protocol:` field on interface contract roles | **DONE** | Optional string on any role; four documented built-in values (`combinational`/`valid-only`/`ready-valid`/`fixed-frame`); absent by default, fully backward compatible. |
| Validation | **DONE** | `ContractVerifier` (`forge/topgen/ip/contract_verifier.py`) reports an unrecognized `protocol` value as a structured error (`KNOWN_PROTOCOLS` frozenset), same reporting convention as every other contract check — not a crash. |
| Surfaced through `LoadedContract.get_connection_roles()` | **DONE** | `forge/topgen/ip/contract_loader.py` — one-line addition to each of the three role-dict constructions, same pattern as `coordinates`/`partition` (Phase 0). |
| IR population | **DONE** | `ResolvedLogicalInterface.protocol` (added to the model in Phase 1, explicitly annotated "not populated this slice (Phase 2.2)") is now actually populated in `forge/ir/build.py::_build_interfaces`. `forge/core/cli/groups/inspect.py`'s `--diff` JSON reconstruction already round-tripped `protocol` since it was first written — no change needed there. |
| Real usage (not just synthetic test fixtures) | **DONE** | `plugins/trigger_demo/forge/interfaces/hit_decoder_ip.interface.yaml`'s `raw_hit`/`raw_valid`/`decoded_hit`/`decoded_valid` now declare `protocol: valid-only` — an honest description of this `clock_free`, `II=1` decoder gated only by valid strobes, not a placeholder. Manually verified: `build_project_ir` on `trigger_demo` shows `protocol == "valid-only"` for these interfaces; full test suite (including `test_equivalence.py` and the generation byte-diff tests) passes unchanged, confirming the new field is inert to matching/generation. |
| Tests | **DONE** | `TestProtocolSemantics` in `test_contract_verifier.py` (no-protocol-declared valid, each known value valid, unknown value is a structured error); `test_ir_build.py::test_interfaces_carry_protocol_from_the_contract`. |
| Full suite | **DONE** | `479 passed, 9 skipped` (+4 over Phase 1 slice 7), coverage `58.35%`, **0 regressions**. |

### Interface members (2.5)

A role opts into member grouping by declaring both `interface:` (the
shared logical-interface name) and `member:` (its role within that group
— one of `data`/`valid`/`ready`/`last`/`metadata`). Roles that omit these
fields keep today's 1:1 role-per-interface mapping unchanged — purely
additive, no existing contract needed to change.

| Item | Status | Evidence |
|---|---|---|
| `interface:` / `member:` fields on interface contract roles | **DONE** | Optional per-role strings; `forge/topgen/ip/contract_loader.py::get_connection_roles()` surfaces `interface`/`member`/`direction` on every returned role dict (defaulting to `role_name` when absent — preserves the 1:1 mapping). |
| Validation | **DONE** | `ContractVerifier` (`forge/topgen/ip/contract_verifier.py`) reports: an unrecognized `member` value as a structured error (`KNOWN_MEMBERS` frozenset); `member:` declared without `interface:` as a warning (no grouping effect); the same `member` name declared twice within one `interface` group as a structured error (ambiguous). |
| IR grouping | **DONE** | `forge/ir/build.py::_build_interfaces` merges roles sharing one `interface:` name into a single `ResolvedLogicalInterface` with one `ResolvedInterfaceMember` per role. Group-level `wiring_kind`/`coordinates`/`protocol` come from the `member: data` role (or the first role if none is named `data`). A member whose own `direction:` differs from the group's (e.g. a `ready` handshake signal flowing back from the consumer) is *not* forced onto the group's direction — it's recorded on the new `ResolvedInterfaceMember.direction` field instead, so cross-direction handshakes group correctly. |
| Matching unaffected | **DONE** | Topology matching (`forge.topgen.ip.matcher`, `topology_deriver.py`) still operates per role/`wiring_kind`, reading the same dicts as before — the two new dict keys are additive and ignored by matching. No change to `auto_match_ports` or connection derivation. |
| `--diff` round-trip | **DONE** | `forge/core/cli/groups/inspect.py::_project_from_json` reconstructs `ResolvedInterfaceMember.direction` from emitted IR JSON, so `forge inspect --diff` stays lossless for grouped interfaces. |
| Real usage note | **Deliberately not applied to `trigger_demo`'s checked-in contract this session** — `hit_decoder_ip.interface.yaml`'s `raw_hit`/`raw_valid` pair is documented in `docs/IP_INTERFACE_POLICY.md` "Interface members" as the intended real-world grouping candidate, but leaving it ungrouped keeps `test_interfaces_carry_protocol_from_the_contract` (which asserts an interface named `raw_hit`) and the byte-identical generation goldens untouched. Grouping it is a follow-up, not a blocker — the feature is exercised by dedicated synthetic-contract tests instead (see below). |
| Tests | **DONE** | `TestInterfaceMembers` in `test_contract_verifier.py` (valid grouping, unknown member is an error, member-without-interface warns, duplicate member in one group is an error); `test_ir_build.py`'s `test_roles_without_interface_field_stay_1to1`, `test_grouped_roles_merge_into_one_interface_with_multiple_members`, `test_reverse_direction_member_records_its_own_direction`. |
| Full suite | **DONE** | `487 passed, 8 skipped` (+8 over Phase 2.2's 479), coverage `68.38%`, **0 regressions**. |

### Declarative cardinality (2.4)

A role opts into cardinality constraints by declaring `cardinality:` —
`producers: {min, max}` (input roles) or `consumers: {min, max}` (output
roles), plus `fanout: allowed|forbidden` and `completeness: required|optional`
as sugar for the relevant bound. New module: `forge/topgen/ip/cardinality.py`.

| Item | Status | Evidence |
|---|---|---|
| `cardinality:` field, `fanout`/`completeness` sugar | **DONE** | `forge/topgen/ip/cardinality.py::parse_cardinality` — direction-aware (rejects `consumers`/`fanout` on input roles, `producers` on output roles, unknown keys, `max < min`). Absent by default, fully backward compatible. |
| Structural validation | **DONE** | `ContractVerifier` calls `parse_cardinality` per role and reports `CardinalityError` as a normal structured issue (`forge/topgen/ip/contract_verifier.py`) — same reporting convention as `protocol`/`member`. |
| Design-level enforcement | **DONE** | `verify_cardinality(design_cfg, contracts, match_report)` (`forge/topgen/ip/cardinality.py`) checks the *actual wired design* — not just one contract in isolation — against `auto_match_ports`'s `MatchReport`. Catches: required-producer-missing (`min` unmet — "required completeness"), forbidden/insufficient fan-out (`consumers` bound — fully observable today since the matcher never restricted source fan-out), and excess producers/ambiguous fan-in (`max` exceeded). |
| Matcher instrumentation for ambiguous fan-in | **DONE** | `MatchReport.rejected_fanin: Dict[(dst_instance, dst_port), List[(src_instance, src_port)]]` (`forge/topgen/ip/matcher.py`) — the existing first-driver-wins guard (both the regular-connections and topology-group wiring loops) now records every candidate it silently dropped, instead of only the one it kept. This is what makes "more than one valid match exists" (release-plan §2.4) detectable at all — previously the losing candidates left no trace anywhere. |
| Strict-mode gate | **DONE** | `forge gen-top --strict` (`forge/core/cli/groups/topgen.py`) fails with exit code 1 on any `error`-severity `verify_cardinality` issue — same pattern as the existing `verify_topology_groups` gate immediately above it. `--strict` help text updated to mention it. |
| IR representation | **DONE** | `ResolvedLogicalInterface.cardinality: Optional[Dict[str, Any]]` (`forge/ir/model.py`) — resolved (not raw) `{"producers"|"consumers": {"min", "max"}}`, taken from the group's `member: data` role (Phase 2.5 grouping) via `forge/ir/build.py::_cardinality_dict`. Descriptive only — `forge inspect` visibility, not itself an enforcement point (`verify_cardinality` is). `--diff` JSON round-trip updated in `forge/core/cli/groups/inspect.py`. |
| Relationship to existing ad hoc mechanisms | **Formalized, not replaced** | The matcher's silent first-driver-wins guard and `verify_topology_groups`'s overlap/coverage warnings still run unconditionally as before (unaffected) — `cardinality:`/`verify_cardinality` is an opt-in, declarative layer on top, not a rewrite of either. |
| Tests | **DONE** | `test_cardinality.py` (new — 20 tests: `TestParseCardinality`, `TestBound`, `TestVerifyCardinality`, including the rejected-fan-in scenario); `TestDeclarativeCardinality` in `test_contract_verifier.py` (structural validation); `test_ir_build.py`'s `test_cardinality_is_resolved_from_the_data_members_declaration`, `test_no_cardinality_declared_stays_none`. |
| Full suite | **DONE** | `513 passed, 8 skipped` (+26 over Phase 2.5's 487), coverage `68.85%`, **0 regressions**. |

### Schema versioning (2.7)

Unified on a plain `"major.minor"` `schema_version` string across the four
user-authored schemas (design topology, module registry, interface
contract, verification contract), applying a single shared compatibility
policy. New module: `forge/core/schema_version.py`. Full policy/rationale
in `docs/development/SCHEMA_VERSIONING.md`.

| Item | Status | Evidence |
|---|---|---|
| Shared parsing/compatibility helper | **DONE** | `forge/core/schema_version.py::parse_schema_version`/`check_schema_version` — accepts `"N"` (legacy bare-integer, e.g. `modules.yml`'s existing `registry_version: '1'`) or `"N.M"`; absent → silent; same-major/older-or-equal-minor → silent; same-major/newer-minor → warning; different major → error; malformed → error. |
| Design topology (`design.yml`) | **DONE** | `DesignConfig.schema_version` (`forge/topgen/config.py`, `DESIGN_SCHEMA_VERSION = "1.0"`), popped in `.load()`; checked by new `DesignValidator.validate_schema_version()` (`forge/topgen/validation.py`), wired into `validate_all()`. |
| Module registry (`modules.yml`) | **DONE** | Existing `registry_version` field kept as-is (already documented, semantically clear — same reasoning as keeping `wiring_kind` in 2.1); `RegistryValidator._validate_top_level()` now also checks it via `check_schema_version` against `MODULE_REGISTRY_SCHEMA_VERSION`, on top of the pre-existing "missing" warning. |
| Interface contract (`*.interface.yaml`) | **DONE** | `LoadedContract.schema_version` property (`forge/topgen/ip/contract_loader.py`, `INTERFACE_CONTRACT_SCHEMA_VERSION = "1.0"`); `ContractVerifier.verify()` checks `ip_interface.schema_version` the same way protocol/member/cardinality are checked. |
| Verification contract (`design.verification.yml`) | **DONE** | `VerifyDesignContract.schema_version` field, `VERIFY_CONTRACT_SCHEMA_VERSION = "1.0"` (`forge/verify/design_contract.py`). This loader is raise-only (no warnings-collection channel exists anywhere in the module) — only error-severity issues (different major, malformed) are surfaced, as a `DesignContractError`; a newer-minor warning is intentionally not surfaced. Documented as a deliberate asymmetry, not an oversight. |
| Canonical IR / provenance manifest | **Unchanged — already had identity/version** | `IR_SCHEMA_VERSION`/`PROVENANCE_SCHEMA_VERSION` keep their existing `"0.1.0"`-shaped strings; not rewritten to the new `"major.minor"` convention (different, already-shipped family — rewriting would be a gratuitous, disruptive rename of an existing identifier). |
| "Generated result structures" | **Deferred to Phase 6, not fabricated** | These are Phase 6.7's planned JSON output envelopes (`forge build`/`test`/`report`), which don't exist yet — nothing to version. |
| Absence is silent everywhere | **Verified** | No pre-existing test's warning/error counts changed by this work (`test_validate_json_output_is_well_formed`'s `== 2` warning-count assertion on `passthrough_demo`, and every `test_verify_contract_*` `returncode == 0` assertion, are all untouched) — confirms the "silent unless declared" design goal was met, not just claimed. |
| Tests | **DONE** | `test_schema_version.py` (new — 14 tests, the shared helper in isolation); `test_schema_version_integration.py` (new — 5 tests, `DesignConfig`/`DesignValidator` and `RegistryValidator` wiring); `TestSchemaVersioning` in `test_contract_verifier.py` (4 tests); 3 new tests in `test_design_contract_errors.py`; `test_explain_staleness_detects_schema_version_change` added to `test_ir_provenance.py` (closes a pre-existing coverage gap the code path already had). |
| Full suite | **DONE** | `540 passed, 8 skipped` (+27 over Phase 2.4's 513), coverage `69.24%`, **0 regressions**. |

### Migration tooling (2.8)

One CLI command, `forge topgen migrate --kind <kind>`, backed by pure
functions in `forge/topgen/migrate.py` — five kinds, each supporting
`--dry-run` and printing a readable diff (`difflib.unified_diff` for
text-content kinds; a plain action list for filesystem-move kinds).
Text-content kinds do **targeted line-level edits**, never a full
`yaml.safe_load`→`yaml.dump` round-trip — PyYAML has no comment-preserving
round-trip, and this project doesn't depend on `ruamel.yaml`, so a full
round-trip would silently strip every comment in hand-authored files.

| Item | Status | Evidence |
|---|---|---|
| `schema-version` | **DONE** | Inserts the Phase 2.7 schema version into a file lacking one (auto-detects design.yml/modules.yml/*.interface.yaml/design.verification.yml from filename, `--schema-kind` overrides); no-op ("already declared") is not an error. |
| `partition-to-coordinates` | **DONE, honestly scoped** | 1-axis wrap only (`partition: X` → `coordinates: {axis: X}`) — genuine multi-axis decomposition of an arbitrary partition string isn't safely automatable without a user-supplied mapping (`forge/topgen/ip/coordinates.py` treats a partition string as one opaque value); `partition:` isn't deprecated, so this is opt-in convenience, not a required migration. Refuses (exit 2) rather than guessing when `--role` names a nonexistent or ineligible role. |
| `legacy-plugin-layout` | **DONE** | Automates the one concrete case `MIGRATION.md` already documented manually (`<plugin>/verify/` → `<plugin>/forge/verify/`, dead `_FW_PYTHON` sys.path block removal). Only removes the block when a confident, contiguous marker/`sys.path` line pair is found; otherwise flags it for manual removal rather than guessing destructively. No live plugin in this repo still has the old layout, so this is exercised via synthetic `tmp_path` fixtures, not a real plugin — the logic itself is real and tested. |
| `rename-verify-contract` | **DONE** | Renames the deprecated `verify.design.yml` to the canonical `design.verification.yml` — a pure filesystem rename (same schema), no comment-loss risk. |
| `infer-contract` | **DONE, honestly scoped** | Generates a conservative `*.interface.yaml` skeleton for a compat-mode module (`MatchReport.compat_mode_modules`) from observed `ip_info` ports — only `clock_primary`/`reset_primary` are inferred, using the same conservative name heuristics `auto_match_ports` already uses for compat-mode wiring. Every other port is listed as an explicit `# TODO` comment, never assigned a role — data-port semantics can't be safely guessed from a name alone. |
| Dry-run invariant | **DONE** | Every kind computes its change fully in memory and only writes when `--dry-run` is absent — same compute-then-write-gated-by-dry-run pattern as `cmd_gen_top`/`cmd_clean`/`cmd_init_plugin`. Verified with `_tree_snapshot`-based tests (same technique as `test_topgen_cli_commands.py`'s existing dry-run tests). |
| Not automated | **Documented, not silently skipped** | ARC→FORGE rename (fully historical/closed, no live path to migrate); `--use-kind-subdir` (already has its own runtime deprecation warning, not a file-schema concern); `bx_counter` (already a complete no-op wherever found, nothing broken to fix). See `docs/development/MIGRATION_TOOLING.md` "Not automated (and why)". |
| Docs | **DONE** | New `docs/development/MIGRATION_TOOLING.md` (per-kind policy, example invocations); `MIGRATION.md` updated to point at the new `legacy-plugin-layout` command for the case it already documented manually. |
| Tests | **DONE** | `test_migrate.py` (new — 31 tests, pure functions); `test_topgen_migrate_cli.py` (new — 15 tests, CLI argument handling/dry-run/exit codes). |
| Full suite | **DONE** | `587 passed, 8 skipped` (+47 over Phase 2.7's 540), coverage `70.20%`, **0 regressions**. |

### Phase 2 status

All 8 sub-items (2.1–2.8) are now **DONE**. Phase 2 "Contract schema
stabilization" is complete.

---

## Phase 3 — Clock, reset, timing, and transformations

Three parallel research passes (design, transformation types + matching
evidence, generation-plan surface) confirmed this phase is almost entirely
greenfield — zero existing CDC code, zero transformation-kind distinction
beyond `register`/`delay`, zero `forge build` command. Implemented as a
sequence of slices, same discipline as Phases 1/2.

### Slice 1 — 3.1 baseline domain correctness

Before this slice, `ResolvedClockDomain`/`ResolvedResetDomain` always
produced exactly one domain literally named `"default"` containing
**every** instance, including clock-free/reset-free (purely combinational)
modules that were never wired to any clock/reset net at all —
`ResolvedInstance.clock_domain`/`.reset_domain` existed but were never
populated with anything but the same hardcoded string. This slice fixes
both problems: real domain identity, and correct exclusion of clock-free
modules.

| Item | Status | Evidence |
|---|---|---|
| Domain name is the resolved net, not a hardcoded literal | **DONE** | `forge/ir/build.py::_resolve_domain_nets` — contract-driven modules resolved authoritatively via `MatchReport.contract_wired_roles` (no re-matching); no-contract modules fall back to the same conservative name heuristics (`ap_clk`/`clk`/`clock`, `ap_rst`/`rst`/`reset`/`rst_n`) `auto_match_ports` itself uses. Manually verified: `passthrough_demo` → `"ap_clk"`/`"ap_rst"` (previously `"default"`); `trigger_demo` → `"ap_clk"`/`"ap_rst"` across its 7 modules. |
| Clock-free/reset-free modules correctly excluded | **DONE** | `contract.clock_free`/`.reset_free` (already existing properties, `forge/topgen/ip/contract_loader.py`) checked before falling back to heuristics — a clock-free module resolves to `None`, not a domain. Manually verified on the real `trigger_demo` design: `dec_0`..`dec_3` (hit_decoder_ip, `clock_free: true`) resolve `clock_domain=None` while still correctly resolving `reset_domain="ap_rst"` — previously these instances were incorrectly counted as members of the single `"default"` clock domain. |
| Per-instance scalar is the single source of truth | **DONE** | `ResolvedInstance.clock_domain`/`.reset_domain` (now `Optional[str] = None`, was `str = "default"`) are populated first; `ResolvedClockDomain`/`ResolvedResetDomain`'s `instances` lists are *derived* by grouping instances with a non-`None` value — no redundant independent population (`forge/ir/build.py`, `_grouped_domains` helper in `assemble_project_ir`). |
| Diagnostics for unresolved domains | **DONE** | A no-contract module whose ports don't match any clock/reset name heuristic gets a warning-severity `DiagnosticReference` (`"no clock/reset domain could be resolved"`). The pre-existing "contract declares a role but its raw_port wasn't found in ip_info" case already produced a `MatchReport` warning (already surfaced as a diagnostic) — not duplicated. Manually verified both paths (heuristic-found → no diagnostic; heuristic-not-found → diagnostic) with synthetic fixtures. |
| Existing behavior preserved elsewhere | **DONE** | No change to matching, generation, or any non-domain IR field. `IR_SCHEMA_VERSION` unchanged (field *values* changed, not the schema shape — `clock_domain`/`reset_domain` were always optional-shaped strings). |
| Tests | **DONE** | `test_ir_build.py`: `test_real_designs_resolve_to_named_not_default_domains`, `test_clock_free_module_has_no_clock_domain_but_keeps_reset` (real `trigger_demo`), `test_no_contract_heuristic_clock_name_resolves_a_domain`, `test_unresolvable_domain_produces_a_diagnostic` (synthetic). One pre-existing test's hardcoded `"default"` assertion updated to the correct `"ap_clk"`/`"ap_rst"` values (`test_passthrough_demo_resolves_one_module_with_interfaces`) — a deliberate, documented behavior change, not a regression. |
| Full suite | **DONE** | `591 passed, 8 skipped` (+4 over Phase 2's 587), coverage `70.39%`, **0 regressions**. |

### Slice 2 — 3.2a named domains, relationships, CDC/reset-crossing detection, strict gate

Before this slice, nothing anywhere detected a clock- or reset-domain
crossing — confirmed by research: zero CDC code in the tree beyond
forward-looking docstrings and reserved-role warnings.

| Item | Status | Evidence |
|---|---|---|
| Domain relationship declaration | **DONE** | New optional top-level `clock_domains:`/`reset_domains:` design.yml blocks, keyed by the already-resolved net name, each `{derived_from, ratio}` (`forge/topgen/config.py::DesignConfig.clock_domains`/`.reset_domains`, popped/validated in `.load()`). Purely descriptive — does not auto-approve crossings between related domains. |
| CDC approval declaration | **DONE** | New optional `cdc: {kind: 2ff_sync\|async_fifo, depth}` on a `connections:` entry (`Connection.cdc`, `forge/topgen/config.py`), validated inline next to the existing `boundary` validation. Scope: `connections:` only, consistent with `register_stages`/`delay_cycles`/`boundary` already being `Connection`-only. |
| Shared domain resolution | **DONE** | Slice 1's `_resolve_domain_nets` moved out of `forge/ir/build.py` into new public `forge/topgen/ip/domains.py::resolve_domain_nets` (IR-agnostic — no `DiagnosticReference`/model.py dependency), so the IR builder and the new CDC checker can never disagree about a module's domain. `build.py` wraps its `unresolved` output back into the same diagnostic messages slice 1 already produced. |
| CDC checker | **DONE** | New `forge/topgen/ip/cdc.py::verify_cdc(design_cfg, contracts, match_report, conn_map, global_nets)` — shaped like `cardinality.py`'s verifier (only ever `"error"`-severity, no warning tier, matching `verify_cardinality`'s own convention). Flags any wired module-pair resolved to different, both-known clock or reset domains with no matching `cdc:` declaration. One `cdc:` block approves both clock- and reset-crossing for its connection. |
| Strict-gate-only, matching existing convention | **DONE** | Confirmed by reading `topgen.py`: `verify_cardinality`/`verify_topology_groups` are only ever *called* inside `if getattr(args, "strict", False):` — non-strict `gen-top` runs do zero cardinality/topology-group checking today. The new 7th gate (`forge/core/cli/groups/topgen.py`, right after the cardinality gate) follows the identical pattern for CDC. `--strict` help text updated. |
| IR fields (descriptive only) | **DONE** | `ResolvedClockDomain`/`ResolvedResetDomain.derived_from`/`.ratio` (from the new design.yml blocks); `ResolvedConnection.crosses_clock_domain`/`.crosses_reset_domain` (computed from the two endpoints' resolved domains). Enforcement is `verify_cdc`'s job, not the IR builder's — mirrors how cardinality's IR field is descriptive while `verify_cardinality` enforces. `--diff` JSON round-trip updated in `forge/core/cli/groups/inspect.py`. |
| Manual end-to-end verification | **DONE** | Synthetic two-clock-domain fixture: `verify_cdc` flags the undeclared crossing directly; `gen-top --strict` on the same fixture exits 1 with the crossing named in the error, then exits 0 once `cdc: {kind: 2ff_sync}` is added. Real `trigger_demo` (single domain): `gen-top --strict` still completes a full real generation run with exit 0 — the common case stays silent and unaffected. |
| Tests | **DONE** | `test_domains.py` (new, 6 tests); `test_cdc.py` (new, 6 tests); `test_cdc_schema_loading.py` (new, 11 tests — design.yml-level `cdc:`/`clock_domains:` parsing/validation); 2 new CLI tests in `test_topgen_cli_commands.py` (undeclared crossing fails `--strict`, declared adapter passes); 2 new tests in `test_ir_build.py` (domain relationship + crossing flag on a synthetic fixture, real `passthrough_demo` never flags same-domain connections). |
| Full suite | **DONE** | `618 passed, 8 skipped` (+27 over slice 1's 591), coverage `71.17%`, **0 regressions**. |

### Slice 3 — 3.2b CDC synchronizer RTL (Verilog only, real)

A real, working 2-flop synchronizer for `cdc: {kind: 2ff_sync}` connections
— the first slice to touch actual generated RTL content since Phase 1's
migration steps. `async_fifo` stays declarable/structurally-approved only,
with no FIFO RTL body — an explicit, visible limitation, not a silent gap.

| Item | Status | Evidence |
|---|---|---|
| Real `cdc_sync2ff` RTL support file | **DONE** | `plugins/trigger_demo/algo/rtl/cdc_sync2ff.v` — a correct, standard double-flip-flop synchronizer (both stages clocked by the *destination* domain, synchronous reset), same doc-comment/parameter style as the existing `RegisterStage.v`/`signal_delay.v` in the same directory. Documents its own single-bit/independently-meaningful-bits scope honestly (not a coherent multi-bit bus transfer — that needs `async_fifo`, not yet implemented). |
| Generator emission | **DONE** | `forge/topgen/generators/structural_verilog.py::write_structural_verilog` gained an optional `match_report` parameter; builds a `cdc_map` (mirrors the existing `reg_stages_map`/`delay_cycles_map`/`boundary_map` pattern) and, for every `cdc: {kind: 2ff_sync}` connection, emits a `cdc_sync2ff` instance per pin-pair, wired to an intermediate `sync_net_*` wire that the destination instance's port-mapping then consumes (same intermediate-wire pattern as `reg_net`/`delay_net`) — a 2ff_sync declaration takes priority over any also-declared delay/register stage on the same connection, since a domain crossing needs the synchronizer regardless. |
| Destination-domain clock/reset resolution | **DONE** | Reuses `forge.topgen.ip.domains.resolve_domain_nets` (the same resolver the IR and `verify_cdc` use) to find the *destination* instance's own clock/reset net — not always `ap_clk`/`ap_rst`. |
| Real bug found and fixed during manual verification | **DONE** | `resolve_domain_nets`'s domain identity is the raw port name verbatim (e.g. `"rst"`), but the generator's own pre-existing clock/reset auto-map silently aliases every standard-name variant (`clk`/`clock`/`ap_clk`, `rst`/`reset`/`ap_rst`/`rst_n`, and `_clk`/`_rst`-suffixed names) onto the single literal `ap_clk`/`ap_rst` top-level port. A first implementation emitted `.dst_rst(rst)` — a reference to a net that doesn't exist in the generated file (no top-level port or wire is ever literally named `rst`) — caught immediately by manually reading the generated Verilog, not by a passing-too-easily test. Fixed with a new `_domain_to_top_level_net()` translation helper applying the identical aliasing rule; regression test added (`test_standard_reset_name_variant_maps_to_ap_rst_not_raw_name`). |
| `async_fifo`: honest limitation | **DONE** | Wired directly (same as no adapter at all) with a visible `// NOTE: ... FIFO RTL generation is not implemented yet ...` comment emitted directly into the generated file itself — not just in docs, so anyone reading the RTL sees the limitation where it matters. |
| `cdc:` declared without `match_report` | **DONE** | Raises a clear `ValueError` rather than silently guessing or defaulting — consistent with this codebase's existing "never guess destructively" discipline (e.g. `forge/topgen/migrate.py`'s ambiguous-pattern refusals). |
| Backward compatibility | **DONE** | `match_report` is optional (default `None`); a design with no `cdc:` declarations needs no code changes and no new parameter — confirmed by manual byte-identical regeneration of the real `trigger_demo` design (single clock/reset domain, declares no `cdc:`) against its slice-2 output. |
| Build-manifest inclusion | **DONE** | `forge/core/cli/groups/topgen.py::generate_build_manifest` gained a `needs_cdc_sync2ff` check, same search-and-include pattern as `RegisterStage.v`/`signal_delay.v`/`slr_crossing_delay.v` (search `project_root`/`ip_root`/`manifest_output.parent` for the file by name; warn if declared-but-not-found). |
| Tests | **DONE** | `test_cdc_generation.py` (new — 8 tests): synchronizer emission with correct destination-domain wiring, the standard-name-alias regression test above, non-standard clock names staying their own net, `async_fifo`'s direct-wire-plus-note behavior, the `match_report`-required error, backward compatibility with no `cdc:` declared, build-manifest inclusion (present when declared, absent when not), and a real-`trigger_demo` regression guard. |
| Full suite | **DONE** | `626 passed, 8 skipped` (+8 over slice 2's 618), coverage `71.50%`, **0 regressions**. |

### Slice 4 — 3.3 transformation-kind taxonomy

`ResolvedTransformation.kind` was `'register'|'delay'` only. Expanded to
the full release-plan §3.3 taxonomy — real, generated-or-declared
instances for most kinds, honestly documented vocabulary-only reservations
for the rest (same precedent as `clock_secondary`/`reset_secondary`).

| Item | Status | Evidence |
|---|---|---|
| `IR_SCHEMA_VERSION` bump | **DONE** | `"0.1.0"` → `"0.2.0"` (`forge/ir/model.py`) — a breaking rename of IR schema *values* (`register`→`pipeline_register`, `delay`→`latency_delay`), same pre-1.0 minor-bump convention already used for this version family. Three tests with a hardcoded `"0.1.0"` literal updated. |
| `pipeline_register`/`latency_delay`/`slr_crossing` | **DONE** | `forge/ir/build.py`'s `module_transforms` construction: `register_stages`→`pipeline_register` (renamed only); `delay_cycles` with no `boundary`→`latency_delay` (renamed only); `delay_cycles` **with** `boundary`→`slr_crossing` (not both — the generator emits exactly one RTL instance, `slr_crossing_delay` not `signal_delay`, for this case, and the IR now matches that reality), carrying the real boundary string in a new `ResolvedTransformation.tag` field. |
| `cdc_synchronizer`/`async_fifo` | **DONE** | From `Connection.cdc` (slices 2/3) — `kind: 2ff_sync`→`cdc_synchronizer`, anything else→`async_fifo`. `async_fifo` appears as a genuine transformation object even though its RTL body isn't generated — the declaration/approval is real; code generation is the documented limitation, not the IR fact. |
| `fanout` | **DONE, with a real-design validation** | Computed generically in `assemble_project_ir` (no new schema) — a post-pass grouping the already-built connection list by `(producer.instance_id, producer.port)`, excluding `"$external"` producers (clock/reset fan-out is universal, not a meaningful signal). **Verified on the real `trigger_demo` design**: `dec_0`..`dec_3`'s `decoded_hit`/`decoded_valid` outputs each already drive two consumer connections today — exactly 8 producer groups / 16 connections, confirmed both by direct computation and by the new test, before any code was written (real usage discovered, not fabricated). |
| `gather_scatter` | **Deferred to slice 5, documented not fabricated** | The classification already exists transiently in `topology_deriver.py`'s matching but is discarded before reaching `MatchReport` — surfacing it needs the matcher plumbing slice 5 (3.5) does anyway. Zero live instances this slice; stated in `ResolvedTransformation`'s docstring, same as the two items below. |
| `width_adapter`/`protocol_adapter`/`constant_source` | **Vocabulary only, zero live instances — documented, not fabricated** | Confirmed nothing in the matcher or either generator ever inserts a width/protocol adapter or a non-zero constant source today. Defined in the kind vocabulary (satisfies "represent separately") with the same "reserved, no effect yet" framing as `clock_secondary`/`reset_secondary` (`docs/IP_INTERFACE_POLICY.md` "Reserved roles"). |
| `tie_off` | **DONE** | New `"$tie_off"` producer sentinel (mirrors the existing `"$external"` convention exactly) — a tied-to-zero port has no real producer in `conn_map` at all. New public `forge/ir/build.py::build_tie_off_connections(tied_to_zero)`, called from `cmd_gen_top` right next to where `top_ports` is already attached, for **both** verilog and vhdl modes (confirmed both generators return `report["tied_to_zero"]`; `write_bd_tcl` doesn't, so bd mode correctly gets none — same honest asymmetry `top_ports` already has). Absent from `forge inspect`'s pre-generation IR, present only in `gen-top`'s emitted `design.ir.json` — same timing as `top_ports`. `forge/ir/project.py::project_to_conn_map` updated to skip `"$tie_off"` producers (defensive correctness for any future reload of a `design.ir.json` containing tie-offs). |
| Manual end-to-end verification | **DONE** | Synthetic design with an unconnected RTL input: real generated Verilog ties it `8'd0` and the emitted `design.ir.json` carries a real `tie_off` connection with the exact expected shape. Both `trigger_demo` and (implicitly, same code path) `passthrough_demo` regenerate **byte-identical** Verilog to slice 3's output — this slice only changes `design.ir.json` content, never generated RTL. |
| Tests | **DONE** | `test_ir_build.py` (+9): kind renames, `slr_crossing`'s `tag`, `cdc_synchronizer`/`async_fifo`, synthetic fan-out, **real-`trigger_demo` fan-out** (asserts the exact 16-connection/8-producer set), `build_tie_off_connections` (2 tests), `project_to_conn_map` tie-off skipping. `test_topgen_cli_commands.py` (+1): full CLI round-trip proving a real tied input produces the exact expected `design.ir.json` connection shape. |
| Full suite | **DONE** | `636 passed, 8 skipped` (+10 over slice 3's 626), coverage `71.78%`, **0 regressions**. |

### What's left in Phase 3

- Slice 6 (3.4 — generation plan / `forge build`) — not yet started (see
  the phase plan). Slice 5 (below) closes 3.5.

---

## Phase 3 (slice 5) — 3.5 matching-evidence expansion

For every automatically resolved connection, preserve producer/consumer/
matching-keys/coordinates/protocol/width/cardinality-result/clock-domain-
result/selected-strategy/rejected-candidates-and-reasons, available to
diagnostics and JSON output. Investigated first (`forge/topgen/ip/matcher.py`'s
every silent-skip site enumerated before writing code); a deliberately
prioritized, honestly-scoped subset was instrumented — see "What's
deferred" below.

| Item | Status | Evidence |
|---|---|---|
| `MatchReport.rejected_matches` (semantic rejections with no wire, so no `ResolvedConnection` to attach `rejected_fanin`-style evidence to) | **DONE** | New `RejectedMatch` dataclass (`forge/topgen/ip/matcher.py`). `_derive_from_contracts` now returns `(pairs, rejections)` instead of just `pairs` — instrumented at its 3 semantic-skip sites: no destination role for a `wiring_kind`, ambiguous (>1 role sharing a `wiring_kind` on either side), and a previously-*silent* role-`kind` mismatch (`nd_tpl` vs `prefix_array` etc. — the pre-existing `if/elif` had no `else`, so this case produced literally nothing, not even a `continue`, before this slice). The bulk auto-match block (bare `for sg in src_grps.values(): for dg in dst_grps.values(): ... break`) gained a `for...else` recording "no destination group matched shape (count/width/type)" when no `dg` satisfies the inner loop. |
| Deliberately deferred (documented, not instrumented) | **NOT DONE — scoped out, not fabricated** | Geometric slot/offset-mismatch `continue`s in the per-instance and topology-group replication loops (positional, not semantic — a human reading a slot-alignment failure needs the geometry, which a generic reason string wouldn't add value over), and `_groupify`'s structural exclusion of `ap_*`/protocol-suffix/user-declared-external ports (never candidates at all, excluded by design on every module — not a "lost match"). |
| `gather_scatter` classification surfaced | **DONE** | `topology_deriver.py::_expand_role_pair`'s gather branch (`scalar`→`prefix_array`) previously returned `meta=None`, asymmetric with scatter's existing `{"kind": "scatter", "target_instance": i}` tag — now returns `{"kind": "gather", "target_index": i}`. Verified inert to wiring: gather's instance selection was already fully determined by the pre-existing `slot` value, not `meta`; a dedicated test asserts identical `(src, dst, slot)` triples before/after. New `MatchReport.gather_scatter_evidence: Dict[4-tuple, str]` (separate dict, not overloading `connection_evidence`'s existing `str`-valued type) populated in the topology-group replication loop. `forge/ir/build.py` looks this up per connection and — this is what finally gives `gather_scatter` a real, non-zero `ResolvedTransformation` instance, closing the gap the taxonomy (slice 4) explicitly deferred. |
| Real-design validation: gather | **DONE** | `plugins/trigger_demo`'s `decoder_to_collector` topology group (`dec_0..dec_3`'s `decoded_hit`/`decoded_valid` → `col`'s `in_hit_N`/`in_valid_N` prefix arrays) is a real gather pattern — computed independently before writing the test (expected exactly 8 connections: 4×`decoded_hit` + 4×`decoded_valid`), confirmed both by direct computation and `test_ir_build.py::test_trigger_demo_real_gather_scatter_evidence`. |
| Scatter: honestly scoped | **Synthetic only, documented as such** | No `prefix_array`→`scalar` occurrence exists in either reference design — proven only via a synthetic fixture (`test_ir_build.py::test_matching_evidence_synthetic_scatter`), explicitly not conflated with gather's real-design proof. |
| Per-connection `MatchingEvidence` (`forge/ir/model.py`) | **DONE** | New nested dataclasses `MatchingEvidence`, `RejectedCandidate`, `CardinalityCheckResult`; one new field `ResolvedConnection.matching_evidence: Optional[MatchingEvidence]`. Every sub-field is `Optional`/empty-default and left unset when the underlying contract role/bound doesn't exist for that side (handshake pins, no-contract modules, `$external`) — never fabricated. `producer_width`/`consumer_width` deliberately kept as two separate fields, not one — no `width_adapter` exists, so a mismatch is a real, previously-silent fact this now surfaces instead of hides. |
| Sourcing (`forge/ir/build.py::assemble_project_ir`) | **DONE** | Coordinates/protocol/wiring_kind: a new `port_index_by_module` reverse index (physical port name → owning interface/member, built once per module right after `_build_interfaces`, expanding `scalar`/`prefix_array`/`nd_tpl` bindings — `nd_tpl` expansion reuses `matcher._expand_nd`, same cross-module private-import precedent `topology_deriver.py` already established). Width: sourced from `ip_info_data` directly (the authoritative per-physical-port source), not from interface metadata. Cardinality result: reuses `forge/topgen/ip/cardinality.py`'s `Bound`/`parse_cardinality` machinery already imported into `forge/ir/`; producer/consumer wiring counts precomputed once (not per-connection) from `connection_evidence` + `rejected_fanin`, using the exact same counting rule `verify_cardinality` itself applies (proven by a dedicated test whose `actual_count` matches what `verify_cardinality` would compute). `cdc_declared`: sourced from the same `for c in cfg.connections:` pass that already builds `module_transforms`. Purely descriptive — nothing here feeds `project_to_conn_map` or any generator. |
| `--diff`/`--explain-staleness` round-trip | **DONE** | `forge/core/cli/groups/inspect.py::_project_from_json` reconstructs `matching_evidence` field-by-field (new `_matching_evidence()` helper) — the exact bug class hit and fixed three times before this field existed (`emission_order`, `top_ports`, `protocol`). Verified: `forge inspect --emit-ir` → `--diff` against itself on real `trigger_demo` reports `hash_equal: True` and zero connection changes, with `matching_evidence` actually populated on 8+ connections in the emitted JSON (not a vacuous pass). Test: `test_inspect_cli_group.py::test_inspect_diff_round_trips_matching_evidence`. |
| Byte-identical regeneration | **DONE** | The pair-construction logic in `matcher.py` (`lst.append((s_pin, d_pin))`, `pairs.append(...)`) is untouched by this slice — only new bookkeeping (`rejected_matches`, `gather_scatter_evidence`) was added alongside it, and `topology_deriver.py`'s gather-branch change is proven inert to wiring order (see above). `test_generation_ir_equivalence.py`'s existing byte-identical proofs (direct `conn_map` vs. IR-projected, real `write_structural_verilog`/`write_structural_vhdl`/`write_bd_tcl` calls on both reference designs) pass unchanged; manually re-ran the same generation path standalone for both designs post-slice, confirming real, non-empty output with zero divergence between the direct and IR-projected paths. |
| Tests | **DONE** | `test_matcher_connection_evidence.py` (+6): `_derive_from_contracts` unit tests for each rejection reason plus one success case, an `auto_match_ports` integration test proving `module_pair` gets filled in by the caller, and the bulk-auto-match no-shape-match case. `test_topology_deriver.py`: `test_gather_scalar_to_array` updated for the new symmetric meta tag (deliberate, documented behavior change — not a regression). `test_ir_build.py` (+5): real-`trigger_demo` gather count/IDs, no-crash full-connection-set iteration, width-mismatch surfacing, cardinality-result-unsatisfied (two producers vs. a max=1 bound), synthetic scatter. `test_inspect_cli_group.py` (+1): round-trip proof described above. `test_topgen_cli_commands.py`: one pre-existing exact-dict-equality assertion updated to include the new `matching_evidence: None` key (tie_off connections correctly don't get sourced evidence — that path doesn't go through `assemble_project_ir`'s conn_map loop). |
| Full suite | **DONE** | `648 passed, 8 skipped` (+12 over Phase 3 slice 4's 636), coverage `61.67%` (up from `61.40%` immediately pre-slice), **0 regressions**. |

### What's left after slice 5

- Rejected-candidate detail for the geometric/positional skip sites and
  `_groupify`'s structural exclusions — explicitly deferred (see table
  above), not silently dropped.

---

## Phase 3 (slice 6) — 3.4 Generation plan / `forge build`

A deterministic generation-plan artifact and `forge build --plan/--apply/
--accept-plan-hash`, built entirely as a reshape of already-computed IR/
matching facts — no new matching, generation, or analysis logic. Slice 5's
`matching_evidence`/`rejected_matches` feed this slice's "matching
evidence" plan section directly, confirming the dependency ordering
recorded above.

| Item | Status | Evidence |
|---|---|---|
| `compute_gen_top_plan` extraction | **DONE, behavior-preserving** | New `GenTopPlanContext` dataclass + `compute_gen_top_plan(cfg, design_path, args, *, read_only, emit_progress=True)` (`forge/core/cli/groups/topgen.py`) — pure code motion of `cmd_gen_top`'s path/contract/ip_info-resolution/matching compute phase, `sys.exit`-free (raises ordinary exceptions, propagated by the caller's own unchanged `try/except`). The two "can't even start" failure modes (missing design file, validation errors) deliberately stay in `cmd_gen_top` itself, with their existing custom print+exit messages untouched — `compute_gen_top_plan` takes an already-loaded, already-validated `cfg`, so there's no raise-vs-custom-print mismatch to reconcile. `cmd_gen_top` now calls this function and unpacks the returned context back into the exact same local variable names its unchanged strict-gate/dry-run/mode-branch code already expected — **zero lines of the mode branches (vhdl/verilog/bd generation) or the dry-run preview were touched**. |
| `read_only` generalizes the existing dry-run ip_info path | **DONE** | `read_only=True` reuses the exact in-memory-only ip_info path `--dry-run` already had (never writes `ip_info.yaml`); `read_only=False` preserves the real write+reload behavior exactly. `cmd_gen_top` calls `compute_gen_top_plan(..., read_only=getattr(args, "dry_run", False))` — a direct generalization of the boolean it already branched on, not a new decision. |
| 3 structured issue lists always computed | **DONE** | `topology_group_issues`/`cardinality_issues`/`cdc_issues` are computed unconditionally inside `compute_gen_top_plan` (previously only computed lazily inside `cmd_gen_top`'s own `if args.strict:` blocks) — safe because `verify_topology_groups`/`verify_cardinality`/`verify_cdc` are pure, non-raising, side-effect-free functions (confirmed by inspection: each just builds and returns a plain issue list, same convention as every other `contract_verifier`-family check). `cmd_gen_top`'s own strict-gate `sys.exit(1)` blocks are **completely unchanged** — same condition, same print, same exit code — just reading `ctx.topology_group_issues` etc. instead of a locally-lazy call. |
| Pre-generation `ResolvedProject` always built | **DONE, one real behavior addition — proven inert** | `compute_gen_top_plan` always calls `assemble_project_ir` (previously only reached from the non-dry-run mode branches). Confirmed side-effect-free (same guarantee `forge inspect` already relies on for arbitrary/partially-unresolved designs — graceful degradation via diagnostics, never a crash). `cmd_gen_top`'s own mode branches keep their own independent `assemble_project_ir` + `project_to_conn_map` calls **completely untouched** — `ctx.project` is unused by `cmd_gen_top` itself, only by `forge build`. |
| `forge/ir/plan.py` (new) | **DONE** | `PLAN_SCHEMA_VERSION`, `PlannedConnection`, `PlannedTransformation`, `UnresolvedIssue`, `GenerationPlan` dataclasses; `build_generation_plan(project, match_report, *, topology_group_issues, cardinality_issues, cdc_issues, compat_mode_modules, output_artifacts)` — pure reshape, no new computation. `inferred_connections`/`explicit_connections` classify by the existing `wiring_method` vocabulary (`{auto_match, topology_group}` vs `{contract_wiring, port_map, port_map_ranges}`; global clock/reset nets — `heuristic`/`None` — are in neither bucket, reflected via `compat_mode_modules` instead, matching the release-plan text's own scope). `latency_changes` is an explicitly-descriptive filtered view of `generated_transformations` (kinds `pipeline_register`/`latency_delay`/`slr_crossing`) — **not** a new latency engine; `forge analyze latency-check` remains the real tool. `matching_evidence` folds in per-connection `MatchingEvidence` (slice 5) plus `MatchReport.rejected_matches` under an `"unmatched_candidates"` key. `unresolved_issues` folds in all 3 structured issue lists plus `ResolvedDesign.diagnostics` — no new checks invented. Lives at the IR layer (not inside `build.py`'s CLI group), same separation rationale as `serialize.py`/`provenance.py`; passes `ci/import_direction_check.sh` unchanged. |
| `plan_hash` | **DONE** | `_canonical_plan_json` (json.dumps with sorted keys, excluding `generated_from` — same exclusion rationale as `serialize.py::_canonical_design_json` excluding `ResolvedProject.generated_from`) + `forge.core.utils.content_hash.hash_bytes` (the existing generic sha256 primitive — no new hashing logic). Manually verified deterministic (same plan → same hash across two separate CLI invocations) and portable (`generated_from`'s absolute paths don't affect the hash). |
| `forge/core/cli/groups/build.py` (new) | **DONE** | `cmd_build`/`register`, registered as a single top-level command in `forge/core/cli/main.py` (same pattern as `doctor`/`inspect` — no nested subgroup). `--plan` (default-true, exists for symmetry with `--apply`), `--apply`, `--accept-plan-hash <hash>`, `--json`, plus the path-resolution flags `gen-top` already has (`--contracts-from`, `--ip-info`, `--build-dir`, `--hls-build-root`, `--src-root`, `--ip-root`, `--system`, `--consumer-root`, `--rtl-resource-root`, `--mode`, `--output`, `--top-name`). **Deliberately smaller flag surface than `gen-top`** — no `--bd-name`/`--gen-testbench`/`--xml-stimulus`/`--lint` knobs, a documented v1 scoping choice (see the module's own docstring), not an oversight; `--apply`'s delegation fills in `gen-top`'s own defaults for these. |
| `--apply` reuses 100% of `gen-top`'s write logic | **DONE** | `_gen_top_namespace_from_build_args` builds an `argparse.Namespace` from `forge build`'s own parsed args (`dry_run=False`, plus the gen-top-only defaults above) and calls `topgen.cmd_gen_top(ns)` directly — no separate writer in `build.py`. `cmd_gen_top` has no explicit success exit (falls through on success), so `forge build --apply`'s own exit code *is* `cmd_gen_top`'s; any internal `sys.exit`/exception propagates unchanged. Manually verified on real `passthrough_demo`: `forge build --apply`'s `algo_top.v` is **byte-identical** to `topgen gen-top`'s direct output; `test_build_cli_group.py::test_apply_produces_byte_identical_artifacts_to_gen_top` automates the same proof. `--accept-plan-hash` is checked **before** `--apply` delegates (a mismatch blocks the real write) — `test_apply_respects_accept_plan_hash_before_writing` proves zero files are written when the hash check fails. |
| CI scoping | **Deliberately minimal — flag/exit-code contract only** | `forge build design.yml --accept-plan-hash <hash> --json` is a normal CLI invocation any CI runner can already call and check the exit code of (mirrors `doctor --strict`'s existing usage shape) — no CI-specific pipeline config, no GitHub Actions scaffolding invented. |
| Real-design validation | **DONE** | Manually ran `forge build` (`--plan`, `--json`, `--accept-plan-hash` matching/mismatching, `--apply`) against real `plugins/trigger_demo`/`plugins/passthrough_demo`: `trigger_demo`'s plan correctly reports 16 inferred + 29 explicit = 45 connections (matches the design's known connection count), 30 transformations (6 latency-bearing), 31 connections carrying matching evidence, 2 unresolved issues (the same 2 diagnostics `forge inspect` already reports), plan hash stable across repeated invocations. `--apply` on `passthrough_demo` produces the full real artifact set (`algo_top.v`, `build_manifest.json`, `port_map.yaml`, `design.ir.json`, etc.), byte-identical to `gen-top`'s direct output except the expected non-semantic `source_file` path in `port_map.yaml`. |
| Extraction proven behavior-preserving | **DONE, via the existing regression suite, not a new synthetic diff** | `compute_gen_top_plan`'s extraction touches the exact code range `test_topgen_cli_commands.py` already exercises extensively (dry-run invariants, all 3 mode branches' real generation, `--strict` gating on all 7 checks, the tie-off connection test, the "gen-top's IR matches a fresh inspect" equivalence test) — every one of those tests passed **unmodified** after the extraction, which is the behavior-preservation proof: had the refactor changed any observable output, print ordering, or exit code, those pre-existing byte-exact assertions would have caught it. (A separate git-stash-based before/after diff was considered and rejected as unsafe — this repository's working tree carries many prior sessions' uncommitted work as untracked/tracked diffs simultaneously, and a partial stash was confirmed hands-on to produce an inconsistent tracked/untracked hybrid state, not a clean baseline.) |
| Tests | **DONE** | `test_ir_plan.py` (new — 8 tests, synthetic fixtures, no CLI): inferred/explicit classification, transformation/latency-change filtering, matching-evidence + rejected-matches folding, diagnostics-to-issues folding, hash determinism/sensitivity/portability. `test_build_cli_group.py` (new — 10 tests): registration, zero-file-write invariant for `--plan` (tree-snapshot technique), human output section presence, JSON output shape cross-checked against real `trigger_demo` connection counts, hash determinism across two CLI invocations, `--accept-plan-hash` matching/mismatching, missing-design guidance, `--apply` byte-identical-to-`gen-top` proof, `--accept-plan-hash` blocking `--apply` before any write. |
| Full suite | **DONE** | `667 passed, 8 skipped` (+19 over Phase 3 slice 5's 648: 8 in `test_ir_plan.py`, 10 in `test_build_cli_group.py`, +1 net from the extraction exercising slightly more of `cardinality.py`/`cdc.py`/`contract_verifier.py` even outside `--strict`), coverage `62.32%` (up from `61.83%`), **0 regressions**. |

### What's left after slice 6

- `forge build --plan`'s `unresolved_issues` cannot predict `_strict_port_gate`'s
  open-output/tied-input violations — those need a real generator report,
  which only exists after a write. Only `--apply` can surface them (same
  timing asymmetry `tie_off`/`top_ports` already have in the IR). Documented,
  not fabricated.
- Rejected-candidate detail for the geometric/positional skip sites (slice
  5's own deferred item) is therefore also absent from `forge build`'s
  matching-evidence section — inherits the same, already-documented scope
  boundary.
- No graphical/visual explorer — out of scope per the audit, unchanged.
- No CI *pipeline* — only the `--accept-plan-hash` flag/exit-code contract,
  by design (see table above).

**Phase 3 ("Clock, reset, timing, and transformations") is now complete —
all 6 slices done.**

---

## Next recommended session (not started here)

Phase 3 is complete. Remaining, unrelated to Phase 3:

- `generate_build_manifest`'s resolved-build-path gap (unchanged from
  before Phase 3).
- mtime-based staleness migration onto content-hash provenance for
  `gen-top`/`verify` (Phase 1 slice 2's provenance manifest remains a
  parallel capability, not yet the migration target).
- Design explorer (Phase 8) — now meaningfully unblocked by slice 5's
  matching evidence and slice 6's generation plan, but still a
  substantial, separate multi-session effort (static SVG + interactive
  HTML + explorer functionality, per the release plan's own phase
  breakdown).
- MkDocs site, unrelated-domain reference project, property-based tests,
  plugin terminology rename — all previously deferred, still open.
