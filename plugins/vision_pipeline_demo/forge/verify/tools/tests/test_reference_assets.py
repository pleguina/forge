"""Tests for plugins/vision_pipeline_demo/tutorial/generate_reference_assets.py
— the script that produces docs/assets/generated/vision-pipeline/**.

These tests invoke real tools (`forge inspect`, Graphviz `dot`, `forge
analyze plot-results`) since the whole point of this script is gluing
real renderers together — skipped gracefully when those aren't on PATH,
consistent with this repo's existing HLS/Vivado-dependent test pattern.
"""
from __future__ import annotations

import importlib.util as _ilu
import shutil
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[4]

pytestmark = pytest.mark.skipif(
    shutil.which("forge") is None or shutil.which("dot") is None,
    reason="requires forge CLI and Graphviz dot on PATH",
)


def _load_generator():
    tutorial_dir = _PLUGIN_ROOT / "tutorial"
    spec = _ilu.spec_from_file_location(
        "vpd_ref_assets_gen", tutorial_dir / "generate_reference_assets.py",
    )
    module = _ilu.module_from_spec(spec)  # type: ignore[union-attr]
    sys.modules["vpd_ref_assets_gen"] = module
    spec.loader.exec_module(module)       # type: ignore[union-attr]
    return module


gen = _load_generator()


def test_generate_all_produces_every_diagram_and_figure(tmp_path):
    manifest = gen.generate_all(tmp_path)

    expected_diagrams = {name for name, _ in gen.DIAGRAM_DESIGNS}
    assert set(manifest["diagrams"]) == expected_diagrams
    for name in expected_diagrams:
        assert (tmp_path / "diagrams" / f"{name}.svg").is_file()
        assert manifest["diagrams"][name]["design_content_hash"]

    expected_figures = {
        "quickstart-input", "quickstart-normalized", "quickstart-mask",
        "pixel-result-input", "pixel-result-normalized", "pixel-result-gradient", "pixel-result-mask",
        "tile-stats-overlay", "full-functional-tile-overlay",
        "fifo-high-water-mark",
    }
    assert set(manifest["figures"]) == expected_figures
    for name in expected_figures:
        assert (tmp_path / "figures" / f"{name}.png").is_file()


def test_generate_all_is_byte_deterministic_across_two_runs(tmp_path):
    out1, out2 = tmp_path / "run1", tmp_path / "run2"
    manifest1 = gen.generate_all(out1)
    manifest2 = gen.generate_all(out2)
    assert manifest1 == manifest2

    for rel in sorted((out1 / "diagrams").glob("*")) + sorted((out1 / "figures").glob("*")):
        rel_path = rel.relative_to(out1)
        assert (out2 / rel_path).read_bytes() == rel.read_bytes(), f"non-deterministic: {rel_path}"


def test_check_passes_against_the_currently_committed_assets():
    """The real, committed docs/assets/generated/vision-pipeline/ tree
    must already be fresh -- this is the same invariant
    ci/vision_pipeline_demo_reference_check.sh-style checks enforce for
    comments, applied here to generated figures instead.
    """
    assert gen.main(["--check"]) == 0
