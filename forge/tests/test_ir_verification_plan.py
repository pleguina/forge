"""
Tests for forge.ir.verification_plan — Phase G3, verification planning on
the canonical IR.

Driven by a real `gen-top` run of plugins/passthrough_demo (no EDA tools
needed) plus its real `design.verification.yml`, so the plan is checked
against the ports FORGE actually generated rather than a hand-built IR that
could agree with the code and disagree with reality.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge.core.cli.main import build_parser
from forge.ir.deserialize import from_json_dict
from forge.ir.model import (
    ResolvedDesign,
    ResolvedProject,
    ResolvedVerificationBinding,
    ResolvedVerificationPlan,
)
from forge.ir.verification_plan import build_verification_plan, port_divergences
from forge.verification.design_contract import load_verify_design

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN = REPO_ROOT / "plugins/passthrough_demo"
DESIGN_YML = PLUGIN / "forge/designs/design.yml"
MODULES_YML = PLUGIN / "forge/modules.yml"
VERIFY_YML = PLUGIN / "forge/verify/design.verification.yml"
# The DUT directory this plugin's flows declare, relative to the consumer
# root — see its README's documented gen-top invocation.
DUT_REL = "gen-top/design_passthrough_demo"


@pytest.fixture(scope="module")
def generated(tmp_path_factory) -> Path:
    """One real gen-top run laid out the way the plugin documents it: the
    consumer root is the working root, and the DUT lands in the directory
    the verification contract's `dut_rtl_source` names."""
    root = tmp_path_factory.mktemp("consumer")
    parser = build_parser()
    args = parser.parse_args([
        "topgen", "gen-top", str(DESIGN_YML),
        "--mode", "verilog",
        "--consumer-root", str(root),
        "--contracts-from", str(MODULES_YML),
        "--build-dir", str(root / "build"),
        "--output", str(root / DUT_REL / "algo_top.v"),
    ])
    try:
        args.func(args)
    except SystemExit as exc:
        assert not exc.code, f"gen-top failed: {exc.code}"
    return root


@pytest.fixture(scope="module")
def project(generated):
    payload = json.loads((generated / DUT_REL / "design.ir.json").read_text())
    proj, _notes = from_json_dict(payload)
    return proj


def test_generated_ir_carries_a_populated_plan(project):
    plan = project.design.verification_plan
    assert plan.populated
    assert "Resolved from the canonical IR" in plan.note


def test_clock_and_reset_are_separated_from_stimulus(project):
    plan = project.design.verification_plan
    assert plan.clocks == ["ap_clk"]
    assert plan.resets == ["ap_rst"]
    assert "ap_clk" not in [b.top_port for b in plan.stimulus]


def test_every_stimulus_point_names_the_pin_behind_it(project):
    """The join generation knew and nothing downstream could reconstruct: a
    top-level port name back to the instance pin it drives."""
    plan = project.design.verification_plan
    assert plan.stimulus
    for binding in (*plan.stimulus, *plan.observation):
        assert binding.origin == "external"
        assert binding.instance_id, binding.top_port
        assert binding.instance_port, binding.top_port
        # The generator's naming rule, checked rather than assumed.
        assert binding.top_port == f"{binding.instance_id}_{binding.instance_port}"


def test_stimulus_and_observation_split_by_direction(project):
    plan = project.design.verification_plan
    assert {b.direction for b in plan.stimulus} == {"in"}
    assert {b.direction for b in plan.observation} == {"out"}


def test_flows_targeting_this_designs_generated_top_are_identified(project):
    plan = project.design.verification_plan
    contract = load_verify_design(VERIFY_YML)

    assert len(plan.flows) == len(contract.flows)
    assert all(fl.targets_this_design for fl in plan.flows)
    assert all(fl.dut_kind == "generated_top" for fl in plan.flows)
    assert not any(fl.unresolved_reason for fl in plan.flows)


def test_a_flow_for_another_designs_dut_is_recorded_but_not_claimed(project, tmp_path):
    """A verification contract covers a whole plugin, which may hold several
    designs. A flow whose DUT directory is a different design's is not this
    design's flow — and saying so is not the same as failing to resolve it."""
    contract = load_verify_design(VERIFY_YML)

    plan = build_verification_plan(
        project, contract,
        dut_dir=tmp_path / "gen-top/some_other_design",
        consumer_root=tmp_path,
    )

    assert not any(fl.targets_this_design for fl in plan.flows)
    assert all(fl.note and "not this design's" in fl.note for fl in plan.flows)
    assert not any(fl.unresolved_reason for fl in plan.flows)


def test_a_flow_elaborating_the_wrong_top_module_is_a_finding(project, tmp_path, generated):
    """Same DUT directory, different top-level module: the testbench would
    elaborate something this run never wrote."""
    contract = load_verify_design(VERIFY_YML)
    renamed = ResolvedProject(
        design=ResolvedDesign(
            name=project.design.name,
            top_module="some_other_top",
            top_ports=project.design.top_ports,
        ),
    )

    plan = build_verification_plan(
        renamed, contract,
        dut_dir=generated / DUT_REL, consumer_root=generated,
    )

    assert all(fl.targets_this_design for fl in plan.flows)
    assert all("some_other_top" in fl.unresolved_reason for fl in plan.flows)


