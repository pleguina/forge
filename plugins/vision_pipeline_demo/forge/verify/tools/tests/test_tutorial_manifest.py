"""Tests for the tutorial manifest (plugins/vision_pipeline_demo/tutorial.yml)
and the progressive runner that drives it
(plugins/vision_pipeline_demo/tutorial/runner.py).

These tests are pure-Python/no-toolchain: they validate the manifest's
own internal consistency and the runner's plan-construction logic without
invoking `forge`, Vitis HLS, or Vivado. Real end-to-end execution
(``--step <id>``, ``--all``) is exercised manually against real
toolchains — see the tutorial-productization audit's own T2 completion
notes for that evidence, not a unit test here.
"""
from __future__ import annotations

import importlib.util as _ilu
import sys
from pathlib import Path

import pytest
import yaml

_PLUGIN_ROOT = Path(__file__).resolve().parents[4]
_REPO_ROOT = _PLUGIN_ROOT.parents[1]


def _load_runner():
    tutorial_dir = _PLUGIN_ROOT / "tutorial"
    spec = _ilu.spec_from_file_location(
        "vpd_tutorial2", tutorial_dir / "__init__.py", submodule_search_locations=[str(tutorial_dir)],
    )
    module = _ilu.module_from_spec(spec)  # type: ignore[union-attr]
    sys.modules["vpd_tutorial2"] = module
    spec.loader.exec_module(module)       # type: ignore[union-attr]
    import vpd_tutorial2.runner as runner  # noqa: E402
    return runner


runner = _load_runner()


@pytest.fixture(scope="module")
def manifest():
    return runner.load_manifest()


@pytest.fixture(scope="module")
def modules_registry():
    with open(_PLUGIN_ROOT / "forge" / "modules.yml") as f:
        return yaml.safe_load(f)


# ── Manifest structural integrity ────────────────────────────────────────

def test_schema_version(manifest):
    assert manifest["schema_version"] == 1


def test_every_step_has_unique_id(manifest):
    ids = [s["id"] for s in manifest["steps"]]
    assert len(ids) == len(set(ids)), f"duplicate step ids: {ids}"


def test_every_step_design_file_exists(manifest):
    for step in manifest["steps"]:
        design_path = _PLUGIN_ROOT / "forge" / "designs" / step["design"]
        assert design_path.is_file(), f"step {step['id']!r}: design file not found: {design_path}"


def test_every_step_flow_dir_exists(manifest):
    for step in manifest["steps"]:
        for flow in step.get("flows", []):
            flow_dir = _PLUGIN_ROOT / "forge" / "verify" / flow["name"]
            assert flow_dir.is_dir(), f"step {step['id']!r}: flow dir not found: {flow_dir}"
            assert (flow_dir / "verify.flow.yml").is_file()


def test_every_capability_is_in_the_vocabulary(manifest):
    vocab = set(manifest["capability_vocabulary"])
    for step in manifest["steps"]:
        unknown = [c for c in step["capabilities"] if c not in vocab]
        assert not unknown, f"step {step['id']!r} uses unknown capabilities: {unknown}"


def test_every_hls_module_is_a_real_hls_registry_entry(manifest, modules_registry):
    hls_names = {m["name"] for m in modules_registry["modules"] if m["kind"] == "hls"}
    for step in manifest["steps"]:
        unknown = [m for m in step.get("hls_modules", []) if m not in hls_names]
        assert not unknown, f"step {step['id']!r} declares non-HLS/unknown modules: {unknown}"


def test_every_step_design_only_references_declared_hls_modules(manifest, modules_registry):
    """The manifest's own `hls_modules` list for a step must be exactly the
    HLS-kind modules that design actually instantiates — neither more
    (wasted synth) nor fewer (gen-top would fail scanning for missing
    port metadata).
    """
    hls_names_by_ref = {m["name"]: m["kind"] for m in modules_registry["modules"]}
    for step in manifest["steps"]:
        design_path = _PLUGIN_ROOT / "forge" / "designs" / step["design"]
        with open(design_path) as f:
            design = yaml.safe_load(f)
        refs = {m["ref"] for m in design.get("modules", [])}
        real_hls_refs = {r for r in refs if hls_names_by_ref.get(r) == "hls"}
        assert real_hls_refs == set(step.get("hls_modules", [])), (
            f"step {step['id']!r}: manifest hls_modules {step.get('hls_modules')} "
            f"!= design's real HLS refs {sorted(real_hls_refs)}"
        )


