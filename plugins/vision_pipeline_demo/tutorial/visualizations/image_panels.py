#!/usr/bin/env python3
"""Deterministic image-domain figure renderers.

Every figure this module produces is rendered directly from a real
golden dataset (`forge/verify/schemas/data/*.xml`) and this plugin's own
`GoldenModelProvider` implementations
(`forge/verify/tools/golden_model_provider.py`) — never from invented or
hand-typed pixel values. `forge.verify.dataset_format.XmlDatasetLoader`
(FORGE core) does the raw XML parsing; everything downstream of that —
reconstructing a 2D grid, choosing a color scale, drawing a tile
overlay — is vision-domain-specific and belongs entirely to this
package.

Determinism (no timestamps, no randomness, fixed dimensions, identical
bytes from identical input): every render call takes an explicit,
already-computed 2D array — never re-runs anything with an unseeded
random component — and PIL's own PNG encoder emits byte-identical
output for byte-identical pixel data (verified empirically before this
module was written).
"""
from __future__ import annotations

import importlib.util as _ilu
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageDraw, ImageFont

_PLUGIN_ROOT = Path(__file__).resolve().parents[2]


def _load_golden_model_provider_module():
    """Load this plugin's own golden_model_provider.py by explicit file
    path, mirroring bootstrap.py's own precedent for avoiding cross-plugin
    bare-name clashes — see
    plugins/vision_pipeline_demo/forge/verify/tools/tests/conftest.py.
    """
    path = _PLUGIN_ROOT / "forge" / "verify" / "tools" / "golden_model_provider.py"
    spec = _ilu.spec_from_file_location("vpd_golden_model_provider", path)
    module = _ilu.module_from_spec(spec)  # type: ignore[union-attr]
    spec.loader.exec_module(module)       # type: ignore[union-attr]
    return module


def load_dataset(xml_path: Path):
    """Load a real golden dataset XML via FORGE's own loader (no
    project-local XML parsing duplicated here)."""
    from forge.verify.dataset_format import XmlDatasetLoader
    return XmlDatasetLoader().load(str(xml_path))


def _as_int(value: Any) -> int:
    if isinstance(value, str):
        return int(value, 0)
    return int(value)


def grid_from_events(
    events: Sequence[dict],
    width: int,
    height: int,
    value_field: str,
    *,
    source: str = "in",
) -> list:
    """Reconstruct a `height` x `width` grid of ints from a real event
    list, using each event's own x/y coordinate (always under `in`) to
    place `value_field` (read from `in` or `expected`, per `source`).
    """
    grid = [[0] * width for _ in range(height)]
    for ev in events:
        x = _as_int(ev["in"]["x"])
        y = _as_int(ev["in"]["y"])
        value = _as_int(ev[source][value_field])
        grid[y][x] = value
    return grid


def render_grayscale_panel(
    grid: Sequence[Sequence[int]],
    output_path: Path,
    *,
    scale: int = 16,
    value_max: int = 255,
) -> None:
    """Render `grid` (values in [0, value_max]) as a deterministic
    grayscale PNG, nearest-neighbor upscaled by `scale` for visibility at
    tutorial figure sizes.
    """
    height = len(grid)
    width = len(grid[0]) if height else 0
    img = Image.new("L", (width, height))
    pixels = img.load()
    for y, row in enumerate(grid):
        for x, value in enumerate(row):
            pixels[x, y] = min(255, int(value * 255 / value_max)) if value_max != 255 else min(255, value)
    if scale > 1:
        img = img.resize((width * scale, height * scale), Image.NEAREST)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)


def render_diff_map(
    grid_a: Sequence[Sequence[int]],
    grid_b: Sequence[Sequence[int]],
    output_path: Path,
    *,
    scale: int = 16,
) -> int:
    """Render the pixel-wise absolute difference between two same-shaped
    grids. Returns the real total absolute difference (0 for a passing
    comparison) so a caller can assert on it rather than eyeball the
    image.
    """
    height = len(grid_a)
    width = len(grid_a[0]) if height else 0
    diff_grid = [[abs(grid_a[y][x] - grid_b[y][x]) for x in range(width)] for y in range(height)]
    total = sum(sum(row) for row in diff_grid)
    render_grayscale_panel(diff_grid, output_path, scale=scale, value_max=max(1, max((max(row) for row in diff_grid), default=1)))
    return total


def render_tile_overlay(
    base_grid: Sequence[Sequence[int]],
    tile_records: Sequence[dict],
    tile_width: int,
    tile_height: int,
    output_path: Path,
    *,
    scale: int = 16,
) -> None:
    """Draw tile boundary grid lines and each real tile's own mean value
    over the base (normalized-pixel) image. `tile_records` are the real,
    deduplicated per-(frame_id, tile_id) records a GoldenModelProvider
    computed — not invented summary statistics.
    """
    height = len(base_grid)
    width = len(base_grid[0]) if height else 0
    img = Image.new("L", (width, height))
    pixels = img.load()
    for y, row in enumerate(base_grid):
        for x, value in enumerate(row):
            pixels[x, y] = min(255, value)
    img = img.resize((width * scale, height * scale), Image.NEAREST).convert("RGB")
    draw = ImageDraw.Draw(img)

    for tx in range(0, width * scale + 1, tile_width * scale):
        draw.line([(tx, 0), (tx, height * scale)], fill=(255, 0, 0), width=1)
    for ty in range(0, height * scale + 1, tile_height * scale):
        draw.line([(0, ty), (width * scale, ty)], fill=(255, 0, 0), width=1)

    font = ImageFont.load_default()
    seen_tiles = set()
    for record in tile_records:
        tile_id = record["tile_id"]
        if tile_id in seen_tiles:
            continue
        seen_tiles.add(tile_id)
        # tile_id encodes (tile_row * 1024 + tile_col) -- see
        # tile_stats_hls.h's own frozen tile-ID formula, NOT a plain
        # row-major index over however many tile columns this frame has.
        tile_row, tile_col = divmod(tile_id, 1024)
        tile_x = tile_col * tile_width * scale
        tile_y = tile_row * tile_height * scale
        draw.text((tile_x + 2, tile_y + 2), f"mean={record['mean']}", fill=(255, 255, 0), font=font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
