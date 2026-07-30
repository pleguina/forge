#!/usr/bin/env python3
"""vision_pipeline_demo's layer-B dataset adapter (release-plan Phase 10,
slice 10.1).

Like passthrough_demo's, this dataset genuinely needs no domain
transformation — its XML file is already FORGE-shaped (layer-A's
``XmlDatasetLoader`` reads it directly) and the algorithm is simple
enough that no real preprocessing happens before the golden model runs.
An identity ``materialize()`` here is an honest reflection of that, not a
placeholder standing in for missing work.
"""
from __future__ import annotations

from typing import Mapping

from forge.verify.dataset_adapter import (
    CanonicalDataset,
    DatasetSource,
    register_dataset_adapter,
)

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
