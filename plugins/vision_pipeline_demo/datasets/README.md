# vision_pipeline_demo dataset adapters

See [ADR 0001](../../../docs/development/adr/0001-dataset-ownership-boundary.md)
for why dataset loading is owned by this plugin rather than FORGE core.

Three real, project-owned dataset adapters producing the canonical
`DatasetEvent` model (`model.py`):

- `adapters/synthetic.py` — `SyntheticPatternAdapter`: deterministic
  generated patterns (constant, horizontal/vertical edge, corner,
  checkerboard, ramp, seeded noise). No external dependencies.
- `adapters/image_folder.py` — `ImageFolderAdapter`: a directory (or
  explicit file list) of PNG/PGM/JPEG/BMP images, with explicit
  (never library-default) integer-luma grayscale and nearest-neighbor
  resize.
- `adapters/numpy_array.py` — `NumpyArrayAdapter`: a `.npy`/`.npz` array
  under an explicit `HW`/`NHW`/`NHWC` layout; rejects ambiguous shapes.

`serialize_xml.py` flattens canonical events into FORGE's existing
per-pixel XML event shape (the same shape
`forge.verify.dataset_format.XmlDatasetLoader` reads) and writes real
`.xml` fixture files. `manifest.py` builds the `forge.dataset_manifest`
sidecar (source/preprocessing/canonical-event/serialized-dataset hashes)
and provides `check_staleness()` — a pure content-hash comparison against
a manifest's recorded source files, never modification-time-based.

Each adapter also has a thin `ProjectDatasetAdapter` wrapper registered
with FORGE under `vision_pipeline.synthetic` /
`vision_pipeline.image-folder` / `vision_pipeline.numpy-array`
(`../forge/verify/tools/dataset_adapter.py`), reachable through the real
`forge.verify.dataset_service.DatasetService` — the same path
`gen_stimulus.py`/`forge test run` already use for the checked-in XML
fixtures.

## CLI

```bash
python3 cli.py generate-synthetic --output <dir> --dataset-id <id> \
  --width 8 --height 8 --pattern ramp --event-count 1
python3 cli.py import-images --output <dir> --dataset-id <id> \
  --source <image-dir> --width 32 --height 32
python3 cli.py import-numpy --output <dir> --dataset-id <id> \
  --source <file.npy> --layout NHW
python3 cli.py inspect  <manifest.yml>
python3 cli.py validate <manifest.yml> [--source-root <dir>]
python3 cli.py rebuild  <manifest.yml> --source-root <dir> --output <dir>
```

`import-bsds500`/`import-fashion-mnist` are explicitly out of scope: both
would require network downloads, which conflicts with keeping the
mandatory tutorial path offline-capable. See
[ADR 0001](../../../docs/development/adr/0001-dataset-ownership-boundary.md).
