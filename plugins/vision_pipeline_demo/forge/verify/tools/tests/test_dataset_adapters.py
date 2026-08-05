"""Tests for the project-owned dataset adapters (see
docs/development/adr/0001-dataset-ownership-boundary.md):
``SyntheticPatternAdapter``, ``ImageFolderAdapter``, ``NumpyArrayAdapter``,
their FORGE-registered ``ProjectDatasetAdapter`` wrappers, and the
manifest/staleness machinery.

Required test coverage, mapped to test functions below:
  * same source/config produces byte-identical canonical events
    -> test_synthetic_determinism, test_numpy_layout_determinism
  * filesystem ordering does not change event order
    -> test_image_folder_stable_lexical_order
  * source relocation does not change semantic hashes
    -> test_image_folder_relocation_does_not_change_canonical_hash
  * one changed source pixel changes the canonical-event hash
    -> test_image_folder_changed_pixel_changes_hash
  * one preprocessing change changes the preprocessing/dataset hashes
    -> test_preprocessing_change_changes_hash
  * invalid shape/range fails cleanly -> test_numpy_ambiguous_shape_rejected
  * corrupt image fails with source filename -> test_image_folder_corrupt_file_diagnostic
  * XML round-trip preserves event semantics -> test_xml_round_trip_matches_flatten
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from datasets.adapters.image_folder import ImageFolderAdapter
from datasets.adapters.numpy_array import NumpyArrayAdapter
from datasets.adapters.synthetic import SyntheticPatternAdapter
from datasets.manifest import build_manifest, check_staleness, compute_canonical_events_hash, load_manifest, write_manifest
from datasets.model import tile_id_for
from datasets.serialize_xml import flatten_events, write_xml

from forge.verify.dataset_format import XmlDatasetLoader


# ── SyntheticPatternAdapter ──────────────────────────────────────────────

def test_synthetic_determinism():
    a = list(SyntheticPatternAdapter(width=8, height=8, patterns=["ramp", "noise"], seed=7, event_count=2).iter_events())
    b = list(SyntheticPatternAdapter(width=8, height=8, patterns=["ramp", "noise"], seed=7, event_count=2).iter_events())
    assert compute_canonical_events_hash(a) == compute_canonical_events_hash(b)


def test_synthetic_noise_seed_changes_output_but_deterministic_patterns_do_not():
    ramp_a = list(SyntheticPatternAdapter(width=4, height=4, patterns=["ramp"], seed=1, event_count=1).iter_events())
    ramp_b = list(SyntheticPatternAdapter(width=4, height=4, patterns=["ramp"], seed=2, event_count=1).iter_events())
    assert compute_canonical_events_hash(ramp_a) == compute_canonical_events_hash(ramp_b)

    noise_a = list(SyntheticPatternAdapter(width=4, height=4, patterns=["noise"], seed=1, event_count=1).iter_events())
    noise_b = list(SyntheticPatternAdapter(width=4, height=4, patterns=["noise"], seed=2, event_count=1).iter_events())
    assert compute_canonical_events_hash(noise_a) != compute_canonical_events_hash(noise_b)


def test_synthetic_unknown_pattern_rejected():
    with pytest.raises(ValueError, match="Unknown synthetic pattern"):
        SyntheticPatternAdapter(width=4, height=4, patterns=["not-a-real-pattern"], event_count=1)


def test_synthetic_row_major_traversal_and_tile_id():
    # 16x16 exercises 2x2 tiles -- both tile_id row/col terms vary, the
    # same reasoning behind the full-functional design's own dataset size.
    events = list(SyntheticPatternAdapter(width=16, height=16, patterns=["constant"], event_count=1).iter_events())
    pixels = events[0].pixels
    assert len(pixels) == 256
    # row-major: pixel index i has x = i % 16, y = i // 16
    for i, px in enumerate(pixels):
        assert px.x == i % 16
        assert px.y == i // 16
        assert px.end_of_line == (px.x == 15)
        assert px.end_of_frame == (px.x == 15 and px.y == 15)
        assert px.tile_id == tile_id_for(px.x, px.y)
    tile_ids = {px.tile_id for px in pixels}
    assert tile_ids == {0, 1, 1024, 1025}  # 2x2 tiles, per the frozen formula


# ── ImageFolderAdapter ────────────────────────────────────────────────────

def _write_png(path: Path, value: int, size: int = 4) -> None:
    Image.fromarray(np.full((size, size, 3), value, dtype=np.uint8), "RGB").save(path)


def test_image_folder_stable_lexical_order(tmp_path):
    _write_png(tmp_path / "z_last.png", 10)
    _write_png(tmp_path / "a_first.png", 20)
    _write_png(tmp_path / "m_middle.png", 30)

    events = list(ImageFolderAdapter(source=tmp_path, width=4, height=4).iter_events())
    names = [Path(e.source_metadata["source_file"]).name for e in events]
    assert names == ["a_first.png", "m_middle.png", "z_last.png"]


def test_image_folder_grayscale_formula(tmp_path):
    Image.fromarray(np.array([[[255, 0, 0]] * 2] * 2, dtype=np.uint8), "RGB").save(tmp_path / "red.png")
    events = list(ImageFolderAdapter(source=tmp_path, width=2, height=2).iter_events())
    expected = (77 * 255) >> 8
    assert all(px.pixel == expected for px in events[0].pixels)


def test_image_folder_nearest_resize_upsample(tmp_path):
    arr = np.zeros((2, 2, 3), dtype=np.uint8)
    arr[0, 0] = (0, 0, 0)
    arr[0, 1] = (255, 255, 255)
    arr[1, 0] = (100, 100, 100)
    arr[1, 1] = (200, 200, 200)
    Image.fromarray(arr, "RGB").save(tmp_path / "grid.png")

    events = list(ImageFolderAdapter(source=tmp_path, width=4, height=4).iter_events())
    grid = {(px.x, px.y): px.pixel for px in events[0].pixels}
    assert grid[(0, 0)] == grid[(1, 0)] == grid[(0, 1)] == grid[(1, 1)] == 0
    assert grid[(2, 0)] == grid[(3, 0)] == grid[(2, 1)] == grid[(3, 1)] == 255


def test_image_folder_changed_pixel_changes_hash(tmp_path):
    _write_png(tmp_path / "a.png", 100)
    before = list(ImageFolderAdapter(source=tmp_path, width=4, height=4).iter_events())
    h_before = compute_canonical_events_hash(before)

    _write_png(tmp_path / "a.png", 101)  # one-value change, same file
    after = list(ImageFolderAdapter(source=tmp_path, width=4, height=4).iter_events())
    h_after = compute_canonical_events_hash(after)

    assert h_before != h_after


def test_image_folder_relocation_does_not_change_canonical_hash(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _write_png(src / "a.png", 77)
    events_src = list(ImageFolderAdapter(source=src, width=4, height=4).iter_events())

    dst = tmp_path / "relocated" / "deeper"
    dst.mkdir(parents=True)
    _write_png(dst / "a.png", 77)
    events_dst = list(ImageFolderAdapter(source=dst, width=4, height=4).iter_events())

    assert compute_canonical_events_hash(events_src) == compute_canonical_events_hash(events_dst)
    # the per-image hash itself is also identical, since it's a content hash
    assert events_src[0].source_metadata["source_image_hash"] == events_dst[0].source_metadata["source_image_hash"]


def test_image_folder_corrupt_file_diagnostic(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"this is not a real png file")
    with pytest.raises(ValueError, match=r"bad\.png"):
        list(ImageFolderAdapter(source=tmp_path, width=4, height=4).iter_events())


def test_image_folder_empty_directory_rejected(tmp_path):
    with pytest.raises(ValueError, match="no matching image files"):
        ImageFolderAdapter(source=tmp_path, width=4, height=4)


def test_image_folder_max_events_truncates(tmp_path):
    for name, value in [("a.png", 1), ("b.png", 2), ("c.png", 3)]:
        _write_png(tmp_path / name, value)
    events = list(ImageFolderAdapter(source=tmp_path, width=4, height=4, max_events=2).iter_events())
    assert len(events) == 2


# ── NumpyArrayAdapter ─────────────────────────────────────────────────────

def test_numpy_layout_determinism(tmp_path):
    arr = np.arange(2 * 4 * 4, dtype=np.uint8).reshape(2, 4, 4)
    np.save(tmp_path / "frames.npy", arr)
    a = list(NumpyArrayAdapter(source=tmp_path / "frames.npy", layout="NHW").iter_events())
    b = list(NumpyArrayAdapter(source=tmp_path / "frames.npy", layout="NHW").iter_events())
    assert compute_canonical_events_hash(a) == compute_canonical_events_hash(b)
    assert [px.pixel for px in a[0].pixels] == arr[0].flatten().tolist()


def test_numpy_ambiguous_shape_rejected(tmp_path):
    arr = np.zeros((2, 4, 4), dtype=np.uint8)
    np.save(tmp_path / "frames.npy", arr)
    with pytest.raises(ValueError, match="requires a 2-D array"):
        NumpyArrayAdapter(source=tmp_path / "frames.npy", layout="HW")


def test_numpy_nhwc_bad_channel_count_rejected(tmp_path):
    arr = np.zeros((2, 4, 4, 5), dtype=np.uint8)  # 5 channels: unsupported
    np.save(tmp_path / "frames.npy", arr)
    with pytest.raises(ValueError, match="1 or 3 channels"):
        NumpyArrayAdapter(source=tmp_path / "frames.npy", layout="NHWC")


def test_numpy_npz_requires_array_key_when_ambiguous(tmp_path):
    np.savez(tmp_path / "multi.npz", one=np.zeros((4, 4), dtype=np.uint8), two=np.zeros((4, 4), dtype=np.uint8))
    with pytest.raises(ValueError, match="array_key is required"):
        NumpyArrayAdapter(source=tmp_path / "multi.npz", layout="HW")


def test_numpy_value_range_rescale(tmp_path):
    arr = np.array([[0.0, 0.5], [1.0, 0.25]], dtype=np.float32)
    np.save(tmp_path / "frame.npy", arr)
    events = list(NumpyArrayAdapter(source=tmp_path / "frame.npy", layout="HW", value_range=(0.0, 1.0)).iter_events())
    pixels = {(px.x, px.y): px.pixel for px in events[0].pixels}
    assert pixels[(0, 0)] == 0
    assert pixels[(1, 0)] == 128  # round(0.5*255) == 128 (nearest)
    assert pixels[(0, 1)] == 255
    assert pixels[(1, 1)] == 64   # round(0.25*255) == 64 (nearest)


def test_numpy_labels_recorded(tmp_path):
    np.savez(tmp_path / "labeled.npz", images=np.zeros((2, 2, 2), dtype=np.uint8), labels=np.array([3, 5]))
    events = list(
        NumpyArrayAdapter(source=tmp_path / "labeled.npz", layout="NHW", array_key="images", label_key="labels").iter_events()
    )
    assert events[0].labels == {"label": 3}
    assert events[1].labels == {"label": 5}


# ── serialize_xml round-trip ──────────────────────────────────────────────

def test_xml_round_trip_matches_flatten(tmp_path):
    events = list(SyntheticPatternAdapter(width=4, height=4, patterns=["checkerboard"], event_count=1).iter_events())
    xml_path = write_xml(events, tmp_path / "out.xml")
    loaded = XmlDatasetLoader().load(xml_path)
    assert flatten_events(events) == loaded.events


# ── manifest + preprocessing-change hash sensitivity ─────────────────────

def test_preprocessing_change_changes_hash():
    events = list(SyntheticPatternAdapter(width=4, height=4, patterns=["ramp"], event_count=1).iter_events())
    m1 = build_manifest(
        dataset_id="d", adapter_id="synthetic", adapter_version="1.0", events=events,
        preprocessing_config={"width": 4, "height": 4},
    )
    m2 = build_manifest(
        dataset_id="d", adapter_id="synthetic", adapter_version="1.0", events=events,
        preprocessing_config={"width": 8, "height": 4},  # one changed field
    )
    assert m1.preprocessing_config_hash != m2.preprocessing_config_hash
    # canonical event content is unchanged (same events passed in)
    assert m1.canonical_events_hash == m2.canonical_events_hash


def test_manifest_round_trip(tmp_path):
    events = list(SyntheticPatternAdapter(width=4, height=4, patterns=["ramp"], event_count=1).iter_events())
    manifest = build_manifest(
        dataset_id="rt", adapter_id="synthetic", adapter_version="1.0", events=events,
        preprocessing_config={"width": 4, "height": 4}, adapter_config={"width": 4, "height": 4},
    )
    path = write_manifest(manifest, tmp_path / "rt.manifest.yml")
    loaded = load_manifest(path)
    assert loaded.dataset_id == manifest.dataset_id
    assert loaded.canonical_events_hash == manifest.canonical_events_hash
    assert loaded.adapter_config == manifest.adapter_config


# ── staleness ─────────────────────────────────────────────────────────────

def test_staleness_detects_content_change_not_mtime(tmp_path):
    src_root = tmp_path / "src"
    src_root.mkdir()
    f = src_root / "a.bin"
    f.write_bytes(b"original content")

    events = list(SyntheticPatternAdapter(width=2, height=2, patterns=["constant"], event_count=1).iter_events())
    manifest = build_manifest(
        dataset_id="s", adapter_id="x", adapter_version="1.0", events=events,
        preprocessing_config={}, source_paths=[f], source_root=src_root,
    )
    assert check_staleness(manifest, source_root=src_root) == []

    # touching mtime only (no content change) must not report staleness
    import os
    import time
    time.sleep(0.01)
    os.utime(f, None)
    assert check_staleness(manifest, source_root=src_root) == []

    # real content change must be detected
    f.write_bytes(b"changed content!!")
    reasons = check_staleness(manifest, source_root=src_root)
    assert len(reasons) == 1
    assert "a.bin" in reasons[0]

    # restoring the original bytes clears staleness
    f.write_bytes(b"original content")
    assert check_staleness(manifest, source_root=src_root) == []


def test_staleness_detects_missing_file(tmp_path):
    src_root = tmp_path / "src"
    src_root.mkdir()
    f = src_root / "a.bin"
    f.write_bytes(b"data")
    events = list(SyntheticPatternAdapter(width=2, height=2, patterns=["constant"], event_count=1).iter_events())
    manifest = build_manifest(
        dataset_id="s", adapter_id="x", adapter_version="1.0", events=events,
        preprocessing_config={}, source_paths=[f], source_root=src_root,
    )
    f.unlink()
    reasons = check_staleness(manifest, source_root=src_root)
    assert any("missing source file" in r for r in reasons)


# ── FORGE-registered ProjectDatasetAdapter wrappers (real DatasetService path) ──

def test_forge_synthetic_adapter_via_dataset_service():
    from forge.verify.dataset_adapter import DatasetSource
    from forge.verify.dataset_service import DatasetService

    service = DatasetService()
    canonical = service.materialize(
        DatasetSource(), "vision_pipeline.synthetic",
        {"width": 4, "height": 4, "patterns": ["checkerboard"], "seed": 0, "event_count": 1},
    )
    assert len(canonical.events) == 16
    assert canonical.metadata.semantic.adapter_id == "vision_pipeline.synthetic"
    assert canonical.metadata.semantic.preprocessing_hash is not None


def test_forge_image_folder_adapter_via_dataset_service(tmp_path):
    from forge.verify.dataset_adapter import DatasetSource
    from forge.verify.dataset_service import DatasetService

    _write_png(tmp_path / "a.png", 42)
    service = DatasetService()
    canonical = service.materialize(
        DatasetSource(raw_path=tmp_path), "vision_pipeline.image-folder", {"width": 4, "height": 4},
    )
    assert len(canonical.events) == 16
    assert canonical.metadata.semantic.adapter_id == "vision_pipeline.image-folder"


def test_forge_numpy_array_adapter_via_dataset_service(tmp_path):
    from forge.verify.dataset_adapter import DatasetSource
    from forge.verify.dataset_service import DatasetService

    arr = np.zeros((1, 4, 4), dtype=np.uint8)
    np.save(tmp_path / "frames.npy", arr)
    service = DatasetService()
    canonical = service.materialize(
        DatasetSource(raw_path=tmp_path / "frames.npy"), "vision_pipeline.numpy-array", {"layout": "NHW"},
    )
    assert len(canonical.events) == 16
    assert canonical.metadata.semantic.adapter_id == "vision_pipeline.numpy-array"


def test_forge_image_folder_adapter_requires_raw_path():
    from forge.verify.dataset_adapter import DatasetSource
    from forge.verify.dataset_service import DatasetService

    service = DatasetService()
    with pytest.raises(ValueError, match="requires DatasetSource.raw_path"):
        service.materialize(DatasetSource(), "vision_pipeline.image-folder", {"width": 4, "height": 4})
