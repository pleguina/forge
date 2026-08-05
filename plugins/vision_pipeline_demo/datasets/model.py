#!/usr/bin/env python3
"""Canonical dataset event model.

Every adapter in ``adapters/`` normalizes its own raw source into this one
typed model before any serialization happens — a single normalization
point means downstream consumers (manifest hashing, XML serialization,
the golden model) only ever have to handle one event shape, regardless of
which adapter produced it. Downstream conversion to FORGE's per-pixel
event-dict shape (the shape ``golden_model_provider.py`` and
``gen_stimulus.py`` already consume) happens once, in ``serialize_xml.py``
and the FORGE-registered adapter wrappers
(``forge/verify/tools/dataset_adapter.py``) — never duplicated per-adapter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, Sequence

# Frozen tile geometry — every mandatory dataset tier uses 8x8 tiles,
# matching the design's tile_stats_hls/tile_boundary_rtl tile size.
TILE_WIDTH = 8
TILE_HEIGHT = 8


def tile_id_for(x: int, y: int, *, tile_width: int = TILE_WIDTH, tile_height: int = TILE_HEIGHT) -> int:
    """The one frozen tile-ID formula:
    ``(y // tile_height) * 1024 + (x // tile_width)``. Shared here so no
    adapter re-derives or drifts from it independently."""
    return (y // tile_height) * 1024 + (x // tile_width)


@dataclass(frozen=True)
class PixelTransaction:
    """One logical input transaction presented to the pixel pipeline."""

    pixel: int
    x: int
    y: int
    frame_id: int
    tile_id: int
    end_of_line: bool
    end_of_frame: bool


@dataclass(frozen=True)
class ControlTransaction:
    """A control-plane action associated with an event."""

    cycle: int
    action: str
    values: "Mapping[str, int | bool]"


@dataclass(frozen=True)
class SinkSchedule:
    """Deterministic downstream-ready behavior."""

    ready_by_cycle: "Sequence[bool]"


@dataclass(frozen=True)
class DatasetEvent:
    """Canonical event consumed by the golden model and dataset writers."""

    event_id: str
    width: int
    height: int
    pixels: "Sequence[PixelTransaction]"
    controls: "Sequence[ControlTransaction]" = ()
    sink_schedule: "SinkSchedule | None" = None
    labels: "Mapping[str, Any]" = field(default_factory=dict)
    source_metadata: "Mapping[str, Any]" = field(default_factory=dict)


class DatasetAdapter(Protocol):
    """Adapter from one raw source format to canonical FORGE events.
    ``iter_events`` must yield in a stable, deterministic
    order — callers (manifest hashing, serialization) rely on that order
    being reproducible for the same source/config."""

    name: str
    version: str

    def iter_events(self) -> "Iterable[DatasetEvent]": ...


def pixel_transaction_to_dict(px: PixelTransaction) -> "dict[str, Any]":
    """Canonical, JSON-hashable representation of one transaction —
    the single conversion point :func:`canonical_events_to_dicts` and
    ``manifest.py``'s content hash both build on, so hashing and
    serialization never see two different shapes for the same event."""
    return {
        "pixel": px.pixel,
        "x": px.x,
        "y": px.y,
        "frame_id": px.frame_id,
        "tile_id": px.tile_id,
        "end_of_line": bool(px.end_of_line),
        "end_of_frame": bool(px.end_of_frame),
    }


def dataset_event_to_dict(event: DatasetEvent) -> "dict[str, Any]":
    """Canonical, JSON-hashable representation of one :class:`DatasetEvent`,
    including its nested pixels/controls/labels/source_metadata."""
    return {
        "event_id": event.event_id,
        "width": event.width,
        "height": event.height,
        "pixels": [pixel_transaction_to_dict(p) for p in event.pixels],
        "controls": [
            {"cycle": c.cycle, "action": c.action, "values": dict(c.values)}
            for c in event.controls
        ],
        "sink_schedule": (
            list(event.sink_schedule.ready_by_cycle) if event.sink_schedule is not None else None
        ),
        "labels": dict(event.labels),
        "source_metadata": dict(event.source_metadata),
    }
