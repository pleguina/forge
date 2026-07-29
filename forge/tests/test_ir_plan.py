"""
Tests for forge.ir.plan (release-plan §3.4 — deterministic generation-plan
artifact) in isolation, with synthetic ResolvedProject/MatchReport
fixtures — no CLI involved. Real end-to-end coverage (against
plugins/trigger_demo/passthrough_demo) lives in test_build_cli_group.py.
"""

from __future__ import annotations

from forge.ir.model import (
    DiagnosticReference,
    MatchingEvidence,
    ResolvedConnection,
    ResolvedDesign,
    ResolvedEndpoint,
    ResolvedProject,
    ResolvedTransformation,
)
from forge.ir.plan import build_generation_plan, plan_hash
from forge.topgen.ip.matcher import MatchReport, RejectedMatch


def _connection(id_, src_inst, src_port, dst_inst, dst_port, *, wiring_method,
                transformations=None, matching_evidence=None):
    return ResolvedConnection(
        id=id_,
        producer=ResolvedEndpoint(instance_id=src_inst, port=src_port),
        consumer=ResolvedEndpoint(instance_id=dst_inst, port=dst_port),
        wiring_method=wiring_method,
        transformations=transformations or [],
        matching_evidence=matching_evidence,
    )


def _project(connections, diagnostics=None, generated_from=None):
    design = ResolvedDesign(
        name="synthetic", connections=connections, diagnostics=diagnostics or [],
    )
    return ResolvedProject(
        design=design, forge_version="0.0.0",
        generated_from=generated_from or {"design": "/abs/path/design.yml"},
    )


def _build(project, match_report=None, **overrides):
    kwargs = dict(
        topology_group_issues=[], cardinality_issues=[], cdc_issues=[],
        compat_mode_modules=[], output_artifacts=["algo_top.v"],
    )
    kwargs.update(overrides)
    return build_generation_plan(project, match_report or MatchReport(), **kwargs)


def test_inferred_vs_explicit_classification():
    conns = [
        _connection("c1", "a", "out", "b", "in", wiring_method="auto_match"),
        _connection("c2", "a", "out2", "b", "in2", wiring_method="topology_group"),
        _connection("c3", "a", "out3", "b", "in3", wiring_method="contract_wiring"),
        _connection("c4", "a", "out4", "b", "in4", wiring_method="port_map"),
        _connection("c5", "a", "out5", "b", "in5", wiring_method="heuristic"),
        _connection("c6", "$external", None, "b", "clk", wiring_method=None),
    ]
    plan = _build(_project(conns))
    assert {c.id for c in plan.inferred_connections} == {"c1", "c2"}
    assert {c.id for c in plan.explicit_connections} == {"c3", "c4"}
    # heuristic/None (global nets) are in neither bucket
    all_ids = {c.id for c in plan.inferred_connections} | {c.id for c in plan.explicit_connections}
    assert "c5" not in all_ids and "c6" not in all_ids


def test_transformations_and_latency_changes():
    conns = [
        _connection("c1", "a", "out", "b", "in", wiring_method="port_map", transformations=[
            ResolvedTransformation(id="x1", kind="pipeline_register", cycles=2),
            ResolvedTransformation(id="x2", kind="fanout"),
        ]),
    ]
    plan = _build(_project(conns))
    assert len(plan.generated_transformations) == 2
    assert [t.kind for t in plan.latency_changes] == ["pipeline_register"]
    assert plan.latency_changes[0].cycles == 2
    assert plan.latency_changes[0].connection_id == "c1"


def test_matching_evidence_and_rejected_matches():
    ev = MatchingEvidence(producer_width=8, consumer_width=8)
    conns = [
        _connection("c1", "a", "out", "b", "in", wiring_method="port_map", matching_evidence=ev),
        _connection("c2", "a", "out2", "b", "in2", wiring_method="port_map"),  # no evidence
    ]
    mr = MatchReport(rejected_matches=[
        RejectedMatch(module_pair=("a", "b"), scope="auto_match_group", src_ref="x", reason="no match"),
    ])
    plan = _build(_project(conns), match_report=mr)
    assert "c1" in plan.matching_evidence
    assert "c2" not in plan.matching_evidence
    assert plan.matching_evidence["c1"]["producer_width"] == 8
    assert len(plan.matching_evidence["unmatched_candidates"]) == 1
    assert plan.matching_evidence["unmatched_candidates"][0]["src_ref"] == "x"


def test_diagnostics_become_unresolved_issues():
    conns = [_connection("c1", "a", "out", "b", "in", wiring_method="port_map")]
    diag = DiagnosticReference(severity="warning", message="timing note", object_id="module:a")
    plan = _build(_project(conns, diagnostics=[diag]))
    assert len(plan.unresolved_issues) == 1
    issue = plan.unresolved_issues[0]
    assert issue.severity == "warning"
    assert issue.category == "diagnostic"
    assert issue.message == "timing note"
    assert issue.object_id == "module:a"


def test_plan_hash_is_deterministic():
    conns = [_connection("c1", "a", "out", "b", "in", wiring_method="port_map")]
    plan1 = _build(_project(conns))
    plan2 = _build(_project(conns))
    assert plan_hash(plan1) == plan_hash(plan2)


def test_plan_hash_changes_with_real_content_change():
    base = [_connection("c1", "a", "out", "b", "in", wiring_method="port_map")]
    changed = [_connection("c1", "a", "out", "b", "in", wiring_method="port_map", transformations=[
        ResolvedTransformation(id="x1", kind="pipeline_register", cycles=3),
    ])]
    h1 = plan_hash(_build(_project(base)))
    h2 = plan_hash(_build(_project(changed)))
    assert h1 != h2


def test_plan_hash_ignores_generated_from():
    """A plan computed against two different checkout locations of the
    identical design must hash identically — generated_from carries
    absolute, machine-specific paths."""
    conns = [_connection("c1", "a", "out", "b", "in", wiring_method="port_map")]
    plan_a = _build(_project(conns, generated_from={"design": "/home/alice/repo/design.yml"}))
    plan_b = _build(_project(conns, generated_from={"design": "/ci/runner/xyz/design.yml"}))
    assert plan_hash(plan_a) == plan_hash(plan_b)


def test_compat_mode_and_output_artifacts_pass_through():
    conns = [_connection("c1", "a", "out", "b", "in", wiring_method="port_map")]
    plan = _build(
        _project(conns),
        compat_mode_modules=["legacy_mod"],
        output_artifacts=["algo_top.v", "port_map.yaml"],
    )
    assert plan.compat_mode_modules == ["legacy_mod"]
    assert plan.output_artifacts == ["algo_top.v", "port_map.yaml"]
