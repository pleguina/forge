"""vision_pipeline_demo dataset-adapter package.

Project-owned dataset adapters producing the canonical
:class:`~vision_pipeline_demo.datasets.model.DatasetEvent` model,
independent of any specific raw source -- see
docs/development/adr/0001-dataset-ownership-boundary.md for why this
lives in the plugin rather than FORGE core. See ``model.py`` for the
event model, ``adapters/`` for the three required adapters (synthetic,
image-folder, NumPy), ``serialize_xml.py`` for the FORGE-XML writer,
``manifest.py`` for the sidecar manifest + staleness check, and ``cli.py``
for the example-local dataset commands.
"""
