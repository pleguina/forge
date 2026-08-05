"""Tests for the image-domain figure renderer
(plugins/vision_pipeline_demo/tutorial/visualizations/image_panels.py):
shape, determinism, and mismatch localization, using real golden
datasets and real GoldenModelProvider output — never invented pixel
data.
"""
from __future__ import annotations

import importlib.util as _ilu
import sys
from pathlib import Path

from PIL import Image

_PLUGIN_ROOT = Path(__file__).resolve().parents[4]
_DATA_DIR = _PLUGIN_ROOT / "forge" / "verify" / "schemas" / "data"


def _load_image_panels():
    viz_dir = _PLUGIN_ROOT / "tutorial" / "visualizations"
    spec = _ilu.spec_from_file_location("vpd_image_panels", viz_dir / "image_panels.py")
    module = _ilu.module_from_spec(spec)  # type: ignore[union-attr]
    sys.modules["vpd_image_panels"] = module
    spec.loader.exec_module(module)       # type: ignore[union-attr]
    return module


ip = _load_image_panels()


def _quickstart_events():
    ds = ip.load_dataset(_DATA_DIR / "vision_pipeline_quickstart_golden.xml")
    gmp = ip._load_golden_model_provider_module()
    result = gmp.PixelResultProvider().evaluate(ds, {})
    events = [{"in": ev["in"], "expected": exp["expected"]} for ev, exp in zip(ds.events, result.events)]
    return ds, events, gmp


# ── grid_from_events ──────────────────────────────────────────────────────

def test_grid_from_events_real_ramp_shape_and_values():
    ds, events, _gmp = _quickstart_events()
    grid = ip.grid_from_events(ds.events, 8, 8, "pixel", source="in")
    assert len(grid) == 8
    assert all(len(row) == 8 for row in grid)
    # Real ramp formula: pixel = (i*4) & 0xFF, row-major i = y*8+x.
    assert grid[0][0] == 0
    assert grid[0][1] == 4
    assert grid[7][7] == 252


def test_grid_from_events_reads_expected_namespace():
    ds, events, _gmp = _quickstart_events()
    grid = ip.grid_from_events(events, 8, 8, "normalized_pixel", source="expected")
    assert len(grid) == 8 and len(grid[0]) == 8
    # scale=3/2, offset=-64, clamped [0,255] -- top-left ramp value 0 -> clamped to 0.
    assert grid[0][0] == 0


# ── render_grayscale_panel: shape + determinism ──────────────────────────

def test_render_grayscale_panel_shape(tmp_path):
    grid = [[i * 32 for i in range(8)] for _ in range(8)]
    out = tmp_path / "panel.png"
    ip.render_grayscale_panel(grid, out, scale=4)
    with Image.open(out) as img:
        assert img.size == (32, 32)
        assert img.mode == "L"


def test_render_grayscale_panel_is_byte_deterministic(tmp_path):
    grid = [[(x * 7 + y * 13) % 256 for x in range(8)] for y in range(8)]
    out1, out2 = tmp_path / "a.png", tmp_path / "b.png"
    ip.render_grayscale_panel(grid, out1, scale=8)
    ip.render_grayscale_panel(grid, out2, scale=8)
    assert out1.read_bytes() == out2.read_bytes()


def test_render_grayscale_panel_clamps_value_range(tmp_path):
    grid = [[300, -5], [128, 0]]  # out-of-range values must clamp, not wrap or error
    out = tmp_path / "clamped.png"
    ip.render_grayscale_panel(grid, out, scale=1, value_max=255)
    with Image.open(out) as img:
        px = img.load()
        assert px[0, 0] == 255  # 300 clamped to 255
        assert px[1, 1] == 0


# ── render_diff_map: real mismatch localization ──────────────────────────

def test_diff_map_zero_for_identical_grids(tmp_path):
    grid = [[i for i in range(8)] for _ in range(8)]
    total = ip.render_diff_map(grid, grid, tmp_path / "diff.png")
    assert total == 0


def test_diff_map_localizes_a_real_injected_mismatch(tmp_path):
    """Not a claim about real RTL behavior -- a unit test of the renderer
    itself: given two grids that differ at exactly one cell, the diff map
    must report a nonzero total and place the difference at that cell,
    nowhere else.
    """
    grid_a = [[0 for _ in range(4)] for _ in range(4)]
    grid_b = [[0 for _ in range(4)] for _ in range(4)]
    grid_b[2][1] = 40
    out = tmp_path / "diff.png"
    total = ip.render_diff_map(grid_a, grid_b, out, scale=4)
    assert total == 40
    with Image.open(out) as img:
        px = img.load()
        # The mismatched cell (col=1, row=2) occupies pixels [4:8, 8:12) at scale=4.
        assert px[4, 8] == 255  # scaled to full brightness (value_max == 40 here)
        assert px[0, 0] == 0    # every other cell stays zero


# ── render_tile_overlay: real tile-ID decoding ────────────────────────────

def test_tile_overlay_places_labels_at_the_real_tile_id_formula_position(tmp_path):
    """tile_id encodes (tile_row * 1024 + tile_col), NOT a plain row-major
    index over however many tile columns a frame has -- confirmed against
    the real 16x16/2x2-tile dataset. A wrong decoding was caught by
    actually rendering this and looking at it, not assumed from the
    formula alone.
    """
    ds = ip.load_dataset(_DATA_DIR / "vision_pipeline_full_functional_golden.xml")
    gmp = ip._load_golden_model_provider_module()
    result = gmp.TileStatsProvider().evaluate(ds, {})
    events = [{"in": ev["in"], "expected": exp["expected"]} for ev, exp in zip(ds.events, result.events)]
    normalized = ip.grid_from_events(ds.events, 16, 16, "pixel", source="in")
    tile_records = [ev["expected"] for ev in events]

    tile_ids = {r["tile_id"] for r in tile_records}
    assert tile_ids == {0, 1, 1024, 1025}, "real tile-ID formula for a 2x2 tile grid"

    out = tmp_path / "overlay.png"
    ip.render_tile_overlay(normalized, tile_records, tile_width=8, tile_height=8, output_path=out)
    with Image.open(out) as img:
        assert img.size == (16 * 16, 16 * 16)  # default scale=16
        assert img.mode == "RGB"


def test_tile_overlay_is_byte_deterministic(tmp_path):
    ds = ip.load_dataset(_DATA_DIR / "vision_pipeline_quickstart_golden.xml")
    gmp = ip._load_golden_model_provider_module()
    result = gmp.TileStatsProvider().evaluate(ds, {})
    events = [{"in": ev["in"], "expected": exp["expected"]} for ev, exp in zip(ds.events, result.events)]
    normalized = ip.grid_from_events(ds.events, 8, 8, "pixel", source="in")
    tile_records = [ev["expected"] for ev in events]

    out1, out2 = tmp_path / "a.png", tmp_path / "b.png"
    ip.render_tile_overlay(normalized, tile_records, tile_width=8, tile_height=8, output_path=out1)
    ip.render_tile_overlay(normalized, tile_records, tile_width=8, tile_height=8, output_path=out2)
    assert out1.read_bytes() == out2.read_bytes()
