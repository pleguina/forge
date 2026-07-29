# Contracts and Protocols

**Scope:** All HLS-generated and designer-authored IPs integrated through the framework contract model.

---

## Architecture statement

**Contracts are the authoritative source of integration intent for supported modules.**

The generator uses contracts as the primary wiring source.
Heuristic port-name matching is retained only as a fallback for legacy/unmanaged modules.

A module is **contract-supported** when:

1. A valid `*.interface.yaml` contract exists (referenced in `modules.yml` via `interface_contract:`).
2. The contract passes `verify-contract` against the exported IP metadata.
3. All required generation-authoritative roles are present in the contract.

For any contract-supported module:
- The generator **must** wire framework-facing roles from the contract.
- The generator **must not** substitute heuristic name matching for required roles.
- If the contract is missing or invalid at generation time, generation **fails** for that module.

For unmanaged/legacy modules (no contract):
- The generator falls back to heuristic port-name matching.
- A compatibility-mode warning is emitted for every such module.
- Strict builds (`--strict`) reject compat-mode modules entirely.

This policy is reflected in the implementation. It is not aspirational.

---

## Core rules

### 1. Raw IPs remain free

The framework does **not** require raw HLS IPs to rename ports, follow a naming convention, or restructure their modules. Designers keep full freedom over internal names, local signals, and HLS coding style.

### 2. Contract-supported modules require an integration contract

Any module that is part of the supported integration path must have a source-controlled `*.interface.yaml` contract. The contract maps raw IP ports to canonical semantic roles. It is the *only* required integration artifact.

### 3. The framework normalizes semantics, not strings

