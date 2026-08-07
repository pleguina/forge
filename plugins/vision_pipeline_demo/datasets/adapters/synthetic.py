#!/usr/bin/env python3
"""``SyntheticPatternAdapter``.

Generates deterministic pixel-grid patterns with no external
dependencies (stdlib ``random`` only, seeded — never OS entropy), so the
mandatory tutorial and CI paths never require a network fetch or a
bundled image corpus. This is a *generator* adapter, not a source-file
reader: :class:`DatasetSource` carries no meaningful
``raw_path``/``serialized`` for it (see the FORGE-registered wrapper in
``forge/verify/tools/dataset_adapter.py`` for how that's reconciled with
:class:`~forge.verification.dataset_adapter.ProjectDatasetAdapter`'s protocol).

Traversal order is row-major (frozen) — the only order consistent with
``end_of_line``/``end_of_frame`` semantics.
"""
from __future__ import annotations

import random
from typing import Iterable, Mapping, Sequence

from ..model import (
    ControlTransaction,
    DatasetEvent,
    PixelTransaction,
    SinkSchedule,
    tile_id_for,
)

ADAPTER_NAME = "synthetic"
ADAPTER_VERSION = "1.0"

KNOWN_PATTERNS = (
    "constant",
    "horizontal_edge",
    "vertical_edge",
    "corner",
    "checkerboard",
    "ramp",
    "noise",
)


def _pixel_value(pattern: str, x: int, y: int, width: int, height: int, rng: "random.Random") -> int:
    if pattern == "constant":
        return 128
    if pattern == "horizontal_edge":
        return 255 if y >= height // 2 else 0
    if pattern == "vertical_edge":
        return 255 if x >= width // 2 else 0
    if pattern == "corner":
        return 255 if (x == 0 and y == 0) else 0
    if pattern == "checkerboard":
        return 255 if (x + y) % 2 == 0 else 0
    if pattern == "ramp":
        return (x * 255) // max(1, width - 1)
    if pattern == "noise":
        return rng.randint(0, 255)
    raise ValueError(
        f"Unknown synthetic pattern {pattern!r}. Known patterns: {', '.join(KNOWN_PATTERNS)}"
    )


class SyntheticPatternAdapter:
    """Deterministic generated-pattern adapter.

    Args:
        width: frame width in pixels.
        height: frame height in pixels.
        patterns: pattern names to cycle through, one per generated frame
            (``patterns[frame_index % len(patterns)]``). Must each be one
            of :data:`KNOWN_PATTERNS`.
        seed: base deterministic seed. Only the ``noise`` pattern actually
            consumes randomness; every other pattern is a pure function of
            (x, y, width, height) and ignores the seed entirely, so two
            runs with different seeds still produce byte-identical output
            for non-noise patterns (this is deliberate, not an oversight).
        event_count: number of frames to generate.
        controls: optional control actions, replicated onto every
            generated frame unchanged.
        sink_ready_schedule: optional sink-ready schedule, replicated onto
            every generated frame unchanged.
    """

    name = ADAPTER_NAME
    version = ADAPTER_VERSION

    def __init__(
        self,
        *,
        width: int,
        height: int,
        patterns: "Sequence[str]",
        seed: int = 0,
        event_count: int = 1,
        controls: "Sequence[Mapping[str, object]]" = (),
        sink_ready_schedule: "Sequence[bool] | None" = None,
    ) -> None:
        if width <= 0 or height <= 0:
            raise ValueError(f"width/height must be positive, got {width}x{height}")
        if not patterns:
            raise ValueError("patterns must be a non-empty sequence")
        unknown = [p for p in patterns if p not in KNOWN_PATTERNS]
        if unknown:
            raise ValueError(
                f"Unknown synthetic pattern(s) {unknown!r}. Known patterns: {', '.join(KNOWN_PATTERNS)}"
            )
        if event_count <= 0:
            raise ValueError(f"event_count must be positive, got {event_count}")

        self._width = width
        self._height = height
        self._patterns = list(patterns)
        self._seed = seed
        self._event_count = event_count
        self._controls = list(controls)
        self._sink_ready_schedule = list(sink_ready_schedule) if sink_ready_schedule is not None else None

    def iter_events(self) -> "Iterable[DatasetEvent]":
        for frame_id in range(self._event_count):
            pattern = self._patterns[frame_id % len(self._patterns)]
            # One independent, deterministic RNG per frame, derived from the
            # base seed + frame index -- so inserting/removing frames never
            # perturbs an already-generated frame's own noise values. A
            # plain int combination (not a tuple) -- random.Random's own
            # hash-based tuple seeding is deprecated since Python 3.9.
            rng = random.Random((self._seed << 32) ^ frame_id)

            pixels: "list[PixelTransaction]" = []
            for y in range(self._height):
                for x in range(self._width):
                    pixels.append(
                        PixelTransaction(
                            pixel=_pixel_value(pattern, x, y, self._width, self._height, rng),
                            x=x,
                            y=y,
                            frame_id=frame_id,
                            tile_id=tile_id_for(x, y),
                            end_of_line=(x == self._width - 1),
                            end_of_frame=(x == self._width - 1 and y == self._height - 1),
                        )
                    )

            controls = tuple(
                ControlTransaction(cycle=c["cycle"], action=c["action"], values=dict(c.get("values", {})))
                for c in self._controls
            )
            sink_schedule = (
                SinkSchedule(ready_by_cycle=tuple(self._sink_ready_schedule))
                if self._sink_ready_schedule is not None
                else None
            )

            yield DatasetEvent(
                event_id=f"synthetic-{pattern}-{frame_id:06d}",
                width=self._width,
                height=self._height,
                pixels=tuple(pixels),
                controls=controls,
                sink_schedule=sink_schedule,
                source_metadata={"pattern": pattern, "seed": self._seed, "frame_index": frame_id},
            )