def test_a_module_entry_point_resolves_to_its_instances():
    """An HLS unit flow names one module, not the generated top."""
    from forge.ir.build import build_project_ir

    trigger_design = REPO_ROOT / "plugins/trigger_demo/forge/designs/design.yml"
    trigger_modules = REPO_ROOT / "plugins/trigger_demo/forge/modules.yml"
    trigger_verify = REPO_ROOT / "plugins/trigger_demo/forge/verify/design.verification.yml"

    proj = build_project_ir(trigger_design, contracts_from=trigger_modules)
    plan = build_verification_plan(proj, load_verify_design(trigger_verify))

    module_flows = [fl for fl in plan.flows if fl.dut_kind == "module"]
    assert module_flows, "trigger_demo declares single-module HLS flows"
    for fl in module_flows:
        assert fl.targets_this_design
        assert fl.entry_point_module
        assert fl.entry_point_instances


def test_no_contract_leaves_the_plan_unpopulated_for_a_pre_generation_ir():
    """`forge inspect` never runs the generator, so it has no top-level
    ports and nothing to plan — and must say so rather than claim an empty
    plan."""
    empty = ResolvedProject(design=ResolvedDesign(name="d"))

    plan = build_verification_plan(empty, None)

    assert plan.populated is False
    assert plan.note == ResolvedVerificationPlan().note


def test_port_divergences_reports_both_directions_and_shape():
    plan = ResolvedVerificationPlan(
        populated=True,
        stimulus=[ResolvedVerificationBinding(top_port="d_in", direction="in", width=8)],
        observation=[ResolvedVerificationBinding(top_port="d_out", direction="out", width=4)],
        clocks=["ap_clk"],
        resets=["ap_rst"],
    )

    # Exact agreement (clock/reset included) reports nothing.
    assert port_divergences(plan, {
        "d_in": ("in", 8), "d_out": ("out", 4),
        "ap_clk": ("in", 1), "ap_rst": ("in", 1),
    }) == []

    findings = port_divergences(plan, {
        "d_in": ("out", 8),      # direction differs
        "d_out": ("out", 32),    # width differs
        "ap_clk": ("in", 1),
        "extra": ("in", 1),      # only the DUT has it
        # ap_rst missing: only the design has it
    })
    joined = "\n".join(findings)
    assert "ap_rst: the resolved design has this port, the DUT does not" in joined
    assert "extra: the DUT has this port, the resolved design does not" in joined
    assert "d_in: direction in in the resolved design, out in the DUT" in joined
    assert "d_out: width 4 in the resolved design, 32 in the DUT" in joined


def test_port_divergences_accepts_either_direction_spelling():
    """`input`/`output` (port_map.yaml) and `in`/`out` (the IR) are the same
    fact written two ways — a spelling difference is not a divergence."""
    plan = ResolvedVerificationPlan(
        populated=True,
        stimulus=[ResolvedVerificationBinding(top_port="d_in", direction="in", width=8)],
    )

    assert port_divergences(plan, {"d_in": ("input", 8)}) == []


def test_preflight_confirms_a_matching_dut(generated):
    from forge.verification.preflight import PreflightResult, _check_against_verification_plan

    result = PreflightResult()
    _check_against_verification_plan(
        generated / DUT_REL / "port_map.yaml", result, flow_name="passthrough_xsim",
    )

    assert result.warnings == []
    assert any("match the resolved design" in n for n in result.notes)


def test_preflight_reports_a_dut_that_drifted_from_the_design(generated, tmp_path):
    """The failure this check exists for: a port map from a different
    generation than the IR beside it — today a mid-simulation elaboration
    error against a port that isn't there."""
    import yaml

    from forge.verification.preflight import PreflightResult, _check_against_verification_plan

    src = generated / DUT_REL
    port_map = yaml.safe_load((src / "port_map.yaml").read_text())
    port_map["port_groups"]["outputs"][0]["width"] = 999
    (tmp_path / "port_map.yaml").write_text(yaml.safe_dump(port_map))
    (tmp_path / "design.ir.json").write_text((src / "design.ir.json").read_text())

    result = PreflightResult()
    _check_against_verification_plan(
        tmp_path / "port_map.yaml", result, flow_name="passthrough_xsim",
    )

    assert len(result.warnings) == 1
    assert "999" in result.warnings[0]


def test_preflight_is_silent_without_an_ir_beside_the_port_map(generated, tmp_path):
    """A single-module HLS flow has no generated top level and no
    design.ir.json — the check must add nothing rather than fail."""
    from forge.verification.preflight import PreflightResult, _check_against_verification_plan

    (tmp_path / "port_map.yaml").write_text(
        (generated / DUT_REL / "port_map.yaml").read_text()
    )

    result = PreflightResult()
    _check_against_verification_plan(
        tmp_path / "port_map.yaml", result, flow_name="whatever",
    )

    assert result.warnings == [] and result.notes == [] and result.errors == []
