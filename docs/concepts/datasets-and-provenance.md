# Datasets and Provenance

Two related but distinct concepts: **datasets** are the stimulus/golden
data a verification flow runs against; **provenance** is the record of
what inputs and tool versions produced a given generated artifact. Both
exist to make verification results trustworthy and reproducible, for
different reasons.

## Datasets

A FORGE verification dataset is XML-backed data describing one or more
"events" (stimulus + expected output) that a flow's testbench replays. The
neutral, in-memory representation FORGE loads such a file into is
`forge.verification.dataset_format.SerializedDataset` — a schema-tagged envelope
(`ArtifactSchema("forge.dataset", "1.0")`) pairing the raw list of event
dicts with `DatasetMetadata` (schema, event ids, semantic and environment
metadata, optional units/seed/generator version).

This is deliberately **layer A** of a two-layer model: layer A only
answers "how does FORGE read an already-FORGE-shaped file (XML or JSON)
into this neutral envelope" — it says nothing about interpreting
arbitrary external raw data (ROOT files, image folders, PCAP captures).
Making sense of what loaded (or raw external) data actually *means*
domain-wise is layer B (`forge.verification.dataset_adapter`), a deliberately
separate, project-owned protocol — a dataset's *shape* is inherently
per-plugin (`passthrough_demo`'s and `trigger_demo`'s XML schemas are
completely disjoint), so layer A's job is loading and metadata, never
imposing one universal event structure.

A dataset's content hash (`compute_events_content_hash`) covers only its
canonicalized `events` — never *where* the file lives or *when* it was
loaded — so relocating or reloading a dataset file never changes its
computed identity. `event_ids` are always strings, never assumed to be
small contiguous integers, since real external identifiers
(`"run-355100-event-1842"`) aren't guaranteed to be. The exact field-level
schema is generated from the real dataclasses at
[the artifacts reference](../reference/artifacts.md) — not duplicated
here.

## Provenance

Separately, `forge.ir.provenance.ProvenanceManifest` is a content-hash
record attached to a build of the canonical IR: the FORGE version, IR
schema version, the canonical IR's own content hash, content hashes of
every real source file that fed into it (`design.yml`, `modules.yml`,
interface contracts), the command options used, and — additively —
best-effort toolchain versions, a generation-plan hash, hashes of
actually-generated output artifacts (gen-top only), and a stable project
identity label. `generated_at` is recorded but explicitly never compared
when explaining staleness — it's informational metadata only.

The point of hashing instead of just recording a timestamp is
**reproducibility, not just history**: two manifests built from
byte-identical inputs, the same FORGE version, and the same command
options produce identical content hashes, regardless of when or on what
machine they were built. `explain_staleness()` compares two manifests and
reports, in plain language, every real reason they differ — schema
version changed, FORGE version changed, IR content changed, command
options changed, a source file was added/removed/modified — rather than
a bare "different" boolean.

This is a genuinely separate mechanism from FORGE's pre-existing,
mtime-based staleness checks (`forge/core/stale_detection.py`,
`forge.verification.stale_artifact` — which compare generated artifacts'
modification times against their declared sources, e.g. detecting that
`design.verification.yml` changed since `verify.flow.yml` was last
generated). The two coexist deliberately; the content-hash manifest adds
a reproducibility guarantee mtimes alone can't give (a file touched but
not actually changed doesn't look stale to the hash-based model), it
doesn't replace the existing mtime checks that `forge verify doctor` and
friends already rely on.

The user-facing hook for the hash-based model is `forge inspect`:

```bash
forge inspect design.yml --contracts-from modules.yml --provenance build/forge/provenance.json
forge inspect design.yml --contracts-from modules.yml --explain-staleness build/forge/provenance.json
```

`--provenance` writes a manifest for the current build; `--explain-staleness`
compares a design's current state against a previously-written manifest
and prints exactly why it would (or wouldn't) need regenerating. See
`docs/development/SCHEMA_VERSIONING.md` for how the provenance manifest's
own schema version is governed.
