#!/usr/bin/env python3
"""passthrough_demo's layer-B dataset adapter.

passthrough_demo's dataset genuinely needs no domain transformation — its
XML file is already FORGE-shaped (layer-A's ``XmlDatasetLoader`` reads it
directly). ``passthrough.identity-xml`` is a trivial identity ``materialize()``
that wraps a layer-A ``SerializedDataset`` into a ``CanonicalDataset``
unchanged. This proves the layer-B protocol is real and wired end to end
without inventing artificial complexity for a plugin that has none.
"""
from __future__ import annotations

from typing import Any, Mapping

from forge.verify.dataset_adapter import (
    CanonicalDataset,
    DatasetSource,
    register_dataset_adapter,
)

ADAPTER_ID = "passthrough.identity-xml"
ADAPTER_VERSION = "1.0"


class IdentityXmlDatasetAdapter:
    """Wraps a layer-A ``SerializedDataset`` (loaded from an already-
    FORGE-shaped XML/JSON file) into a ``CanonicalDataset`` with no
    transformation — passthrough_demo's dataset needs none."""

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


ADAPTER = IdentityXmlDatasetAdapter()

register_dataset_adapter(ADAPTER.adapter_id, ADAPTER)
