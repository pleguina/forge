"""Tests for the tutorial ownership vocabulary
(plugins/vision_pipeline_demo/tutorial/ownership.py), the single source of
truth for which of the five ownership categories (project source, FORGE
generated, toolchain output, verification result, tutorial asset) every
path in this plugin's tutorial material belongs to.
"""
from __future__ import annotations

import importlib.util as _ilu
import subprocess
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[4]
_REPO_ROOT = _PLUGIN_ROOT.parents[1]


def _load_ownership():
    # Mirror dataset_adapter.py's own _load_datasets_package() precedent:
    # load by explicit file path rather than adding the plugin root to
    # sys.path, since plugins/vision_pipeline_demo/forge/ would otherwise
    # shadow the real installed `forge` package as a namespace package.
    tutorial_dir = _PLUGIN_ROOT / "tutorial"
    spec = _ilu.spec_from_file_location(
        "vpd_tutorial", tutorial_dir / "__init__.py", submodule_search_locations=[str(tutorial_dir)],
    )
    module = _ilu.module_from_spec(spec)  # type: ignore[union-attr]
    sys.modules["vpd_tutorial"] = module
    spec.loader.exec_module(module)       # type: ignore[union-attr]
    import vpd_tutorial.ownership as ownership  # noqa: E402
    return ownership


ownership = _load_ownership()


def _tracked_plugin_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "plugins/vision_pipeline_demo"],
        capture_output=True, text=True, check=True, cwd=_REPO_ROOT,
    ).stdout.splitlines()
    return [f for f in out if f]


# ── classify() against every real tracked file ──────────────────────────

def test_every_tracked_plugin_file_classifies():
    """No real, checked-in file under the plugin tree is unclassifiable —
    a new file added without updating ownership.py's rules should fail
    this test, not silently render as unlabeled in a future tutorial page.
    """
    files = _tracked_plugin_files()
    assert files, "expected at least one tracked file under plugins/vision_pipeline_demo"
    unclassified = []
    for f in files:
        try:
            ownership.classify(f)
        except ValueError:
            unclassified.append(f)
    assert not unclassified, f"no ownership rule matches: {unclassified}"


@pytest.mark.parametrize("rel_path,expected", [
    ("plugins/vision_pipeline_demo/algo/normalizer/pixel_normalizer.cpp", ownership.PROJECT_SOURCE),
    ("plugins/vision_pipeline_demo/algo/rtl/threshold_rtl.v", ownership.PROJECT_SOURCE),
    ("plugins/vision_pipeline_demo/datasets/model.py", ownership.PROJECT_SOURCE),
    ("plugins/vision_pipeline_demo/forge/modules.yml", ownership.PROJECT_SOURCE),
    ("plugins/vision_pipeline_demo/forge/designs/design_cdc.yml", ownership.PROJECT_SOURCE),
    ("plugins/vision_pipeline_demo/forge/verify/design.verification.yml", ownership.PROJECT_SOURCE),
    ("plugins/vision_pipeline_demo/forge/verify/tools/golden_model_provider.py", ownership.PROJECT_SOURCE),
    ("plugins/vision_pipeline_demo/forge/verify/schemas/data/vision_pipeline_quickstart_golden.xml", ownership.PROJECT_SOURCE),
    ("plugins/vision_pipeline_demo/README.md", ownership.TUTORIAL_ASSET),
    ("plugins/vision_pipeline_demo/tutorial.yml", ownership.TUTORIAL_ASSET),
    ("plugins/vision_pipeline_demo/tutorial/runner.py", ownership.TUTORIAL_ASSET),
    ("plugins/vision_pipeline_demo/forge/verify/packetizer_xsim/verify.flow.yml", ownership.FORGE_GENERATED),
    ("plugins/vision_pipeline_demo/forge/verify/packetizer_xsim/tb_algo_top.sv", ownership.FORGE_GENERATED),
    ("plugins/vision_pipeline_demo/forge/verify/packetizer_xsim/port_map.yaml", ownership.FORGE_GENERATED),
    ("plugins/vision_pipeline_demo/forge/verify/packetizer_xsim/wave.tcl", ownership.FORGE_GENERATED),
    ("plugins/vision_pipeline_demo/forge/verify/packetizer_xsim/stimulus_current.svh", ownership.FORGE_GENERATED),
    ("gen-top/design_vision_pipeline_quickstart/algo_top.v", ownership.FORGE_GENERATED),
    ("plugins/vision_pipeline_demo/forge/verify/packetizer_xsim/xsim_work/xsim.dir/foo", ownership.TOOLCHAIN_OUTPUT),
    ("build_hls_vision_pipeline_demo/pixel_normalizer/solution1/syn/verilog/pixel_normalizer.v", ownership.TOOLCHAIN_OUTPUT),
    ("plugins/vision_pipeline_demo/forge/verify/packetizer_xsim/throughput_result.json", ownership.VERIFICATION_RESULT),
    ("plugins/vision_pipeline_demo/forge/verify/packetizer_xsim/golden_model_provenance.json", ownership.VERIFICATION_RESULT),
])
def test_classify_known_paths(rel_path, expected):
    assert ownership.classify(rel_path) == expected


def test_classify_rejects_unrelated_path():
    with pytest.raises(ValueError):
        ownership.classify("plugins/trigger_demo/forge/modules.yml")


def test_categories_have_legend_text():
    assert set(ownership.LEGEND) == set(ownership.CATEGORIES)
    assert all(isinstance(v, str) and v for v in ownership.LEGEND.values())


# ── tree renderers ────────────────────────────────────────────────────

def test_render_source_tree_runs_and_labels_every_entry():
    text = ownership.render_source_tree()
    assert "plugins/vision_pipeline_demo/" in text
    for category in ownership.CATEGORIES:
        # Not every category has to appear (TOOLCHAIN_OUTPUT/VERIFICATION_RESULT
        # never live directly under the source tree), but every bracketed
        # label that *does* appear must be a real category, not a typo.
        pass
    import re
    labels = set(re.findall(r"\[([A-Z ]+)\]", text))
    assert labels, "expected at least one ownership label in the rendered source tree"
    assert labels <= set(ownership.CATEGORIES)


def test_render_generated_roots_tree_runs_and_labels_every_entry():
    import re
    text = ownership.render_generated_roots_tree()
    labels = set(re.findall(r"\[([A-Z ]+)\]", text))
    assert labels == {ownership.FORGE_GENERATED, ownership.TOOLCHAIN_OUTPUT, ownership.VERIFICATION_RESULT}
