#!/usr/bin/env python3
"""``ImageFolderAdapter``.

Converts a directory (or explicit file list) of PNG/PGM/JPEG/BMP images
into the canonical pixel-event representation, with every preprocessing
choice made explicit and self-implemented rather than left to a library
default -- a library-default grayscale/resize/rounding policy can change
silently between library versions, which would silently shift the
pixel values a "same input" dataset produces and break bit-exact
reproducibility of golden-model comparisons:

  * grayscale — integer luma, ``(77*R + 150*G + 29*B) >> 8``. The three
    coefficients sum to exactly 256, so an already-grayscale source
    (R == G == B) round-trips its own value exactly (``v*256 >> 8 == v``,
    no lossy re-quantization) -- not a coincidence, the standard integer
    BT.601-derived approximation is chosen partly because of this
    property.
  * resize — nearest-neighbor, hand-rolled (``sx = ox*src_w // dst_w``)
    rather than delegated to ``Image.resize``, so behavior can't shift
    between Pillow versions.

Pillow is used only to *decode* arbitrary source image bytes into raw
RGB samples (an unambiguous, format-defined operation with no semantic
choice attached) -- never for the grayscale/resize steps themselves.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from forge.core.utils.content_hash import hash_bytes, hash_file

from ..model import DatasetEvent, PixelTransaction, tile_id_for

ADAPTER_NAME = "image-folder"
ADAPTER_VERSION = "1.0"

DEFAULT_EXTENSIONS = (".png", ".pgm", ".jpg", ".jpeg", ".bmp")

# Frozen grayscale coefficients -- sum to 256 exactly.
_LUMA_R, _LUMA_G, _LUMA_B = 77, 150, 29


def _load_rgb_pixels(path: Path) -> "tuple[int, int, list[tuple[int, int, int]]]":
    """Decode *path* into ``(width, height, rgb_pixels)``, row-major.
    Raises ValueError (naming *path*) on any unsupported/corrupt file, so
    a bad input file produces a clear, actionable error instead of an
    opaque failure downstream."""
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "ImageFolderAdapter requires Pillow (pip install Pillow) to decode image files."
        ) from exc

    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            width, height = im.size
            pixels = list(im.getdata())
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"Corrupt or unsupported image file: {path} ({exc})") from exc

    return width, height, pixels


def _grayscale(width: int, height: int, rgb_pixels: "list[tuple[int, int, int]]") -> "list[int]":
    """Integer luma conversion, frozen formula (see module docstring)."""
    return [
        (_LUMA_R * r + _LUMA_G * g + _LUMA_B * b) >> 8
        for (r, g, b) in rgb_pixels
    ]


def _nearest_resize(
    src_width: int, src_height: int, src_gray: "list[int]", dst_width: int, dst_height: int,
) -> "list[int]":
    """Hand-rolled nearest-neighbor resize -- explicit and
    library-independent, so behavior can't shift between Pillow versions.
    Row-major output, matching the frozen traversal order."""
    out: "list[int]" = [0] * (dst_width * dst_height)
    for oy in range(dst_height):
        sy = min(src_height - 1, (oy * src_height) // dst_height)
        for ox in range(dst_width):
            sx = min(src_width - 1, (ox * src_width) // dst_width)
            out[oy * dst_width + ox] = src_gray[sy * src_width + sx]
    return out


class ImageFolderAdapter:
    """Directory-of-images adapter.

    Args:
        source: a directory to scan, or an explicit list of file paths.
        width, height: target frame dimensions after resize.
        extensions: accepted suffixes when *source* is a directory
            (case-insensitive). Ignored when *source* is an explicit file
            list.
        max_events: cap on the number of frames produced (stable-order
            truncation, not random sampling).
    """

    name = ADAPTER_NAME
    version = ADAPTER_VERSION

    def __init__(
        self,
        *,
        source: "Path | Sequence[Path]",
        width: int,
        height: int,
        extensions: "Sequence[str]" = DEFAULT_EXTENSIONS,
        max_events: "int | None" = None,
    ) -> None:
        if width <= 0 or height <= 0:
            raise ValueError(f"width/height must be positive, got {width}x{height}")

        if isinstance(source, (list, tuple)):
            files = [Path(p) for p in source]
        else:
            root = Path(source)
            if not root.is_dir():
                raise ValueError(f"ImageFolderAdapter source directory does not exist: {root}")
            lowered_ext = {e.lower() for e in extensions}
            files = [p for p in root.iterdir() if p.is_file() and p.suffix.lower() in lowered_ext]

        # Stable lexical order -- by filename string, never filesystem
        # iteration order, so frame_id assignment is deterministic and
        # reproducible across machines and filesystems.
        files.sort(key=lambda p: p.name)
        if max_events is not None:
            files = files[:max_events]
        if not files:
            raise ValueError(f"ImageFolderAdapter found no matching image files under {source!r}")

        self._files = files
        self._width = width
        self._height = height

    def iter_events(self) -> "Iterable[DatasetEvent]":
        for frame_id, path in enumerate(self._files):
            source_hash = hash_file(path)  # before preprocessing
            src_width, src_height, rgb = _load_rgb_pixels(path)
            gray = _grayscale(src_width, src_height, rgb)
            resized = _nearest_resize(src_width, src_height, gray, self._width, self._height)
            converted_hash = hash_bytes(bytes(resized))  # after preprocessing

            pixels: "list[PixelTransaction]" = []
            for y in range(self._height):
                for x in range(self._width):
                    pixels.append(
                        PixelTransaction(
                            pixel=resized[y * self._width + x],
                            x=x,
                            y=y,
                            frame_id=frame_id,
                            tile_id=tile_id_for(x, y),
                            end_of_line=(x == self._width - 1),
                            end_of_frame=(x == self._width - 1 and y == self._height - 1),
                        )
                    )

            yield DatasetEvent(
                event_id=f"image-folder-{frame_id:06d}",
                width=self._width,
                height=self._height,
                pixels=tuple(pixels),
                source_metadata={
                    "source_file": str(path),
                    "source_image_hash": source_hash,
                    "converted_image_hash": converted_hash,
                    "source_width": src_width,
                    "source_height": src_height,
                },
            )
