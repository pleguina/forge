#!/usr/bin/env python3
"""``NumpyArrayAdapter`` (release-plan Phase 10, slice 10.6 —
preflight.md §18.4/§18.1 Tier B).

Loads a ``.npy``/``.npz`` array under one of three explicit layouts --
``HW`` (one frame), ``NHW`` (N grayscale frames), ``NHWC`` (N frames, 1 or
3 channels) -- and rejects anything else outright rather than guessing
which axis is which (spec §18.4: "must reject ambiguous or unsupported
shapes rather than guessing").
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ..model import DatasetEvent, PixelTransaction, tile_id_for

ADAPTER_NAME = "numpy-array"
ADAPTER_VERSION = "1.0"

SUPPORTED_LAYOUTS = ("HW", "NHW", "NHWC")

# Frozen grayscale coefficients (preflight.md §18.5), reused for NHWC's
# 3-channel case -- same formula image_folder.py uses, so a 3-channel
# NumPy source and an equivalent image-folder source normalize identically.
_LUMA_R, _LUMA_G, _LUMA_B = 77, 150, 29


def _require_numpy():
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "NumpyArrayAdapter requires numpy (pip install numpy) to load .npy/.npz files."
        ) from exc
    return np


def _map_value_range(np, arr, *, source_min: float, source_max: float):
    """Explicit, declared linear rescale to the 0-255 domain -- nearest
    rounding, saturating clamp (spec §18.5's frozen value-mapping shape,
    same rounding/saturation policy as pixel_normalizer.cpp's own clamp).
    A no-op (only a dtype cast) when the array is already the 0-255 uint8
    domain, since ``source_min=0, source_max=255`` is the default."""
    if source_max <= source_min:
        raise ValueError(f"value_range must have source_max > source_min, got ({source_min}, {source_max})")
    scaled = (arr.astype("float64") - source_min) * (255.0 / (source_max - source_min))
    rounded = np.rint(scaled)
    return np.clip(rounded, 0, 255).astype("uint8")


def _to_grayscale(np, frame):
    """*frame* is HxW (passthrough) or HxWxC (C in {1, 3}); returns HxW uint8."""
    if frame.ndim == 2:
        return frame
    channels = frame.shape[-1]
    if channels == 1:
        return frame[..., 0]
    if channels == 3:
        r, g, b = frame[..., 0].astype("int64"), frame[..., 1].astype("int64"), frame[..., 2].astype("int64")
        return ((_LUMA_R * r + _LUMA_G * g + _LUMA_B * b) >> 8).astype("uint8")
    raise ValueError(f"NumpyArrayAdapter only supports 1 or 3 channels, got shape with {channels} channels")


class NumpyArrayAdapter:
    """``.npy``/``.npz`` array adapter (spec §18.4).

    Args:
        source: path to a ``.npy`` or ``.npz`` file.
        layout: one of :data:`SUPPORTED_LAYOUTS`.
        array_key: required for ``.npz`` (which array to read); ignored
            for ``.npy``.
        value_range: ``(source_min, source_max)`` the array's raw values
            are declared to fall within; rescaled to 0-255 if not already.
        label_key: optional ``.npz`` key for a length-N label array,
            recorded into each frame's ``labels``.
        max_events: cap on frame count for ``NHW``/``NHWC`` (stable
            leading-index truncation, not random sampling).
    """

    name = ADAPTER_NAME
    version = ADAPTER_VERSION

    def __init__(
        self,
        *,
        source: Path,
        layout: str,
        array_key: "str | None" = None,
        value_range: "tuple[float, float]" = (0, 255),
        label_key: "str | None" = None,
        max_events: "int | None" = None,
    ) -> None:
        if layout not in SUPPORTED_LAYOUTS:
            raise ValueError(f"Unsupported layout {layout!r}. Supported layouts: {SUPPORTED_LAYOUTS}")

        np = _require_numpy()
        source = Path(source)
        if not source.is_file():
            raise ValueError(f"NumpyArrayAdapter source file does not exist: {source}")

        labels: "list[object] | None" = None
        if source.suffix.lower() == ".npz":
            with np.load(source) as npz:
                keys = list(npz.files)
                if array_key is None:
                    if len(keys) != 1:
                        raise ValueError(
                            f"{source}: array_key is required for a .npz with multiple arrays "
                            f"(found: {keys})"
                        )
                    array_key = keys[0]
                if array_key not in npz.files:
                    raise ValueError(f"{source}: array_key {array_key!r} not found (available: {keys})")
                array = npz[array_key]
                if label_key is not None:
                    if label_key not in npz.files:
                        raise ValueError(f"{source}: label_key {label_key!r} not found (available: {keys})")
                    labels = list(npz[label_key])
        elif source.suffix.lower() == ".npy":
            array = np.load(source)
        else:
            raise ValueError(f"NumpyArrayAdapter only supports .npy/.npz, got: {source}")

        expected_ndim = {"HW": 2, "NHW": 3, "NHWC": 4}[layout]
        if array.ndim != expected_ndim:
            raise ValueError(
                f"{source}: layout {layout!r} requires a {expected_ndim}-D array, "
                f"got shape {array.shape} ({array.ndim}-D). Refusing to guess the axis mapping."
            )
        if layout == "NHWC" and array.shape[-1] not in (1, 3):
            raise ValueError(
                f"{source}: NHWC layout requires 1 or 3 channels, got shape {array.shape}"
            )

        if layout == "HW":
            frames = array[None, ...]
            if labels is not None:
                raise ValueError(f"{source}: label_key is not meaningful for a single-frame HW array")
        else:
            frames = array
            if labels is not None and len(labels) != frames.shape[0]:
                raise ValueError(
                    f"{source}: label array length ({len(labels)}) does not match frame count "
                    f"({frames.shape[0]})"
                )

        if max_events is not None:
            frames = frames[:max_events]
            if labels is not None:
                labels = labels[:max_events]

        source_min, source_max = value_range
        self._np = np
        self._source = source
        self._layout = layout
        self._frames = frames
        self._labels = labels
        self._source_min = source_min
        self._source_max = source_max

    def iter_events(self) -> "Iterable[DatasetEvent]":
        np = self._np
        for frame_id in range(self._frames.shape[0]):
            frame = self._frames[frame_id]
            # Rescale to the 0-255 domain first (per-element, all channels),
            # then reduce channels -- luma combination assumes an already
            # 0-255 domain, so it must run after value-range mapping, not
            # before.
            frame_255 = _map_value_range(np, frame, source_min=self._source_min, source_max=self._source_max)
            gray = _to_grayscale(np, frame_255)
            height, width = gray.shape

            pixels: "list[PixelTransaction]" = []
            for y in range(height):
                for x in range(width):
                    pixels.append(
                        PixelTransaction(
                            pixel=int(gray[y, x]),
                            x=x,
                            y=y,
                            frame_id=frame_id,
                            tile_id=tile_id_for(x, y),
                            end_of_line=(x == width - 1),
                            end_of_frame=(x == width - 1 and y == height - 1),
                        )
                    )

            labels = {"label": self._labels[frame_id]} if self._labels is not None else {}

            yield DatasetEvent(
                event_id=f"numpy-array-{frame_id:06d}",
                width=width,
                height=height,
                pixels=tuple(pixels),
                labels=labels,
                source_metadata={"source_file": str(self._source), "layout": self._layout, "frame_index": frame_id},
            )
