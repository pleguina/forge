# ADR 0001: Dataset ownership boundary

## Status

Accepted.

## Context

`vision_pipeline_demo` needs to turn real images, synthetic patterns, and
NumPy arrays into the canonical per-pixel event stream its verification
flows drive through the design under test. FORGE core does not yet
provide a generic, domain-neutral "load a dataset" command — dataset
shape, preprocessing, and identity are inherently project-specific (a
vision project's canonical event is a pixel; a different project's might
be a packet or a register transaction).

## Decision

Dataset loading, adapters, and the dataset CLI are **project-owned**,
living entirely under `plugins/vision_pipeline_demo/datasets/`:

- `datasets/model.py` defines the canonical `DatasetEvent` — every
  adapter normalizes into this shape before serialization.
- `datasets/adapters/{synthetic,image_folder,numpy_array}.py` are the
  three supported sources. Each is registered with FORGE's generic
  `DatasetService` via a thin project-owned wrapper, but the adapter
  logic itself (pattern generation, image decoding, NumPy layout
  handling) stays in the plugin.
- `datasets/cli.py` exposes `generate-synthetic`, `import-images`, and
  `rebuild` as example-local commands. This is a deliberate, temporary
  boundary: once FORGE core ships a generic top-level dataset command,
  these commands should be re-hosted there. Until then, they remain
  plugin-local rather than blocking on unbuilt core infrastructure.
- BSDS500 and Fashion-MNIST importers are explicitly **out of scope**
  (optional, deferred) — they would require network downloads, which
  conflicts with keeping the mandatory tutorial path offline-capable.
  The synthetic and local-file (image-folder/NumPy) adapters cover every
  mandatory tutorial and CI path without a network dependency.

## Consequences

- A project author reading this plugin should look in `datasets/` for
  anything about *what* data the pipeline consumes, and in
  `forge/verify/tools/golden_model_provider.py` for *what the pipeline
  should output* given that data — the two are deliberately separate
  concerns (see [ADR 0004](0004-golden-model-provider-boundary.md)).
- If FORGE core later adds a generic dataset command, `datasets/cli.py`
  should be migrated to register against it rather than duplicated.
- Manifest/content-hash functions over `DatasetEvent` must exclude
  path-derived provenance fields (e.g. `source_metadata`) — relocating a
  source directory must not silently change a dataset's semantic hash.
