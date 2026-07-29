# Schema versioning

Release-plan §2.7: every user-facing schema must carry an explicit
identity and version, with documented compatible-addition, deprecation,
removal, and old/new cross-compatibility behavior. This document is that
policy for FORGE's schemas.

## The six schemas

| Schema | File(s) | Version field | Current version | Supported-version constant |
|---|---|---|---|---|
| Design topology | `design.yml` | `schema_version` (top level, optional) | `1.0` | `forge.topgen.config.DESIGN_SCHEMA_VERSION` |
| Module registry | `modules.yml` | `registry_version` (top level, optional — pre-existing field name, kept as-is) | `1.0`-equivalent (`'1'` accepted as legacy shorthand) | `forge.topgen.config.MODULE_REGISTRY_SCHEMA_VERSION` |
| Interface contract | `*.interface.yaml` | `schema_version` (under `ip_interface:`, optional) | `1.0` | `forge.topgen.ip.contract_loader.INTERFACE_CONTRACT_SCHEMA_VERSION` |
| Verification contract | `design.verification.yml` | `schema_version` (top level, optional) | `1.0` | `forge.verify.design_contract.VERIFY_CONTRACT_SCHEMA_VERSION` |
| Canonical IR | generated `design.ir.json` | `schema_version` (always present — system-generated) | `0.1.0` | `forge.ir.model.IR_SCHEMA_VERSION` |
| Provenance manifest | generated provenance JSON | `schema_version` (always present — system-generated) | `0.1.0` | `forge.ir.provenance.PROVENANCE_SCHEMA_VERSION` |

**Two different string shapes are intentional, not an inconsistency to
fix**: the four *user-authored* schemas use a plain `"major.minor"` string
(no patch component — these are hand-edited, checked-in files, not
published packages, and a patch-level distinction has no meaning for
them). The two *generated* artifacts (canonical IR, provenance manifest)
keep their existing `"major.minor.patch"`-shaped strings (`"0.1.0"`) —
they predate this policy, are independently versioned per the README's
"multiple independently-evolving version numbers" note, and rewriting
them to match the new convention is an unnecessary, disruptive rename of
an already-shipped identifier (governing rule: avoid gratuitous
restructuring).

Module registry's `registry_version` keeps its existing field name rather
than being renamed to `schema_version` — it was already documented and
semantically clear before this policy existed (the same reasoning that
kept `wiring_kind` in Phase 2.1).

## Shared compatibility policy

Implemented once in `forge/core/schema_version.py`
(`parse_schema_version`/`check_schema_version`), applied identically to
all four user-authored schemas:

* **Not declared → silent.** No warning, no error. This is deliberate: it
  keeps every pre-existing `design.yml`/`modules.yml`/`*.interface.yaml`/
  `design.verification.yml` in the repository (and in every downstream
  project) loading exactly as before. Retroactively warning on every
  undeclared file would be noise, not signal — declaring `schema_version`
  is opt-in, matching the precedent set by `protocol:`/`interface:`/
  `cardinality:` (Phase 2.2/2.5/2.4): new optional metadata is never
  flagged by its absence.
* **Same major, declared minor ≤ supported minor → silent.** Fully
  compatible — this is the normal case for a file written against an
  older, still-supported minor version.
* **Same major, declared minor > supported minor → warning.** This FORGE
  build doesn't know about whatever optional fields were added after its
  supported minor — it ignores them rather than failing ("new-schema /
  old-FORGE" behavior).
* **Different major → error.** FORGE does not attempt to interpret a
  schema from an incompatible major version.
* **Malformed value → error** (anything that isn't `N` or `N.M` with
  non-negative integers).

One exception: **`design.verification.yml`'s loader
(`forge.verify.design_contract.load_verify_design`) only acts on
error-severity issues.** That loader is raise-only throughout (no
`print()` calls, no warnings-collection list anywhere in the module) — it
was not retrofitted with a new diagnostic channel just for this field. A
newer-minor warning is silently accepted there rather than surfaced. This
is a deliberate, documented asymmetry, not an oversight.

## Compatible additions, deprecation, removal

* **A minor version bump** may only *add optional* fields. No field may
  become required, and no existing field's meaning may change, in a minor
  bump.
* **Deprecating a field**: keep accepting it (silently, or with a
  call-site-specific warning — e.g. reserved-role warnings already do
  this for `clock_secondary`/`reset_secondary`, see
  `docs/IP_INTERFACE_POLICY.md` "Reserved roles") for at least one full
  major version before a later major version may remove it.
* **Removing or repurposing a field**: requires a major version bump.
  Old-major files then fail loading with the "incompatible schema" error
  above (not a silent misinterpretation) until migrated.
* **Migration tooling** for moving a file between major versions is
  tracked separately as release-plan §2.8 (not yet implemented).

## Old-schema / new-FORGE and new-schema / old-FORGE, summarized

| | Old schema (older/absent minor) loaded by new FORGE | New schema (newer minor) loaded by old FORGE |
|---|---|---|
| Same major | Silent, fully compatible | Warning (fields beyond old FORGE's supported minor are ignored) — except `design.verification.yml`, silent (see exception above) |
| Different major | N/A (FORGE is never "older" than a file it wrote) | Error — incompatible, must migrate |

## Scope note: "generated result structures"

The release plan also lists "generated result structures" among the
schemas needing explicit versioning. Those are the versioned JSON output
envelopes (`{"schema_version", "status", "diagnostics", ...}`) planned for
Phase 6's `forge build`/`forge test`/`forge report` commands
(release-plan §6.7) — **Phase 6 hasn't been implemented yet**, so there is
no concrete structure to version. This is deferred to when Phase 6 is
built, not fabricated ahead of it.
