# IP Interface Policy

**Version:** 1.2  
**Date:** 2026-04-04  
**Scope:** All HLS-generated and designer-authored IPs integrated through the framework contract model

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

The framework cares about **canonical roles** (`clock_primary`, `reset_primary`, `cfg_word`, `cfg_valid`, `input_stream_0`, …), not about whether the raw port is named `ap_clk`, `clk`, or `core_clock_i`. The canonical role vocabulary is defined in `canonical_roles.yaml`.

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

It does **not** check raw naming conventions, internal HLS coding style, or designer preferences.

---

## What the mapping spec (`*.interface.yaml`) must contain

For each IP:

| Field | Required | Description |
|-------|----------|-------------|
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

Consumer-specific extension roles that are not part of the generic framework vocabulary should use a consumer-owned prefix and carry `extension: true`.

Example:

- `omtf_*` for OMTF-owned extensions
- `trigger_demo_*` for a trigger-demo-specific extension family

---

## Canonical role vocabulary

See [`canonical_roles.yaml`](topgen/algo_top_gen/ip/canonical_roles.yaml) for the frozen role set.

Do not add new roles to that file without updating this policy. Extension roles in individual `*.interface.yaml` files do not require changes to the central vocabulary.

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

## Validation command

```bash
# Verify one IP contract:
topgen verify-contract \
    --ip-info ip_info.yaml \
    --contract plugins/<plugin>/interfaces/my_ip.interface.yaml

# Verify all known contracts in the workspace:
topgen verify-contract \
    --ip-info ip_info.yaml \
    --all-contracts plugins/<plugin>/interfaces/
```

Exit codes: 0 = pass, 1 = warnings only, 2 = errors.

---

## Versioning

This policy covers v1.0 of the normalization effort. The scope of required roles may expand in v1.1 when generated wrapper support is added.
