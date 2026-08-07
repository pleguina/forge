#!/usr/bin/env python3
"""vision_pipeline_demo's layer-B dataset adapters — the FORGE-facing
registration shims for this plugin's project-owned dataset logic.

``PixelStreamXmlDatasetAdapter``: this dataset genuinely needs no
domain transformation — its XML file is already FORGE-shaped (layer-A's
``XmlDatasetLoader`` reads it directly) and the algorithm is simple
enough that no real preprocessing happens before the golden model runs.
An identity ``materialize()`` here is an honest reflection of that, not a
placeholder standing in for missing work.

``SyntheticDatasetAdapter``/``ImageFolderDatasetAdapter``/
``NumpyArrayDatasetAdapter``: thin
:class:`~forge.verification.dataset_adapter.ProjectDatasetAdapter` wrappers
around the real adapter logic in
``plugins/vision_pipeline_demo/datasets/adapters/`` — see
docs/development/adr/0001-dataset-ownership-boundary.md for why dataset
loading is project-owned rather than living in FORGE core. Do not
duplicate the adapter implementation when adding a wrapper: each wrapper
here only calls ``iter_events()`` and flattens the result via
``datasets/serialize_xml.py``'s ``flatten_events()``, the same
flattening ``datasets/cli.py`` uses to write on-disk XML fixtures. No
adapter logic is reimplemented here.
"""
from __future__ import annotations

import importlib.util as _ilu
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from forge.verification.dataset_adapter import (
    CanonicalDataset,
    DatasetSource,
    register_dataset_adapter,
)
from forge.verification.dataset_format import (
    DATASET_SCHEMA,
    DatasetMetadata,
    EnvironmentMetadata,
    SemanticMetadata,
    compute_events_content_hash,
)

# The `datasets/` package lives at the plugin root (two levels above
# forge/verify/tools/) -- see docs/development/adr/0001-dataset-ownership-
# boundary.md for why dataset loading lives at the plugin root rather than
# under forge/verify/.
#
# It is loaded by explicit file path, NOT by adding the plugin root to
# sys.path -- a real bug found while wiring this up: the plugin root
# also contains its own `forge/` subdirectory (verification assets, not a
# Python package -- `forge/designs/`, `forge/verify/`, etc., no
# `__init__.py`). Adding the plugin root to sys.path lets Python's
# sys.path-based PathFinder resolve top-level `forge` as a *namespace*
# package rooted there instead, since PathFinder runs before the real
# `forge` editable-install's own meta-path finder (registered via
# `sys.meta_path.append(...)`, i.e. strictly after PathFinder) ever gets
# asked -- so `import forge.verification.results` then fails with
# ``ModuleNotFoundError: No module named 'forge.verification.results'``
# (`forge.verification` resolves to the asset directory, which has no
# `results.py`), even though the same import works fine from any other
# entry point. Loading `datasets` via `importlib.util.spec_from_file_location`
# with an explicit `submodule_search_locations` sidesteps sys.path (and
# this collision) entirely, mirroring `conftest.py`'s own
# `_load_by_path` precedent for avoiding cross-plugin bare-name clashes.
_PLUGIN_ROOT = Path(__file__).resolve().parents[3]


def _load_datasets_package():
    if "datasets" in sys.modules:
        return sys.modules["datasets"]
    datasets_dir = _PLUGIN_ROOT / "datasets"
    spec = _ilu.spec_from_file_location(
        "datasets", datasets_dir / "__init__.py", submodule_search_locations=[str(datasets_dir)],
    )
    module = _ilu.module_from_spec(spec)   # type: ignore[union-attr]
    sys.modules["datasets"] = module
    spec.loader.exec_module(module)        # type: ignore[union-attr]
    return module


_load_datasets_package()

from datasets.adapters.image_folder import DEFAULT_EXTENSIONS, ImageFolderAdapter  # noqa: E402
from datasets.adapters.numpy_array import NumpyArrayAdapter  # noqa: E402
from datasets.adapters.synthetic import SyntheticPatternAdapter  # noqa: E402
from datasets.serialize_xml import event_ids_for, flatten_events  # noqa: E402

ADAPTER_ID = "vision_pipeline.pixel-stream-xml"
ADAPTER_VERSION = "1.0"


class PixelStreamXmlDatasetAdapter:
    """Wraps a layer-A ``SerializedDataset`` (loaded from the already
    ``forge.pixel_stream.v1``-shaped golden XML) into a ``CanonicalDataset``
    with no transformation."""

    adapter_id = ADAPTER_ID
    adapter_version = ADAPTER_VERSION

    def materialize(
        self,
        source: DatasetSource,
        config: "Mapping[str, object]",
    ) -> CanonicalDataset:
        if source.serialized is None:
            raise ValueError(
                f"{self.adapter_id} requires an already-FORGE-shaped source "
                f"(DatasetSource.serialized); got raw_path={source.raw_path!r}. "
                f"This adapter never reads raw external data directly."
            )
        return CanonicalDataset(
            events=source.serialized.events,
            metadata=source.serialized.metadata,
        )


ADAPTER = PixelStreamXmlDatasetAdapter()

register_dataset_adapter(ADAPTER.adapter_id, ADAPTER)


# ── Real dataset adapters (synthetic / image-folder / numpy) ─────────────

