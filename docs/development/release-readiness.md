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

### What's left after slice 6 (Phase 3)

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

## Phase 4 (slice 1) — 4.1 Separate latency sources + provenance

Started per the release plan's own Phase 4 ordering (§4.1 first — §4.2's
`kind: fixed/bounded/elastic` vocabulary and §4.3's alignment inference
both need §4.1's provenance-tagged wrapper to build on; §4.5's
static/runtime unification needs it to honestly name "source of each
prediction"). Investigated first: read `forge/analyze/latency_static/graph.py`,
`checker.py`, `reporter.py` in full before writing code, confirming two
real gaps — `LatencyEdge` carried zero latency of its own (the checker was
provably blind to `register_stages`/`delay_cycles`), and provenance was a
narrow 4-value free-text string (`"explicit"|"hls_report"|"hint"|"unknown"`)
computed only for module nodes, never edges.

| Item | Status | Evidence |
|---|---|---|
| Shared `LatencyValue`/`LatencyProvenance` wrapper | **DONE** | New `forge/analyze/latency_model.py` — `LATENCY_SOURCES` (the 6 release-plan §4.1 values: `explicit_contract`/`hls_report`/`generated_transformation`/`inferred`/`user_hint`/`runtime_observation`), `LATENCY_KINDS` (the 3 §4.2 values — defined now so slice 2 doesn't need to touch this module), `LatencyProvenance`/`LatencyValue` dataclasses, both validated at construction (`LatencyModelError`). Deliberately its own module (not nested in `latency_static/`) so `latency_runtime` (slice 5) can depend on it without a wrong-direction package dependency between sibling analysis packages. |
| `LatencyNode`/`LatencyEdge` refactor | **DONE, zero touch to RTL-facing code** | `forge/analyze/latency_static/graph.py`: `LatencyNode.latency_cycles`/`.latency_source` replaced by `latency: Optional[LatencyValue]`, with back-compat `@property` accessors reading through it (safe — this dataclass is never serialized; `latency-check` has no `--json`/`--format` flag). `LatencyEdge` gains `latency: Optional[LatencyValue] = None`. Nothing in `forge/topgen/config.py::Connection` or `forge/ir/model.py::ResolvedTransformation` was touched — this is an analysis-side-only refactor. |
| Connection latency folded into edges (the actual gap fix) | **DONE** | New `_edge_latency_from_connection(conn)` (`graph.py`): sums `register_stages` + `delay_cycles` (+2 for a declared `cdc: {kind: 2ff_sync}`, the real fixed depth of `cdc_sync2ff.v`) into one `LatencyValue`, provenance `generated_transformation`, populated in `_build_graph_from_ir`'s connection loop. `async_fifo`/topology-group-derived edges honestly stay `latency=None` (no RTL exists for `async_fifo`, `TopologyGroup` has no cycle-count field at all) — not a fabricated 0. `_resolve_latency()` extended to also build a `LatencyProvenance` (`explicit`→`explicit_contract`, `hls_report`→`hls_report`, `hint`→`user_hint`), shared unchanged by both the IR-driven and legacy raw-YAML paths (same function, same precedence, richer return type — `Optional[LatencyValue]` instead of a `(cycles, source_str)` tuple). |
| Checker path-sum fix | **DONE** | `checker.py::check_merge_points`: `PathLatency.total_cycles` previously read only the predecessor *node's* latency; now adds the connecting *edge's* `.latency.cycles` (0/None-safe) into the total, tagging the result `PathLatency.provenance = LatencyProvenance("inferred", detail=...)` — a real, justified use of `inferred` (the sum of ≥2 sourced values, not itself sourced from one place). This directly closes the confirmed "checker blind to register/delay cycles" gap. |
| Reporter visibility | **DONE, deliberate output change** | `reporter.py::render_markdown`: merge-point path table gains a "Source" column so the fold-in is visible in rendered output, not just internal state. `test_trigger_demo_report_is_byte_identical` (new-vs-legacy-path comparison, not a fixed golden file) needed no fixture update — both paths render through the same updated function and currently hit zero merge points on trigger_demo, so the new column is inert on that specific comparison; two other pre-existing hardcoded `latency_source == "hint"` assertions (`test_build_graph_dispatches_to_ir_path_by_default`, `test_build_graph_falls_back_to_legacy_for_a_genuinely_different_override`) were updated to `"user_hint"` — a deliberate, documented vocabulary change, not an accidental regression. |
| Real-design validation | **DONE** | `trigger_demo`'s real `col→trig` connection (`design.yml:65-67`, `register_stages: 2`) and `trig→tfan` (`:73-75`, `delay_cycles: 3`) now show up as real `LatencyEdge.latency` values with `generated_transformation` provenance — verified both by a new automated test and by manually running `forge analyze latency-check` end-to-end (rendered Markdown confirmed). Zero merge points exist in either reference design today (module-group node granularity collapses trigger_demo's real `dec×4→col` gather — confirmed unchanged before/after this slice; per-instance granularity that would surface it lands in Phase 4 slice 3, not this one) — a new regression-guard test asserts this explicitly rather than leaving it implicit. |
| Tests | **DONE** | `test_latency_model.py` (new, 7 tests): source/kind vocabulary completeness, provenance/value construction and rejection. `test_latency_checker.py` (new, 4 tests): the provable regression-closer (equal node latencies + unequal edge latencies → now flagged, where the pre-slice checker would have missed it), a balanced-via-different-node/edge-split case (proves the fold-in isn't over-eager), an unknown-edge-defaults-to-zero-not-unknown case, and the zero-real-merge-points regression guard on both reference designs. `test_latency_graph_ir_equivalence.py` (+1 new, 2 updated): real `col→trig`/`trig→tfan` edge-latency assertions against actual `design.yml` values, plus the two vocabulary-rename fixes above. |
| Full suite | **DONE** | `679 passed, 8 skipped` (+12 over Phase 3 slice 6's 667: 7 in `test_latency_model.py`, 4 in `test_latency_checker.py`, 1 in `test_latency_graph_ir_equivalence.py`), coverage `63.26%` (up from `62.32%`), **0 regressions**. `test_generation_ir_equivalence.py`'s byte-identical RTL proofs re-ran unchanged — confirms nothing RTL-facing was touched, as designed. |

### What's left after Phase 4 slice 1

- Slice 2 (§4.2 — `kind: fixed/bounded/elastic` schema), slice 3 (§4.3 —
  alignment-aware merge-point checking + the per-instance edge granularity
  needed to make trigger_demo's real gather a genuine merge point), slice
  4 (§4.4 — throughput info), slice 5 (§4.5 — static/runtime unification)
  — not yet started, in that dependency order (see
  `docs/plan/FORGE_release_plan.md` §4 and this session's planning notes).
- Honest deferral list carried forward from planning (not yet reached):
  `buffering_capacity`/`occupancy`/`backpressure`/`frame_rate` (zero data
  source anywhere), `ControlSignalTarget.delay_cycles` (a third,
  structurally distinct connection-latency mechanism incompatible with
  the module-pair edge model), `design_parameters.py`'s naive DAG-blind
  cumulative-latency walker (to be cross-referenced, not replaced),
  `async_fifo` CDC latency (no RTL, stays unknown).

---

## Phase 4 (slice 2) — 4.2 Fixed, bounded, and elastic timing

`latency: {kind: fixed|bounded|elastic, ...}` — a structured, richer
alternative to the flat `latency_cycles`/`latency_hint`/`variable_latency`
keys, coexisting with them (not replacing). Real bug found and fixed
during real-design validation, not just synthetic testing: `_load_registry`
in `forge/topgen/config.py` (used by the primary, non-legacy `DesignConfig`
load path) filters every registry entry down to a hardcoded
`IDENTITY_FIELDS` allowlist — the new `latency:` key wasn't in it, so it
was silently stripped before ever reaching `_pop_timing`, and the real
`trigger_demo` migration below (module `trig`) initially lost its timing
entirely (`Module.timing is None`) until this was caught by the full
suite and fixed. A second, related gap fixed in the same pass: the
legacy raw-YAML fallback path (`forge/analyze/latency_static/graph.py`'s
`_build_latency_map`) didn't know about `latency:` blocks at all, which
would have made `trigger_demo`'s own `test_trigger_demo_nodes_and_edges_equivalent`
regression test (new IR path vs. legacy path) correctly catch a real
precedence divergence — fixed by sharing one `_latency_value_from_declared_kind`
helper between both paths, same "define the rule once" discipline
`_resolve_latency` already established in slice 1.

| Item | Status | Evidence |
|---|---|---|
| `LatencyDeclaration` schema | **DONE** | New dataclass in `forge/topgen/config.py`: `kind: fixed\|bounded\|elastic`, `cycles`/`min_cycles`/`max_cycles`. `__post_init__` defense-in-depth validation (unknown kind; `fixed` without `cycles`; `bounded` missing bounds or `min>max`; `elastic` with stray cycle fields) — same "raw exception as first-layer defense, structured `RegistryValidator` error as the nicer user-facing layer" precedent already established for `ModuleTiming`'s own contradiction check. |
| Coexistence with flat fields | **DONE** | `ModuleTiming.latency: Optional[LatencyDeclaration] = None`, additive. `_pop_timing()` raises when a module declares **both** a `latency:` block and any of the three flat keys — checked against the *original* raw YAML keys before any normalization runs (not against the constructed `ModuleTiming`'s fields), specifically so the elastic→`variable_latency` soft-migration below doesn't spuriously self-trigger the contradiction check it was almost written to include. |
| `latency: {kind: elastic}` ↔ `variable_latency: true` | **DONE (soft-migration alias)** | `_pop_timing()` sets `variable_latency=True` under the hood when `kind: elastic` is given — every existing `variable_latency` consumer (the checker's `is_variable` unknown-folding) keeps working with zero changes, while the richer `.latency` data is also available for future consumers. Not a rename — `variable_latency: true` stays fully legal forever. |
| `_validate_latency_block` | **DONE** | `forge/topgen/validation.py`, parallel to the existing `_validate_timing_fields`, called alongside it. `"latency"` added to `_KNOWN_MODULE_KEYS`. Validates: known `kind`; unknown keys inside the block; `fixed` requires `cycles` (and rejects stray `min_cycles`/`max_cycles`); `bounded` requires both bounds, integer-typed, `min<=max` (and rejects stray `cycles`); `elastic` rejects any cycle-count field. |
| IR field | **DONE** | `ResolvedModuleDefinition.latency: Optional[LatencyDeclaration]` (`forge/ir/model.py`) — additive alongside the existing flat fields, reusing `topgen.config.LatencyDeclaration` directly (not mirrored) per the same layering `ir/build.py` already established for `DesignConfig`/`Module`; confirmed compatible with `ci/import_direction_check.sh` (schema-layer import, not a generator/CLI one). Populated in `forge/ir/build.py::assemble_project_ir`. `forge/core/cli/groups/inspect.py::_project_from_json` round-trip updated (the same "new field needs round-trip reconstruction or `--diff` silently drops it" lesson from every prior slice that's added an IR field). |
| `graph.py` precedence | **DONE** | A structured `latency:` declaration is explicit/authoritative, taking precedence over the flat `latency_cycles`/`hls_report`/`latency_hint` chain — same "explicit wins" rule `_resolve_latency` already applies to `latency_cycles`. Implemented identically in both `_build_graph_from_ir` (via `Module.timing.latency`) and `_build_graph_legacy`/`_build_latency_map` (via a raw `entry.get("latency")` dict) through one shared `_latency_value_from_declared_kind` helper. |
| Real-design migration | **DONE** | `plugins/trigger_demo/forge/modules.yml`'s `trigger_logic` entry: `latency_hint: 3  # HLS LATENCY min=3 max=3 (confirmed by HLS report HLS_SYN_LAT=3)` → `latency: {kind: fixed, cycles: 3}` — the comment literally documented a fixed value the old schema couldn't express natively; now first-class. Verified identical resolved value (3 cycles) before/after, provenance correctly now `explicit_contract` instead of `user_hint` (confirmed via `forge analyze latency-check`'s rendered Markdown output, and via updated test assertions in `test_module_timing.py`/`test_ir_build.py`/`test_latency_graph_ir_equivalence.py`). |
| Tests | **DONE** | `test_module_timing.py` (+19): `LatencyDeclaration` per-kind construction/rejection (6), `_pop_timing` parsing per kind + elastic normalization + both-syntaxes rejection (5), `RegistryValidator` structural acceptance/rejection for all 3 kinds and their error paths (9, via a new `_registry_with_latency_block` fixture helper), one real-design IR-population integration test. 3 pre-existing tests updated for the real `trigger_logic` migration (`test_trigger_demo_modules_have_timing_from_registry`, `test_modules_carry_latency_metadata_from_the_registry`, `test_build_graph_dispatches_to_ir_path_by_default`) — deliberate, documented value/provenance changes, not regressions. |
| Full suite | **DONE** | `698 passed, 8 skipped` (+19 over Phase 4 slice 1's 679), coverage `63.43%` (up from `63.26%`), **0 regressions**. `test_generation_ir_equivalence.py`'s byte-identical RTL proofs re-ran unchanged (module-level timing has never fed generation, confirmed again). |

### What's left after Phase 4 slice 2

- `bounded`/`elastic` declarations are schema-complete and IR-populated
  but not yet *acted on* by the checker (a `bounded` node's `.latency_cycles`
  back-compat property is `None`, so it's folded into "unknown" by the
  still-Phase-3-era checker, same as before this slice — correct,
  conservative default until slice 3's alignment-kind inference lands).
- No real-design usage of `bounded`/`elastic` exists yet (only `fixed`,
  via the `trigger_logic` migration) — `variable_latency`/`elastic`
  real-design validation remains open per the honest deferral list.

---

## Phase 4 (slice 3) — 4.3 Alignment requirements

Per-instance `LatencyGraph` node/edge granularity (the confirmed
prerequisite — module-group granularity meant zero real merge points
existed in either reference design), then alignment-kind inference so
the checker "must not assume every reconvergence requires identical
scalar latency" (§4.3's own wording, now literally true).

| Item | Status | Evidence |
|---|---|---|
| Per-instance node granularity | **DONE** | `forge/analyze/latency_static/graph.py`: new `_instance_node_names(mod)`/`_instance_names_raw(name, instances)` helpers — a single-instance module keeps its bare name (`"col"`, zero graph change for the common case); a multi-instance module splits into `"name[0]"`, `"name[1]"`, ... Applied identically to **both** `_build_graph_from_ir` and the legacy raw-YAML fallback (`_build_graph_legacy`/new `_instance_endpoints` helper) — same "define the rule once, don't let the two paths diverge" discipline as every prior slice's shared-precedence functions. `LatencyNode.instances` is now always `1` for a per-instance node (it represents exactly itself); latency/kind/ref stay identical across every instance of the same module (a module/HLS-level fact, not a per-instance one). |
| Per-instance edge expansion | **DONE, conservative for the ambiguous case** | New `_add_instance_edges` (IR path) / `_instance_endpoints` (legacy path): a producer with N instances feeding a 1-instance consumer (or vice versa) expands into N real edges — structurally certain, since there's only one possible instance on the singular side to connect to; no port-matching/contract data needed. When *both* sides have >1 instance (not present in either reference design today), conservatively expands to the full Cartesian product rather than guessing a single pairing — documented in the module's own docstring as a deliberate over-approximation for a case that doesn't arise in practice yet, not silently wrong data. |
| Real merge points surfaced | **DONE — the actual point of this slice** | Verified on real `trigger_demo`: `col` and `partmon` (both fed by all 4 real `dec` instances via `decoder_to_collector`'s `role_pairs` gather and `decoder_hits_to_partition_sink`/`decoder_valids_to_partition_sink`'s `instance_assign` groups) now register as genuine 4-predecessor merge points — the first real (not synthetic) merge points this project has ever had, confirmed both by a new automated test and by manually running `forge analyze latency-check` end-to-end (rendered Markdown shows 4 real `dec[i] → col`/`dec[i] → partmon` input paths). Both correctly report **balanced** (`exact_cycle` alignment, delta 0) — all four `dec` instances share the same module-level `latency_hint: 0` and neither topology group declares `register_stages`/`delay_cycles`. |
| `MismatchReport.alignment` | **DONE** | New field, one of `exact_cycle`/`bounded_skew`/`transaction_order`/`elastic_buffer` — `forge/analyze/latency_static/checker.py`. Inference: any predecessor `kind=="elastic"` → `elastic_buffer` (never a mismatch, tolerant by definition); any predecessor `kind=="bounded"` → `bounded_skew` (mismatch iff the paths' `[min,max]` cycle ranges — a degenerate `[v,v]` point range for fixed/hint/hls_report predecessors — fail to overlap, a real range-overlap computation, not just a label change); consuming node's protocol `== "ready-valid"` (via a new optional `consumer_protocols` param, best-effort — checker stays usable from a bare `LatencyGraph` alone, same "usable before synthesis" property the graph builder itself preserves) → `transaction_order` (never a scalar-latency mismatch); otherwise → `exact_cycle`, byte-identical to pre-slice behavior (same delta computation, same suggestion logic). |
| Reporter visibility | **DONE** | `reporter.py`'s merge-point section now prints `Alignment: `<kind>`` per merge point — deliberate output change, consistent with every prior slice's "make new evidence visible in rendered output" precedent. |
| Tests | **DONE** | `test_latency_checker.py` (+7): a synthetic multi-instance fan-in becoming a real merge point; elastic-suppresses-mismatch-despite-unequal-cycles (the direct §4.3 proof); bounded-overlapping-ranges (not a mismatch) and bounded-non-overlapping-ranges (still a mismatch — proves the tolerance mechanism isn't blanket-permissive); a mixed bounded+fixed merge point (proves the fixed predecessor's exact value is checked against the bounded range, not skipped); `ready-valid` → `transaction_order` inference; `consumer_protocols` omitted defaults to unchanged `exact_cycle` behavior. `test_latency_graph_ir_equivalence.py` (2 updated): real per-instance node-set assertions for trigger_demo, `dec[0]` representative-instance assertion for the legacy-fallback test — deliberate, documented naming changes. `test_real_reference_designs_report_zero_false_mismatches` (updated from slice 1): now asserts the 2 real merge points by name and their balanced/`exact_cycle` status, instead of asserting zero merge points (which was itself only true because of the module-group-granularity limitation this slice fixes). |
| Full suite | **DONE** | `705 passed, 8 skipped` (+7 over Phase 4 slice 2's 698), coverage `63.35%`, **0 regressions**. |

### What's left after Phase 4 slice 3

- Both-sides-multi-instance edge expansion is conservative (Cartesian
  product), not exact — not present in either reference design, so not a
  real gap yet, but documented as a known approximation.
- No real design declares `kind: bounded`/`kind: elastic` — the
  overlap/tolerance logic is proven correct only synthetically (honest
  deferral, unchanged from slice 2's note on the same topic).
- `transaction_order` inference is wired but never invoked with real
  `consumer_protocols` data yet — no caller (e.g. `forge analyze
  latency-check`'s CLI command) currently builds and passes that mapping
  from the IR's `ResolvedLogicalInterface.protocol` data. Wiring that up
  is a small, low-risk follow-up, not attempted this slice to keep the
  diff focused on the checker's own logic.

---

## Phase 4 (slice 4) — 4.4 Throughput information

Recovers `interval_min`/`interval_max` — real data `forge.hls.extract_hls_metrics.HLSMetricsExtractor`
already parsed from `csynth.xml` but that `forge/analyze/hls_reports/extractor.py`
silently dropped before it reached `HLSModuleReport`. The rest of §4.4's
vocabulary (`buffering_capacity`/`occupancy`/`backpressure`/`frame_rate`)
has zero data source anywhere in this codebase — reserved honestly, not
fabricated.

| Item | Status | Evidence |
|---|---|---|
| `interval_min`/`interval_max` recovery | **DONE, mechanical** | `HLSModuleReport` (`forge/analyze/hls_reports/extractor.py`) gains `interval_min: int = 0`/`interval_max: int = 0` (same 0-default convention as every other latency field — `HLSMetricsExtractor.get_int` already defaults to 0 when the XML element is absent, confirmed by reading it). `_parse_xml` wires `lat["interval_min"]`/`lat["interval_max"]` (already present in the raw dict `extract_latency()` returns) straight through — zero new parsing logic, purely recovering already-extracted-but-discarded data. |
| Reserved fields | **DONE — documented, not fabricated** | `buffering_capacity: Optional[int]`, `occupancy: Optional[float]`, `backpressure: Optional[bool]`, `frame_rate: Optional[float]` added to `HLSModuleReport`, all `None`-default with an explicit docstring stating `csynth.xml` (a synthesis-time report) structurally cannot carry this data (it's runtime/simulation or RTL-generation-time information) and no other producer exists anywhere in the codebase — same "reserved, no effect yet" precedent as Phase 3's `width_adapter`/`protocol_adapter`/`constant_source`. `initiation_interval`/throughput itself is **not** reserved — it already had a real source (`pipeline_ii`, plus the newly-recovered `interval_min`/`interval_max` range), reusing the exact field name already established at `design_parameters.py:443`. |
| Reporting | **DONE** | `forge/analyze/hls_reports/formatter.py`: CSV gains `interval_min`/`interval_max` columns; Markdown/HTML gain an "II Range" column rendering `"{min}–{max}"`, or an honest `"—"` (not a fabricated `"0–0"`) when the XML never carried the data at all — verified both ways with the new synthetic fixture. HTML's hardcoded missing-row `<td>—</td>` repeat count updated (`13`→`14`) to match the new column — a real, easy-to-miss regression class (a magic count divorced from the actual column list) caught by a dedicated new test. |
| Fixture decision | **DONE, explicitly synthetic-only** | Neither reference plugin has a checked-in `csynth.xml` (confirmed by search, matches the planning research), and no prior test in this codebase built one either. New `forge/tests/test_hls_reports_extractor.py` embeds a minimal, schema-accurate synthetic `csynth.xml` (matching `HLSMetricsExtractor`'s exact XPath expectations — `UserAssignments`, `PerformanceEstimates/SummaryOfOverallLatency`, `AreaEstimates/Resources`, etc. — not guessed) as a `tmp_path`-written fixture. **No real-design proof of the parsing path itself is possible** — documented here rather than silently claimed; the *absence*-handling path (no `hls_build_root` / no `csynth.xml`) remains real-design-testable and unchanged (pre-existing `test_analyze_cli_group.py::TestHlsReport` tests still pass unmodified). |
| Tests | **DONE** | `test_hls_reports_extractor.py` (new — 8 tests): `interval_min`/`interval_max` recovery; 0-default when absent from the XML; the 4 reserved fields stay `None`; CSV/Markdown/HTML each render the new data correctly; the honest "—" (not "0–0") rendering when absent; the HTML missing-row cell-count regression guard. Manually verified end-to-end via the real `forge analyze hls-report` CLI command against the synthetic fixture (rendered Markdown table showing `II Range: 1–2`). |
| Full suite | **DONE** | `713 passed, 8 skipped` (+8 over Phase 4 slice 3's 705), **0 regressions**. |

### What's left after Phase 4 slice 4

- `buffering_capacity`/`occupancy`/`backpressure`/`frame_rate` remain
  permanently reserved unless a future data source appears (e.g. a
  runtime/simulation-derived metric, or a different report format) —
  not planned as a near-term follow-up.
- No real `csynth.xml` exists in either reference plugin to validate the
  parsing path against a real HLS build — an honest, environment-driven
  constraint (no HLS toolchain/build artifacts available in this repo
  checkout), not a shortcut.

---

## Phase 4 (slice 5) — 4.5 Static/runtime unification

Three concrete sub-decisions from the plan, all implemented: (a) fix the
confirmed broken wide-CSV/long-CSV pipe between `gen_sim.py`'s real
Tier-2 probe emission and `latency_runtime/probe.py`'s consumer, (b)
unify `LatencyComparison` with slice 1's `LatencyValue`/`LatencyProvenance`
wrapper, (c) explicitly reconcile (not fix) `design_parameters.py`'s
third, independent latency-summing codepath.

| Item | Status | Evidence |
|---|---|---|
| Wide/long CSV pipe fix | **DONE — real bug fix** | New `forge/analyze/latency_runtime/probe.py::load_wide_probe_csv(path)` melts `gen_sim.py`'s actual emitted wide format (`cycle,<probe1>,<probe2>,...` — confirmed by reading `_render_probe_open`/`_render_probe_fwrite` directly, not guessed) into the same `ProbeEvent` list `load_probe_csv` (long format) already produces. Probe names are read straight from the wide CSV's own header row (the same names `_render_probe_open` writes there) — no separate probe-name list needs to be supplied. `load_probe_csv` itself is completely untouched — purely additive. |
| CLI wiring | **DONE, exact backward compatibility** | `forge analyze runtime-latency --probe-format {long,wide}` (`forge/core/cli/groups/analyze.py`), default `long` — the one pre-existing test exercising this command needed zero changes, confirmed by re-running it unmodified. |
| `LatencyComparison` unification | **DONE** | `forge/analyze/latency_runtime/comparator.py`: new `predicted: Optional[LatencyValue]` (provenance configurable via `compare()`'s new `predicted_source` kwarg, default `hls_report`) and `observed_value: Optional[LatencyValue]` (provenance always `runtime_observation`) fields, additive alongside the existing `hls_predicted`/`observed` ints (kept verbatim — `reporter.py` reads them directly, needed zero changes). New `path: Optional[List[str]]` field (the fifth required report item, "affected path or interface" — previously unrepresentable at all), populated only when the caller supplies one (honest partial coverage, not a mandatory fabricated field). This closes all 5 release-plan §4.5 report requirements: predicted latency, observed latency, discrepancy (`delta`, unchanged), source of each prediction (new), affected path (new). |
| `design_parameters.py` reconciliation | **DONE — cross-referenced, deliberately not replaced** | A new, detailed code comment at the naive cumulative-latency walker's construction site names the exact gap (file-order summing with zero DAG/parallelism awareness — it doesn't know `dec`'s 4 instances feed `col`/`partmon` in parallel, not sequentially) and points at `forge.analyze.latency_static` as the DAG-aware alternative. The live `design_parameters.json` output itself is **not** changed this phase (a higher-risk, out-of-scope change for an otherwise read-only-analysis-focused phase) — an explicit, documented deferral, not a silent gap. |
| Real-design agreement proof | **DONE, honestly scoped** | New `test_design_parameters_latency_agreement.py`: seeds a synthetic `hls_metrics.json` with each real trigger_demo module's *own* `ModuleTiming`-resolved cycle count (so both codepaths start from identical per-module facts — a genuine, not tautological, comparison), then proves the naive walker's per-module `cumulative_latency` agrees step-by-step with an independently-computed `LatencyGraph`-based running total along trigger_demo's real linear tail (`col→trig→tfan→tsink→tout`, confirmed to be a genuine 1-in-1-out chain by reading `design.yml`'s `connections:`). Does **not** claim agreement on the whole design (the real `dec×4→col`/`partmon` fan-out region is exactly where the two would diverge) — documented as the honest, narrower claim matching the code comment above. |
| Real-design `tier2_probes` | **DONE — declared, format-verified; execution not run** | `plugins/trigger_demo/forge/verify/trigger_pipeline_xsim/port_map.yaml`'s previously-empty `tier2_probes: []` now declares 2 real probes (`dec_0_raw_valid`, `tout_out_valid` — real top-level port names already present in the same file's port list, not invented) — a genuine, tracked repo change (confirmed via `git ls-files`, not an ephemeral/gitignored artifact). Manually verified `gen_sim.py`'s `_tier2_probes`/`_render_probe_open`/`_render_probe_fwrite` render these into valid SystemVerilog emitting exactly the wide-CSV shape `load_wide_probe_csv` expects (`cycle,dec_0_raw_valid,tout_out_valid` header, `%0d,%0b,%0b` row format) — format-compatibility proven end-to-end from declaration through generated code to the new loader. **Honest gap**: no Xilinx/XSIM toolchain is available in this environment, so an actual simulation run producing a real `algo_top_probe.csv` was not executed — documented explicitly rather than claiming full coverage. |
| Tests | **DONE** | `test_latency_runtime.py` (new — 8 tests): wide-CSV loading matches `gen_sim.py`'s real format; missing-file handling; `measure_latency` works on wide-loaded events; long-format loader unaffected; `compare()`'s new wrapper fields (populated, configurable source, optional path, correctly partial on the "unknown" verdict case). `test_analyze_cli_group.py` (+2): `--probe-format wide` end-to-end through the real CLI; `--probe-format` omitted still defaults to `long` unchanged. `test_design_parameters_latency_agreement.py` (new — 2 tests, described above). |
| Full suite | **DONE** | `725 passed, 8 skipped` (+12 over Phase 4 slice 4's 713), coverage `61.66%`, **0 regressions**. |

### What's left after Phase 4 slice 5

- No real XSIM-executed probe CSV exists — `tier2_probes` are declared
  and proven format-compatible, not simulation-verified end-to-end
  (environment constraint, documented above).
- `design_parameters.py`'s naive walker itself is unchanged — cross-
  referenced and agreement-tested on the linear segment only, still
  genuinely wrong at real fan-in regions if ever asked to reason about
  them (unchanged, pre-existing behavior, now at least documented).

**Phase 4 ("Latency and throughput model") is now complete — all 5
slices (§4.1-§4.5) done.**

---

## Phase 4 closing summary (slice 6 — readiness-doc closure)

Full honest-deferral list carried across all 5 slices, consolidated:

1. `buffering_capacity`/`occupancy`/`backpressure`/`frame_rate` (§4.4) — zero data source anywhere in this codebase; permanently reserved, `None`-valued, never fabricated.
2. `ControlSignalTarget.delay_cycles` (§4.1) — a third, structurally distinct connection-latency mechanism (broadcast control signals) incompatible with `LatencyGraph`'s module-pair edge model without a redesign; not folded into the edge model.
3. `design_parameters.py`'s naive DAG-blind cumulative-latency walker (§4.5) — cross-referenced and linear-segment-agreement-tested, not replaced; its live `design.ir.json`-adjacent JSON output was out of scope to change this phase.
4. `transaction_order` alignment (§4.3) — false-positive suppression only; no new check for transaction-order-specific violations (FIFO overflow, reorder detection). Also wired but never invoked with real `consumer_protocols` data by any CLI caller yet.
5. Real end-to-end XSIM-executed runtime-latency validation (§4.5) — `tier2_probes` declared and format-verified on real `trigger_demo`, but no Xilinx/XSIM toolchain available in this environment to actually run the simulation.
6. `async_fifo` CDC latency (§4.1, inherited from Phase 3) — no RTL exists, stays `None`, unchanged.
7. `variable_latency`/`bounded`/`elastic` real-design validation (§4.2/§4.3) — zero real usage of these kinds in either plugin today (only `kind: fixed`, via slice 2's real `trigger_logic` migration); the node-declaration side is necessarily synthetic-only, though the merge-point *graph structure* it's tested against (slice 3, `col`/`partmon`) is real.
8. Both-sides-multi-instance edge expansion (§4.3) is a conservative Cartesian-product approximation, not exact — not present in either reference design today.
9. No real `csynth.xml` exists in either reference plugin (§4.4) — the interval-recovery *parsing* path is synthetic-fixture-tested only; the *absence*-handling path remains real-design-tested.

Phase 4 exit state: `725 passed, 8 skipped`, coverage `61.66%`, **0
regressions** across all 5 slices combined (starting baseline: Phase 3's
final `667 passed, 8 skipped`, coverage `62.32%` — net +58 tests added
across Phase 4).

## Phase 5 (slice 0) — Fix the portable-hash bug

Investigated first (per this phase's own planning session, before writing
any code): confirmed a real, previously-undetected bug in
`forge/ir/serialize.py::_canonical_design_json` — its own docstring
already claimed the IR content hash is portable ("a content hash should
answer 'did the resolved design change', not... the caller's filesystem
layout"), but it hashed `asdict(project.design)` in full, which includes
`ResolvedDesign.source` — a `SourceLocation` populated with the
**resolved absolute path** to `design.yml`. Empirically confirmed by
building the same real design from two different absolute checkout
roots and diffing `content_hash()`'s output. The same class of leak
existed in `ProvenanceManifest.source_hashes`, whose keys were
`str(absolute_path)`.

| Item | Status | Evidence |
|---|---|---|
| `design.source` excluded from the hash | **DONE** | `_canonical_design_json` now pops `design_dict["source"]` before hashing — same exclusion pattern already used for `generated_from`/`forge_version`. |
| `source_hashes` keys made portable | **DONE** | `forge/ir/provenance.py`: keys are now relative to the design file's own directory (`_relative_key`/`_design_dir`), not absolute paths — recommended option (a) from the planning session (unambiguous, no collision handling needed, matches the project's existing `resolve_declared_path`-style conventions). |
| `PROVENANCE_SCHEMA_VERSION` bumped | **DONE** | `"0.1.0"` → `"0.2.0"` — a real, documented schema change (the key format changed), mirroring the exact precedent Phase 3 slice 4 set for `IR_SCHEMA_VERSION`'s 0.1.0→0.2.0 kind-vocabulary rename. |
| **Two further leaks of the same class, found by this slice's own regression tests, not anticipated by the original investigation** | **DONE** | (1) Each module's `contract_path` (resolved absolute path to its interface-contract YAML) and `source_files` (resolved to absolute paths by `topgen/config.py`'s registry loader for any module using `ref:` — the common case for both reference plugins) — found by the same "build from two different absolute checkout roots" technique. (2) Each `design.diagnostics[]` entry's `location.file` (every validation diagnostic is stamped with the resolved absolute `design.yml` path) — found later, by slice 5.4's YAML-key-ordering determinism test, which used a *different* two-different-tmp-directories technique that happened to trigger a diagnostic this slice's own tests hadn't exercised. Both fixed the same way: rewritten relative to the design file's own directory (`_portable_path`) rather than dropped, so a real rename/change is still visible as *a* change, just not tied to *where*. |
| Regression tests | **DONE** | `test_ir_provenance.py` (+3): `content_hash()` identical across two absolute checkout roots for the real `passthrough_demo` design; `source_hashes` keys identical across the same two checkouts; `content_hash()` identical across two checkouts of a synthetic design that produces a located diagnostic (the slice-5.4-discovered leak). `test_ir_build.py`/`test_inspect_cli_group.py`/`test_topgen_cli_commands.py` (existing tests updated, not regressed): 3 pre-existing assertions that hard-coded the old absolute-path key format (`str(DESIGN_YML) in manifest.source_hashes`, and a hand-rolled hash-comparison helper in `test_gen_top_design_ir_matches_fresh_inspect`) updated to the new relative-key/portable-path convention — deliberate, documented changes, not regressions. |
| Full suite | **DONE** | `726 passed, 9 skipped` (+2 over this session's own fresh-venv baseline of `724 passed, 9 skipped`), coverage `61.43%` (up from `61.37%`), **0 regressions**. |

### What's left after slice 0

Nothing scoped to this slice — the bug is fixed and both leak sites (plus
the two additional ones this slice's own tests surfaced) are closed.
Later slices build on this fixed foundation.

---

## Phase 5 (slice 1) — Provenance manifest completion + `gen-top` wiring

`ProvenanceManifest` gains 4 additive fields, and `gen-top` — which had
never written a provenance manifest at all before this phase (only
`forge inspect --provenance`, a read-only path, did) — now writes
`provenance.json` as a sibling of `design.ir.json` at all 3 of its
existing call sites (VHDL/Verilog/BD modes).

| Item | Status | Evidence |
|---|---|---|
| `plan_hash` | **DONE** | Populated by `gen-top` from the exact same `build_generation_plan`/`plan_hash()` call `forge build` already uses (reusing `ctx.project`, the *pre-generation* IR, so a hash pinned via `forge build --accept-plan-hash` can be compared directly against what a later `gen-top` run records). `None` for `forge inspect`'s read-only path — honest absence, no plan exists there. |
| `toolchain_versions` | **DONE — and exercised against real installed tools** | New `forge/core/toolchain_versions.py` (a standalone module, not importing from `doctor.py`, per the import-direction rules `forge/ir` is bound by): reuses `doctor.py`'s exact 5-tool list (`xvlog`/`xelab`/`xsim`/`ghdl`/`verilator`), `shutil.which` presence check, then `-version` (Xilinx tools) or `--version` (ghdl/verilator), 5s timeout, `"unknown"` on any failure — never raises. Auto-populated by `build_provenance` for every manifest (an environment fact, not generation-specific). **Correction to this phase's own planning assumption**: the plan's honest-deferral list expected "no Xilinx/GHDL/Verilator toolchain confirmed present" in this environment (per Phase 4's notes) — manually running `gen-top` on both reference designs during this slice's verification found real `Vivado Simulator v2024.1` (`xvlog`/`xelab`/`xsim`) and `Verilator 5.022` on `PATH`, captured with real, non-placeholder version strings in `provenance.json`. `ghdl` is genuinely absent. Automated tests still use a fake-`PATH` fixture (a trivial shell script), per the plan's own reasoning — real-tool behavior isn't asserted on since it could differ across environments — but the *code path* is now also confirmed against a real toolchain. |
| `output_hashes` | **DONE** | Hashes of the artifacts `gen-top` actually wrote, keyed the same relative-path way as the fixed `source_hashes` (slice 0). Reuses `forge build`'s own `_planned_output_artifacts(ctx, args)` for the candidate file list (no third re-derivation of gen-top's artifact set) — `build_provenance` silently skips any candidate that doesn't actually exist on disk (e.g. verilog-only artifacts a given run didn't need), so this is correct for all 3 modes without per-mode special-casing. |
| `project_identity` | **DONE** | The resolved consumer-root directory name (`ctx.c_root.name`) — `None` when unresolvable. Kept alongside, not replacing, the existing bare-design-stem `project_name`. |
| Tests | **DONE** | `test_ir_provenance.py` (+5): per-field defaults-to-honest-absence for `forge inspect`'s path; `toolchain_versions` auto-populated and never raises; caller-supplied fields accepted and round-trip through `write_provenance`/`read_provenance`; `PROVENANCE_SCHEMA_VERSION == "0.2.0"` regression guard. `test_toolchain_versions.py` (new, 9 tests): not-on-PATH → `None`; real version-string parsing via a fake script; the Xilinx `-version` (single dash) flag specifically proven, not just "any flag"; graceful `"unknown"` on bad exit / empty output / a hanging tool (0.2s timeout); `collect_toolchain_versions` omits absent tools; a real-environment smoke test that never raises. `test_topgen_cli_commands.py` (+1): real `gen-top` run on `passthrough_demo` asserting `provenance.json` exists with non-empty `output_hashes` whose recorded hashes match the real written files' current content hashes, and a `plan_hash` matching an independently-computed `forge build`-equivalent plan for the same design. |
| Full suite | **DONE** | `740 passed, 9 skipped` (+14 over slice 0's `726 passed, 9 skipped`), coverage `61.57%`, **0 regressions**. |

### What's left after slice 1

Nothing scoped to this slice. `output_hashes`/`source_hashes` keys can
become long, ugly relative-path chains (many `../` hops) when `--output`
is pointed somewhere structurally unrelated to `design.yml`'s own
directory — correct (still a real, working relative path, no absolute
leak), just not pretty; not a bug, and every real usage in either
reference plugin keeps `--output` inside the same project tree, where
keys stay short and readable (confirmed by manually running `gen-top` on
both `trigger_demo` and `passthrough_demo` with their real default output
locations).

---

## Phase 5 (slice 2) — Replace mtime-only staleness at the 3 real call sites

Mtime stays the fast pre-check (§5.1's own wording: "may remain an
optimization, but must not be the source of truth"); a new confirmation
layer consults a sibling `provenance.json` (now written by `gen-top` as
of slice 1) before trusting a stale mtime verdict.

| Item | Status | Evidence |
|---|---|---|
| Shared confirmation primitive | **DONE** | New `forge/core/provenance_staleness.py::confirms_fresh(source_path, provenance_dir)` — looks for `provenance_dir/provenance.json`, checks the source's recorded hash (in either `source_hashes` or `output_hashes` — a source to one checker can be an output of `gen-top`'s own manifest, e.g. a DUT RTL file) against a freshly computed one. Returns `True`/`False`/`None` (no usable data — callers fall back to mtime exactly as before this slice). One-directional by design: can only downgrade a stale mtime verdict to fresh, never the reverse. |
| `check_top_gen_staleness`/`check_ip_info_staleness` | **DONE** | `forge/core/stale_detection.py`: `ArtifactStaleness` gains `content_confirmed_fresh: Optional[bool] = None`; `.stale` now checks it first. Confirmation is looked up in `output_dir` — exactly where `gen-top` writes `provenance.json` as of slice 1, a clean, real fit (this checker's sources — `design.yml`/`modules.yml`/`ip_info.yaml` — are exactly what `provenance.json`'s `source_hashes` cover). |
| `check_flow_staleness` (`forge/verify/stale_artifact.py`) | **DONE, real for the common case** | `StalenessResult` gains the same field/property pattern. Confirmation is looked up next to whichever source file was newest (`source_path.parent`) — real and load-bearing for the `dut_rtl_source: gen-top/<name>` layout both reference plugins' top-level `algo_top` flow actually uses (confirmed by reading `design.verification.yml` directly: `dut_rtl_source` there points at `gen-top`'s own output directory, which now has a `provenance.json`); a harmless no-op (falls back to mtime) for the per-HLS-module flows, whose `dut_rtl_source` points at HLS synthesis output — no `provenance.json` is ever written there, honestly, since nothing in this phase generates one for HLS builds. |
| RC-10 (`forge verify release-check`) / doctor step 9 | **DONE, zero changes needed** | Both call `check_flow_staleness` directly and inherit the content-hash-aware behavior automatically — confirmed by reading both call sites; neither constructs `StalenessResult`/`ArtifactStaleness` itself. |
| Tests | **DONE** | `test_provenance_staleness.py` (new, 8 tests): the primitive in isolation — no manifest, unreadable manifest, source not tracked, match via `source_hashes`, match via `output_hashes`, genuine mismatch, bare-filename fallback lookup, missing source file. `test_stale_detection.py` (+3): a real `touch`-without-content-change scenario reports fresh once a `provenance.json` is present (the direct §5.1 proof: "may remain an optimization, but must not be the source of truth"); a genuinely-changed-content scenario still reports stale despite a manifest being present (regression guard against becoming falsely permissive); no-`provenance.json` falls back to mtime-only exactly as before this slice. `test_stale_artifact.py` (new, 6 tests): `StalenessResult.content_confirmed_fresh` overriding/not-overriding the mtime verdict; the same touch/genuine-change/no-manifest trio directly against `_check()`, using the real `gen-top/<name>` DUT-RTL-directory layout. |
| Full suite | **DONE** | `757 passed, 9 skipped` (+17 over slice 1's `740 passed, 9 skipped`), coverage `61.69%`, **0 regressions**. |
| Manual verification | **DONE** | Ran `gen-top` on a copy of `passthrough_demo`, touched `design.yml`'s mtime without changing its content, then ran `forge topgen validate --check-stale`: reports 0 stale artifacts. Removing `provenance.json` and re-running the identical check reproduces the old mtime-only behavior — 6 stale artifacts correctly flagged — confirming the fallback path is intact and the fix is real, not a no-op. |

### What's left after slice 2

`check_ip_info_staleness`'s IP-source-file (`.xci`/`.zip`) comparisons
stay mtime-only — `provenance.json` never hashes IP package files (only
`design.yml`/`contracts_from`/`ip_info`/module contract paths), so
`confirms_fresh` honestly returns `None` there; not a gap introduced by
this slice, since no data source for those hashes exists anywhere yet.

---

## Phase 5 (slice 3) — Explain staleness

`forge inspect --explain-staleness` already covered 4 of the 6 required
reasons (changed input, changed option, changed IR, missing provenance).
Two gaps closed; a third real gap (a genuinely unsafe crash/misreport
path, not in the original 6-reason list but confirmed by reading
`explain_staleness` against slice 0's key-format change) closed too.

| Item | Status | Evidence |
|---|---|---|
| "changed tool version" | **DONE** | `explain_staleness()` gains a `toolchain_versions` dict-diff (new/removed/changed-per-tool), same set-diff pattern already used for `source_hashes` — now meaningful since slice 1 populates the field for real. |
| "unsupported old manifest" | **DONE** | A *previous* manifest whose `schema_version` predates `PROVENANCE_SCHEMA_VERSION` (e.g. a slice-0-era `0.1.0` manifest, absolute-path keys) is flagged `StalenessExplanation(stale=True, reasons=["unsupported old manifest ..."])` and compared no further — confirmed by construction that comparing a 0.1.0 manifest's absolute-path keys against a current 0.2.0 manifest's relative-path keys would otherwise silently misreport every real key as both "removed" and "added" (a false diff), exactly the bug this reason exists to prevent. |
| Artifact-level reason surfacing | **DONE** | New `forge/core/provenance_staleness.py::describe_staleness_basis()` — a short reason clause ("confirmed via content hash: source content changed" / "mtime-only — no provenance manifest available to confirm"), reusing `explain_staleness`'s reason-formatting convention. Wired into both `ArtifactStaleness.message()` (`stale_detection.py`) and `StalenessResult.message()` (`stale_artifact.py`) — the IR-level and artifact-level staleness surfaces now read consistently. |
| Tests | **DONE** | `test_ir_provenance.py` (+4): schema-version-too-old fixture (asserts exactly 1 reason, no misleading key-diff noise); ordinary same-schema-family version-bump fixture (still an ordinary field diff, not "unsupported"); tool-version-changed/added/removed fixtures; identical-toolchains-no-reason regression guard. `test_topgen_cli_commands.py` (+2): a real `gen-top` run followed by `forge topgen validate --check-stale --json` on the identical design/output reports 0 stale (slice 2's fix, re-confirmed end-to-end); a genuine edit still reports stale **and** the JSON payload's `stale.artifacts` now includes a specific `reason: confirmed via content hash: source content changed` line, not just a bare verdict. |
| Full suite | **DONE** | `763 passed, 9 skipped` (+6 over slice 2's `757 passed, 9 skipped`), coverage `61.87%`, **0 regressions**. |

### What's left after slice 3

Nothing scoped to this slice.

---

## Phase 5 (slice 4) — Determinism tests

The 2 genuinely missing §5.4 bullets, confirmed by investigation to be
the only real gaps — "identical semantic input produces identical IR"
(`test_build_is_deterministic`) and "identical IR produces identical
plan" (`test_plan_hash_is_deterministic`) were already real, passing
tests before this slice.

| Item | Status | Evidence |
|---|---|---|
| "Identical plan produces stable generated output" | **DONE** | New `test_topgen_cli_commands.py::test_gen_top_run_twice_produces_stable_plan_hash_and_output_bytes` — `gen-top` run twice **in place** (same output location; two *different* output locations would legitimately disagree, since `plan_hash` correctly depends on `output_artifacts`, i.e. *where* the plan writes — not what this bullet is testing) on the real `passthrough_demo` design: `plan_hash` identical, `algo_top.v`/`design.ir.json` byte-identical. `build_manifest.json` is compared structurally with its `timestamp` field stripped first — inspection confirmed `generate_build_manifest`/`write_design_parameters` both embed a real `datetime.datetime.now().isoformat()` call, the *only* reason either file would ever differ between two runs of the same design; not a determinism bug, an expected, documented exclusion. Chains `forge/ir/plan.py::plan_hash()` to real generated file bytes for the first time. |
| YAML key ordering | **DONE** | New `test_ir_build.py::test_content_hash_is_independent_of_yaml_top_level_key_order` — two `design.yml` fixtures, byte-different only in top-level key order, built from two different tmp directories: `content_hash()` identical. Proves explicitly what was already architecturally guaranteed (every loader parses YAML into a plain dict, and `_canonical_design_json` always re-serializes via `json.dumps(..., sort_keys=True)`) rather than leaving it an implicit, unverified assumption — and, in the process of writing this fixture, surfaced the second `design.diagnostics[].location.file` portable-hash leak documented under slice 0 above (a real bug found *by* a determinism test, not just prevented by one). |
| Portability regression (closes the loop on slice 0) | **DONE, landed in slice 0** | The 3 targeted regression tests already exist in `test_ir_provenance.py` (checkout-portability for `content_hash()`, `source_hashes` keys, and the diagnostic-location leak) — cross-referenced here per the release plan's own "determinism tests" phase structure rather than duplicated. |
| Full suite | **DONE** | `766 passed, 9 skipped` (+3 over slice 3's `763 passed, 9 skipped`), coverage `61.88%`, **0 regressions**. |
| Manual verification | **DONE** | Ran `gen-top` twice on the real `trigger_demo` reference design (same output location), diffed `provenance.json`'s `plan_hash` (identical) and `algo_top.v`'s bytes (identical) across both runs. |

### What's left after slice 4

Nothing scoped to this slice.

---

## Phase 5 closing summary — deferred items

1. **Toolchain-version parsing robustness** — each tool's real
   `-version`/`--version` output format was assumed from documentation
   for the automated test suite (a fake-`PATH` fixture, not a real tool
   invocation, so behavior is asserted identically across every
   development environment). **Update on this phase's original
   assumption**: real Xilinx (`xvlog`/`xelab`/`xsim`, `Vivado Simulator
   v2024.1`) and Verilator (`5.022`) toolchains *are* present in this
   session's environment (confirmed by manually running `gen-top` on both
   reference designs and inspecting the real `toolchain_versions` values
   in the resulting `provenance.json`) — stronger evidence than the
   phase's own planning session assumed, though still not a substitute
   for the fake-fixture-based automated tests, which must stay
   environment-independent.
2. **`output_hashes` scope** — only covers `gen-top`'s own
   directly-written artifacts, not downstream `verify`-generated files
   (testbenches, waveforms) — those have their own, separate staleness
   mechanism (`stale_artifact.py`), made content-hash-aware by slice 2,
   but not merged into one unified provenance manifest this phase.
3. **`project_identity`** stays a best-effort derived string (consumer-root
   directory name), not a globally-unique persisted identifier — no UUID
   scheme invented, matching "don't fabricate infrastructure that doesn't
   exist" discipline.
4. **Full unification of `forge/ir/provenance.py` and
   `stale_detection.py`/`stale_artifact.py` into one system** was not
   attempted — slice 2 makes the mtime-based checkers *content-hash-aware*
   by consulting the provenance manifest when present, but the two
   systems remain architecturally distinct (different data shapes,
   different call sites) rather than merged into a single staleness
   engine. Concretely confirmed by this phase's own work: `stale_artifact.py`'s
   content-hash awareness is real only for the `dut_rtl_source:
   gen-top/<name>` layout (the top-level `algo_top` flow in both
   reference plugins) — the per-HLS-module flows' `dut_rtl_source` points
   at HLS synthesis output, which nothing in this phase writes a
   provenance manifest for, so those stay honestly mtime-only. A full
   merge (e.g. a provenance manifest per HLS build too) would be a
   larger, riskier refactor than this phase's scope.
5. **`check_ip_info_staleness`'s IP-source-file (`.xci`/`.zip`)
   comparisons** stay mtime-only — no data source for their hashes exists
   in `provenance.json` (only `design.yml`/`contracts_from`/`ip_info`/
   module contract paths are ever hashed), unrelated to this phase's
   scope.
6. **`output_hashes`/`source_hashes` key readability** when `--output`
   points somewhere structurally unrelated to `design.yml`'s own
   directory — correct (a real, working relative path) but can become a
   long chain of `../` hops; not pretty, not a bug, not exercised by
   either reference plugin's real usage.

Phase 5 exit state: `766 passed, 9 skipped`, coverage `61.88%`, **0
regressions** across all 5 slices combined (starting baseline: this
session's own fresh-venv run of `724 passed, 9 skipped`, coverage
`61.37%` — net +42 tests added across Phase 5). Note: this baseline is
this session's own measurement, not a re-run of Phase 4's documented
exit state (`725 passed, 8 skipped`, `61.66%`) — the 1-test/1-skip/
~0.3%-coverage difference reflects real environment differences (this
session's environment has Vivado/Verilator on `PATH`, exercising a few
additional code paths Phase 4's own environment notes said were
unavailable), not a regression introduced by this phase.