def test_negative_fixture_steps_have_no_positive_flow_and_vice_versa(manifest):
    for step in manifest["steps"]:
        for flow in step.get("flows", []):
            if flow.get("expect_fail"):
                assert step.get("negative_fixture") is True, (
                    f"step {step['id']!r} has an expect_fail flow but isn't marked negative_fixture"
                )


def test_topgen_reject_steps_have_no_flows(manifest):
    for step in manifest["steps"]:
        if step["kind"] == "topgen_reject":
            assert step.get("flows") == []


# ── build_plan() ──────────────────────────────────────────────────────────

def test_build_plan_default_selects_every_step_in_manifest_order(manifest):
    plan = runner.build_plan(manifest, None)
    assert [s["id"] for s in plan.steps] == [s["id"] for s in manifest["steps"]]
    assert plan.full_selection is True


def test_build_plan_all_matches_no_selection(manifest):
    plan_default = runner.build_plan(manifest, None)
    plan_empty_list = runner.build_plan(manifest, [])
    assert [s["id"] for s in plan_default.steps] == [s["id"] for s in plan_empty_list.steps]


def test_build_plan_full_selection_hls_union_matches_original_script(manifest):
    """The original run_vision_pipeline_demo.sh always synthesized exactly
    these three modules for a full run -- the manifest-driven union for
    every step selected must still match exactly.
    """
    plan = runner.build_plan(manifest, None)
    assert set(plan.hls_modules) == {"pixel_normalizer", "sobel_hls", "tile_stats_hls"}
    assert plan.needs_csim is True


def test_build_plan_single_step_scopes_down(manifest):
    plan = runner.build_plan(manifest, ["cdc"])
    assert [s["id"] for s in plan.steps] == ["cdc"]
    assert plan.hls_modules == []
    assert plan.needs_csim is False
    assert plan.full_selection is False


def test_build_plan_quickstart_needs_csim_and_pixel_normalizer_only(manifest):
    plan = runner.build_plan(manifest, ["quickstart"])
    assert plan.hls_modules == ["pixel_normalizer"]
    assert plan.needs_csim is True


def test_build_plan_unknown_step_raises(manifest):
    with pytest.raises(KeyError):
        runner.build_plan(manifest, ["not_a_real_step"])


def test_build_plan_multi_step_union_is_order_stable_and_deduplicated(manifest):
    plan = runner.build_plan(manifest, ["packetizer", "full_functional", "cdc"])
    assert [s["id"] for s in plan.steps] == ["packetizer", "full_functional", "cdc"]
    assert plan.hls_modules == ["pixel_normalizer", "sobel_hls", "tile_stats_hls"]


# ── missing_tools() ───────────────────────────────────────────────────────

def test_missing_tools_reports_nothing_when_tools_are_absent_from_requirements(manifest, monkeypatch):
    plan = runner.build_plan(manifest, ["invalid_direct_bus_cdc"])
    monkeypatch.setattr(runner.shutil, "which", lambda _binary: None)
    assert runner.missing_tools(plan) == []  # this step only requires python


def test_missing_tools_flags_absent_vitis_hls(manifest, monkeypatch):
    plan = runner.build_plan(manifest, ["quickstart"])
    monkeypatch.setattr(runner.shutil, "which", lambda _binary: None)
    missing = dict(runner.missing_tools(plan))
    assert "vitis_hls" in missing
    assert "vivado_xsim" in missing


def test_missing_tools_reports_nothing_when_everything_is_on_path(manifest, monkeypatch):
    plan = runner.build_plan(manifest, ["quickstart"])
    monkeypatch.setattr(runner.shutil, "which", lambda _binary: "/usr/bin/" + _binary)
    assert runner.missing_tools(plan) == []
