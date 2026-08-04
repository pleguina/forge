"""vision_pipeline_demo dataset-adapter package (release-plan Phase 10,
slice 10.6 — preflight.md §11/§18).

Layer-B, project-owned dataset adapters producing the canonical
:class:`~vision_pipeline_demo.datasets.model.DatasetEvent` model (spec
§18.3), independent of any specific raw source. See ``model.py`` for the
event model, ``adapters/`` for the three required adapters (synthetic,
image-folder, NumPy), ``serialize_xml.py`` for the FORGE-XML writer,
``manifest.py`` for the sidecar manifest + staleness check, and ``cli.py``
for the example-local dataset commands (spec §18.9).
"""