The framework cares about **canonical roles** (`clock_primary`, `reset_primary`, `cfg_word`, `cfg_valid`, `input_stream_0`, …), not about whether the raw port is named `ap_clk`, `clk`, or `core_clock_i`. See [Canonical Roles](#canonical-roles) below for where the vocabulary itself lives.

### 4. Generation-authoritative roles drive wiring deterministically

A subset of canonical roles is **generation-authoritative** — marked `generation_authoritative: true` in `canonical_roles.yaml`. For these roles, the generator ignores heuristics and wires directly from the contract's `raw_port` declaration:

| Role | What it wires |
|------|--------------|
| `clock_primary` | Per-module clock input |
| `reset_primary` | Per-module reset input |
| `cfg_word` | Configuration data word input |
| `cfg_valid` | Configuration valid flag input |

Future phases will add stream roles to this set.

### 5. Heuristics are legacy compatibility mode only

Heuristic port-name matching (`matcher.py`) is not the official integration mechanism. It is a compatibility shim for modules that have not yet been migrated to contracts. Every module running in compatibility mode emits a warning at generation time.

### 6. Validation checks the integration contract, not style

The `verify-contract` command answers:

- Are all required canonical roles present?
- Do mapped port widths and directions match the spec?
- Are role assignments unambiguous?

It does **not** check raw naming conventions, internal HLS coding style, or designer preferences. See [How to Author Topology Contracts](../how-to/author-topology-contracts.md#validating-your-contract) for the actual command usage.

---

## What the mapping spec (`*.interface.yaml`) must contain

For each IP:

| Field | Required | Description |
|-------|----------|--------------|
| `module_name` | ✓ | Raw HLS module name (as in `ip_info.yaml`) |
| `ip_info_key` | ✓ | Key in `ip_info.yaml` used for port lookup |
| `source_type` | ✓ | `hls` or `hdl` |
| `roles` | ✓ | Map of canonical-role → port mapping |

Each role entry for a scalar port must specify:
- `raw_port` — the raw port name
- `direction` — `input` or `output`
- `width` — port width in bits

Each role entry for an array of ports specifies:
- `array: true` (optional — detected from `raw_port_prefix`)
- `raw_port_prefix` — common name prefix (e.g., `in_dt_mb1_`)
- `count` — number of ports in the array
- `direction`, `width` — as above

Each role entry for an N-D template port grid specifies:
- `raw_port_tpl` — template string with `{0}`, `{1}`, … placeholders (e.g., `out_layer{0}_{1}`)
- `dims` — list of positive integers giving each dimension's size (e.g., `[18, 8]`)
- `direction`, `width` — as above
- `wiring_kind` — (recommended) semantic connection-pairing key (not a protocol type — use for ap_none plain data port families) for contract-driven wiring

The number of `{N}` placeholders in `raw_port_tpl` must equal the length of `dims`.

### Partitioned roles: `partition` and `coordinates`

A role that is one of several partition slots on a multi-instance consumer
(matched via `topology_groups`' `instance_assign`) declares its slot label
using either of two equivalent forms:

- the legacy scalar label — `partition: lower_pair` — a single free-form
  string, matched by exact equality.
- a structured mapping — `coordinates: {sector: 2, station: 1}` — one or
  more named axes, matched as a set (axis order doesn't matter).

Both forms normalize to the same internal comparison key (see
`forge/topgen/ip/coordinates.py`), so:
- a `design.yml`'s `instance_assign` entries may use `partition:` or
  `coordinates:` independently of which form the target interface contract's
  roles use, as long as the resolved key matches;
- two roles on the same consumer contract that resolve to the same key
  (whether both use `partition`, both use `coordinates`, or one of each) are
  rejected as a duplicate-coordinate schema error;
- an `instance_assign` entry whose key matches no consumer role's key is
  rejected as an unresolved-coordinate error, listing the available keys.

Prefer `coordinates:` for genuinely multi-axis topology (e.g. a 2D
sector/station grid) — it's self-documenting and lets the duplicate/coverage
checks reason about axes individually in future releases. Prefer the plain
`partition:` string when there's truly only one axis; it isn't deprecated,
and no existing `partition:`-based interface contract or design needs to
change to keep working.

See "Declarative cardinality" below for `exactly one producer`/forbidden
fan-out/required-completeness constraints beyond coordinate matching
itself.

Consumer-specific extension roles that are not part of the generic framework vocabulary should use a consumer-owned prefix and carry `extension: true`.

Example:

- `omtf_*` for OMTF-owned extensions
- `trigger_demo_*` for a trigger-demo-specific extension family

---

### Protocol semantics: `protocol`

Any role may declare an optional `protocol:` string describing its
handshake semantics:

```yaml
roles:
  decoded_hit:
    raw_port: decoded_hit
    direction: output
    width: 32
    wiring_kind: decoded_hit
    protocol: valid-only
```

Conceptually, `protocol` distinguishes handshake shapes that matter for
correctness: pure combinational logic with no clock-relative timing at all;
a data signal accompanied only by a valid strobe with no backpressure (a
`clock_free`, `II=1` producer gated by a `*_valid` signal is the common
case); a full ready/valid handshake where the consumer can apply
backpressure; and a fixed-length, framed transfer with no per-cycle
handshake signal at all. See [Protocols](../reference/protocols.md) for the
complete, generated list of recognized `protocol` values.

`protocol` is optional and absent by default (`None`) — no existing
interface contract needs to declare it to keep working. A declared value
outside the recognized set is a contract-verification error
(`forge topgen verify-contract`), not a silent no-op.

**Scope of this release**: `protocol` is descriptive metadata, surfaced
through `LoadedContract.get_connection_roles()` and the canonical IR
(`ResolvedLogicalInterface.protocol`) for inspection (`forge inspect`).
Full protocol *compatibility validation across matched connections* (e.g.
rejecting a `ready-valid` producer feeding a `valid-only` consumer) is
**not yet implemented** — topology matching still operates per role, not
per grouped interface (see "Interface members" below and
`docs/development/release-readiness.md`). Do not implement a full new
HDL/assertion language for this — that is explicitly out of scope.

---

### Interface members: `interface` and `member`

By default every physical signal is its own independent logical interface
— a `raw_hit` role and its companion `raw_valid` role show up in
`forge inspect` as two unrelated interfaces even though they are one
handshake. A role may opt into **grouping** by declaring both fields:

```yaml
roles:
  raw_hit:
    raw_port: raw_hit
    direction: input
    width: 32
    wiring_kind: raw_detector_hit
    protocol: valid-only
    interface: raw_detector_hit   # group name — shared across members
    member: data                  # this role's part within the group

  raw_valid:
    raw_port: raw_valid
    direction: input
    width: 1
    wiring_kind: raw_hit_valid
    protocol: valid-only
    interface: raw_detector_hit
    member: valid
```

Each `member` names the role a signal plays within its handshake group —
for example the payload itself, its valid strobe, a backpressure signal, a
frame-boundary marker, or an out-of-band metadata signal. An unrecognized
`member` value is a contract-verification error; declaring `member:`
without `interface:` is a warning (it has no effect on its own). See
[Interface Members](../reference/interface-members.md) for the complete,
generated list of recognized `member` values.

Roles sharing one `interface:` name are merged into a single
`ResolvedLogicalInterface` in the canonical IR, with one
`ResolvedInterfaceMember` per role. The group's `wiring_kind`,
`coordinates`, and `protocol` are taken from the `data` member (or the
first declared role if none is named `data`). A `ready` member commonly
flows the *opposite* physical direction from `data`/`valid` (e.g. a
consumer's `ready` output paired with its `data`/`valid` inputs); when a
member's own `direction:` differs from the group's, it is recorded on
`ResolvedInterfaceMember.direction` rather than forcing every member to
share one direction.

Declaring the same `member` name twice within one `interface` group is a
contract-verification error (ambiguous — which role is "the" `valid`
signal?).

**Not required**: `interface`/`member` are entirely optional. A role that
omits them keeps today's 1:1 behavior unchanged — this is additive, not a
migration. Topology matching (`forge.topgen.ip.matcher`) still operates
per role/`wiring_kind`, independent of grouping; grouping is currently an
IR/inspection-level representation, not a matching-time construct.

---

### Declarative cardinality: `cardinality`

Any role may declare an optional `cardinality:` block bounding how many
producers may drive it (`input` roles) or how many consumers it may drive
(`output` roles):

```yaml
roles:
  raw_hit:
    raw_port: raw_hit
    direction: input
    width: 32
    cardinality:
      producers: {min: 1, max: 1}   # exactly one producer, required

  decoded_hit:
    raw_port: decoded_hit
    direction: output
    width: 32
    cardinality:
      fanout: forbidden              # sugar for consumers: {max: 1}
```

Fields (see `forge/topgen/ip/cardinality.py`):

- `producers: {min, max}` — **input roles only**. How many distinct
  producer pins may drive this role. `max` may be an integer or the
  literal string `many`.
- `consumers: {min, max}` — **output roles only**. How many distinct
  consumer pins this role may drive.
- `fanout: allowed | forbidden` — sugar for `consumers.max` (`allowed` →
  `many`, `forbidden` → `1`). Output roles only.
- `completeness: required | optional` — sugar for the relevant bound's
  `min` (`required` → at least 1, `optional` → 0 without raising it).

Declaring `producers` on an output role, `consumers`/`fanout` on an input
role, or an unrecognized key is a contract-verification error — the
direction/key mismatch is deliberate, not a leniency gap, since it usually
signals the role's `direction:` or the intended constraint is wrong.

**Where enforcement happens**: `parse_cardinality` only validates one
role's block *in isolation* (used by `ContractVerifier` for structural
checks — bad bounds, wrong key for the role's direction). Checking whether
the *actual wired design* satisfies a bound is a separate, design-wide
check: `forge.topgen.ip.cardinality.verify_cardinality(design_cfg,
contracts, match_report)`, run against
`forge.topgen.ip.matcher.MatchReport` — the same object
`auto_match_ports` already returns. It reports:

- **producers below `min`** (a required input never got connected —
  "required completeness");
- **producers above `max`** (an ambiguous fan-in the matcher's
  first-driver-wins guard silently resolved by picking the first match —
  now surfaced via `MatchReport.rejected_fanin`, instead of silently
  dropping the losing candidate);
- **consumers outside `[min, max]`** (forbidden or insufficient fan-out on
  an output role).

`forge topgen gen-top --strict` fails the build when `verify_cardinality`
reports any `error`-severity issue, the same pattern already used for
`verify_topology_groups`.

**Scope of this release**: cardinality is evaluated **per individual
physical pin**, after array/N-D roles are expanded to concrete port names
— there's no separate group-level DSL for scatter/gather cardinality (each
expanded pin of a `prefix_array`/`nd_tpl` role is checked exactly like a
scalar role). A `cardinality:` block declared on a grouped interface
(Phase 2.5) is read from the group's `member: data` role and surfaced on
`ResolvedLogicalInterface.cardinality` in the canonical IR — descriptive
only there; `verify_cardinality` (not IR construction) is what actually
enforces it.

---

### Schema versioning: `schema_version`

The `ip_interface:` block may declare an optional `schema_version:` string
(`"major.minor"`, e.g. `"1.0"`):

```yaml
ip_interface:
  schema_version: "1.0"
  module_name: hit_decoder
  ...
```

Absence is valid and silent — no existing contract needs to declare it to
keep working. A declared value is checked against
`forge.topgen.ip.contract_loader.INTERFACE_CONTRACT_SCHEMA_VERSION`: same
major with a declared minor the current FORGE build doesn't recognize is a
warning (fields beyond that minor may be ignored); a different major is a
contract-verification error. See
[Schema Versioning](../development/SCHEMA_VERSIONING.md) for the full policy shared across
all four user-facing FORGE schemas (design topology, module registry,
interface contract, verification contract), including compatible-addition,
deprecation, and removal rules.

---

## Canonical roles

Canonical roles are the framework's semantic vocabulary for physical
ports — the fixed set of names (`clock_primary`, `cfg_word`,
`input_stream_0`, and so on) that a contract's `roles:` map is built from.
A role may be marked `generation_authoritative` (drives wiring
deterministically, see "Core rules" above) or `status: reserved` (accepted
in a contract today for forward compatibility, but with no functional
effect in the current release — e.g. `clock_secondary`/`reset_secondary`,
reserved for a future multi-clock-domain release). See
[Canonical Roles reference](../reference/canonical-roles.md) for the full,
generated enumeration, including which roles are generation-authoritative
or reserved. Do not add new roles without updating that vocabulary;
consumer-owned extension roles (`extension: true`) do not require changes
to the central vocabulary.

---

## Three-layer separation model

| Layer | Location | Contents |
|-------|----------|----------|
| **Source contract** | Plugin source tree | `plugins/<plugin>/interfaces/*.interface.yaml` |
| **Generated artifact metadata** | `ips/`, build output | `ip_info.yaml`, exported component.xml, generated wrapper RTL |
| **Validation output** | Build / output directories | Contract validation reports, generated normalized wrappers, derived port maps |

The verifier reads the source contract and the generated artifact metadata independently. It never writes back into either.

---

## Where mapping specs live

| IP type | Contract location |
|---------|-----------------|
| HLS IP owned by plugin `plugins/<plugin>/` | `plugins/<plugin>/interfaces/<ip_name>.interface.yaml` |
| HDL utility used by a plugin | `plugins/<plugin>/interfaces/<module>.interface.yaml` |
| Future plugin | `plugins/<name>/interfaces/<ip_name>.interface.yaml` |

`ips/` is **generated/build-owned** space. Interface contracts must not live there — they may be overwritten by an IP regeneration step.

The `modules.yml` module registry references the contract via the `interface_contract:` field. Tooling resolves this path relative to the repository root.

---

## What remains free to the designer

- Raw internal signal names
- Raw IP external port names before integration
- Internal implementation style and HLS top structure
- Non-framework-facing helper signals
- Module-local decomposition

The framework contract applies only at the **integration boundary** described in the mapping spec.

---

## Versioning

This policy covers v1.0 of the normalization effort. The scope of required roles may expand in v1.1 when generated wrapper support is added.
