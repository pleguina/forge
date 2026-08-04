#!/usr/bin/env python3
"""Canonical-event -> FORGE-XML serialization (release-plan Phase 10,
slice 10.6 — preflight.md §18.7).

The framework's per-pixel event-dict shape (what
``forge.verify.dataset_format.XmlDatasetLoader`` produces, and what
``golden_model_provider.py``/``gen_stimulus.py`` already consume via
``ev["in"]["pixel"]`` etc.) is flatter than :class:`DatasetEvent` — one
``<event>`` per *pixel transaction*, not per frame. :func:`flatten_events`
is the single place that flattening happens; both the on-disk XML writer
below and the in-memory FORGE ``ProjectDatasetAdapter`` wrappers
(``forge/verify/tools/dataset_adapter.py``) call it, so there is exactly
one flattening implementation, never two that could drift apart.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Sequence

from .model import DatasetEvent

ROOT_TAG = "vision_pipeline_events"


def flatten_events(events: "Sequence[DatasetEvent]") -> "list[dict[str, Any]]":
    """Flatten *events* (frames of pixel transactions) into FORGE's
    per-pixel event-dict shape: ``[{"in": {"pixel": "0x..", "x": "0", ...}}, ...]``
    — string-valued attrs, matching exactly what
    :class:`~forge.verify.dataset_format.XmlDatasetLoader` produces when it
    parses ``<in pixel="0x.." .../>`` attributes from a real XML file, so a
    :class:`~forge.verify.dataset_adapter.CanonicalDataset` built directly
    from this is byte-identical to one loaded from this module's own
    XML output.
    """
    flat: "list[dict[str, Any]]" = []
    for event in events:
        for px in event.pixels:
            flat.append({
                "in": {
                    "pixel": f"0x{px.pixel:02x}",
                    "x": str(px.x),
                    "y": str(px.y),
                    "frame_id": str(px.frame_id),
                    "tile_id": str(px.tile_id),
                    "end_of_line": "1" if px.end_of_line else "0",
                    "end_of_frame": "1" if px.end_of_frame else "0",
                    "pixel_valid": "1",
                }
            })
    return flat


def event_ids_for(flat_events: "Sequence[dict[str, Any]]") -> "list[str]":
    """Contiguous string ids for a flattened event list -- ``"0", "1", ...``,
    matching :class:`~forge.verify.dataset_format.XmlDatasetLoader`'s own
    ``<event id="N">`` numbering convention."""
    return [str(i) for i in range(len(flat_events))]


def serialize_to_xml(events: "Sequence[DatasetEvent]") -> str:
    """Render *events* as a FORGE-XML document string (spec §18.7)."""
    root = ET.Element(ROOT_TAG)
    for idx, flat in enumerate(flatten_events(events)):
        event_elem = ET.SubElement(root, "event", {"id": str(idx)})
        ET.SubElement(event_elem, "in", flat["in"])

    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode") + "\n"


def write_xml(events: "Sequence[DatasetEvent]", path: "Path | str") -> Path:
    """Serialize *events* and write them to *path*, creating parent
    directories as needed. Returns the written path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_to_xml(events), encoding="utf-8")
    return path