def _canonical_dataset_from_events(
    events: "list",
    *,
    adapter_id: str,
    adapter_version: str,
    source_path: "str | None",
) -> CanonicalDataset:
    """Shared plumbing every real-data wrapper below uses: flatten canonical
    ``DatasetEvent``s into FORGE's per-pixel event-dict shape and wrap
    them in a real ``CanonicalDataset`` + ``DatasetMetadata`` -- the exact
    same shape :class:`PixelStreamXmlDatasetAdapter` above hands back,
    just built in-memory instead of loaded from a checked-in XML file."""
    flat = flatten_events(events)
    metadata = DatasetMetadata(
        schema=DATASET_SCHEMA,
        event_ids=event_ids_for(flat),
        semantic=SemanticMetadata(
            source_content_hash=compute_events_content_hash(flat),
            adapter_id=adapter_id,
            adapter_version=adapter_version,
        ),
        environment=EnvironmentMetadata(
            source_path=source_path or "<generated>",
            generated_at=datetime.now(timezone.utc).isoformat(),
        ),
    )
    return CanonicalDataset(events=flat, metadata=metadata)


SYNTHETIC_ADAPTER_ID = "vision_pipeline.synthetic"
SYNTHETIC_ADAPTER_VERSION = "1.0"


class SyntheticDatasetAdapter:
    """Wraps ``datasets.adapters.synthetic.SyntheticPatternAdapter``.
    A pure generator -- needs no raw source at all, so
    *source* is accepted but never inspected; every input comes from
    *config* (``width``, ``height``, ``patterns``, ``seed``,
    ``event_count``, optional ``controls``/``sink_ready_schedule``)."""

    adapter_id = SYNTHETIC_ADAPTER_ID
    adapter_version = SYNTHETIC_ADAPTER_VERSION

    def materialize(self, source: DatasetSource, config: "Mapping[str, Any]") -> CanonicalDataset:
        adapter = SyntheticPatternAdapter(
            width=config["width"],
            height=config["height"],
            patterns=config["patterns"],
            seed=config.get("seed", 0),
            event_count=config.get("event_count", 1),
            controls=config.get("controls", ()),
            sink_ready_schedule=config.get("sink_ready_schedule"),
        )
        events = list(adapter.iter_events())
        return _canonical_dataset_from_events(
            events, adapter_id=self.adapter_id, adapter_version=self.adapter_version, source_path=None,
        )


SYNTHETIC_ADAPTER = SyntheticDatasetAdapter()
register_dataset_adapter(SYNTHETIC_ADAPTER.adapter_id, SYNTHETIC_ADAPTER)


IMAGE_FOLDER_ADAPTER_ID = "vision_pipeline.image-folder"
IMAGE_FOLDER_ADAPTER_VERSION = "1.0"


class ImageFolderDatasetAdapter:
    """Wraps ``datasets.adapters.image_folder.ImageFolderAdapter``.
    Requires ``source.raw_path`` (a directory, or an
    explicit file list passed via ``config["files"]``)."""

    adapter_id = IMAGE_FOLDER_ADAPTER_ID
    adapter_version = IMAGE_FOLDER_ADAPTER_VERSION

    def materialize(self, source: DatasetSource, config: "Mapping[str, Any]") -> CanonicalDataset:
        files = config.get("files")
        image_source = [Path(f) for f in files] if files else source.raw_path
        if image_source is None:
            raise ValueError(
                f"{self.adapter_id} requires DatasetSource.raw_path (an image directory) "
                f"or config['files'] (an explicit file list)."
            )
        adapter = ImageFolderAdapter(
            source=image_source,
            width=config["width"],
            height=config["height"],
            extensions=config.get("extensions", DEFAULT_EXTENSIONS),
            max_events=config.get("max_events"),
        )
        events = list(adapter.iter_events())
        return _canonical_dataset_from_events(
            events, adapter_id=self.adapter_id, adapter_version=self.adapter_version,
            source_path=str(image_source),
        )


IMAGE_FOLDER_ADAPTER = ImageFolderDatasetAdapter()
register_dataset_adapter(IMAGE_FOLDER_ADAPTER.adapter_id, IMAGE_FOLDER_ADAPTER)


NUMPY_ARRAY_ADAPTER_ID = "vision_pipeline.numpy-array"
NUMPY_ARRAY_ADAPTER_VERSION = "1.0"


class NumpyArrayDatasetAdapter:
    """Wraps ``datasets.adapters.numpy_array.NumpyArrayAdapter``.
    Requires ``source.raw_path`` (a ``.npy``/``.npz`` file)."""

    adapter_id = NUMPY_ARRAY_ADAPTER_ID
    adapter_version = NUMPY_ARRAY_ADAPTER_VERSION

    def materialize(self, source: DatasetSource, config: "Mapping[str, Any]") -> CanonicalDataset:
        if source.raw_path is None:
            raise ValueError(f"{self.adapter_id} requires DatasetSource.raw_path (a .npy/.npz file).")
        adapter = NumpyArrayAdapter(
            source=source.raw_path,
            layout=config["layout"],
            array_key=config.get("array_key"),
            value_range=tuple(config.get("value_range", (0, 255))),
            label_key=config.get("label_key"),
            max_events=config.get("max_events"),
        )
        events = list(adapter.iter_events())
        return _canonical_dataset_from_events(
            events, adapter_id=self.adapter_id, adapter_version=self.adapter_version,
            source_path=str(source.raw_path),
        )


NUMPY_ARRAY_ADAPTER = NumpyArrayDatasetAdapter()
register_dataset_adapter(NUMPY_ARRAY_ADAPTER.adapter_id, NUMPY_ARRAY_ADAPTER)
