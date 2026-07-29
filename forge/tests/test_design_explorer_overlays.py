"""Tests for the visual-design-explorer's overlay joins (release-plan
Phase 8, slice 8.0B) — latency, contract maturity, diagnostic linking, and
the conservative "flow entry points" verification join.

Each test is the real regression guard for one of the three real join bugs
found by the Phase 8 investigation (see the release plan's Phase 8 notes
"Two real join-key gaps"), plus the diagnostic-linking id-space fix
(Defect 3).
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from forge.analyze.design_explorer.graph_model import (
    GraphNodeKind,
    MaturityStatus,
    build_design_graph,
    parse_object_reference,
)
from forge.analyze.design_explorer.verification_join import join_flow_entry_points
from forge.analyze.latency_static.graph import build_graph as build_latency_graph
from forge.ir.build import build_project_ir_with_match_report

REPO_ROOT = Path(__file__).resolve().parents[2]
TRIGGER_DESIGN = REPO_ROOT / "plugins/trigger_demo/forge/designs/design.yml"
TRIGGER_MODULES = REPO_ROOT / "plugins/trigger_demo/forge/modules.yml"
TRIGGER_VERIFY_DESIGN = REPO_ROOT / "plugins/trigger_demo/forge/verify/design.verification.yml"


def _latency_by_instance(design: Path, modules: Path) -> dict:
    lg = build_latency_graph(design, modules_yml_path=modules)
    return {n.instance_id: n.latency for n in lg.nodes.values() if n.latency is not None}


# ── Latency join regression (the highest-risk finding) ──────────────────

def test_multi_instance_latency_join_resolves_every_dec_instance_independently():
    """Regression guard for the bracket-vs-underscore instance-id
    mismatch (`"dec[0]"` vs `"dec_0"`) — the bug that would have gone
    unnoticed forever without a multi-instance module in a tested
    fixture. trigger_demo's `dec` (4 instances) is that fixture."""
    project, cfg, match_report = build_project_ir_with_match_report(TRIGGER_DESIGN, contracts_from=TRIGGER_MODULES)
    latency_by_instance = _latency_by_instance(TRIGGER_DESIGN, TRIGGER_MODULES)
    graph = build_design_graph(project, latency_by_instance=latency_by_instance, source_roots=[REPO_ROOT])

    dec_instance_ids = {i.id for i in project.design.instances if i.module == "dec"}
    assert dec_instance_ids == {"dec_0", "dec_1", "dec_2", "dec_3"}

    for inst_id in dec_instance_ids:
        node = next(n for n in graph.nodes if n.id == inst_id)
        # Each instance independently resolves its own latency data (or a
        # genuine, honest None) — never silently dropped because of an
        # id-format mismatch.
        assert node.latency is not None, f"{inst_id} lost its latency data to the join bug"

    trig_node = next(n for n in graph.nodes if n.id == "trig")
    assert trig_node.latency["cycles"] == 3


# ── Maturity: four real states, not a boolean ────────────────────────────

def test_maturity_summary_reports_a_real_mixed_status_not_just_the_extremes():
    project, cfg, match_report = build_project_ir_with_match_report(TRIGGER_DESIGN, contracts_from=TRIGGER_MODULES)
    graph = build_design_graph(project, source_roots=[REPO_ROOT])

    module_groups = [n for n in graph.nodes if n.kind == GraphNodeKind.MODULE_GROUP]
    assert any(n.maturity is not None and n.maturity.status == MaturityStatus.MIXED for n in module_groups), (
        "expected at least one real module with a genuine wiring-method mix"
    )
    dec = next(n for n in graph.nodes if n.id == "module:dec")
    assert dec.maturity.contract_edges > 0
    assert dec.maturity.topology_edges > 0


def test_compute_maturity_summary_exposes_real_per_module_name_lists():
    from forge.core.cli.groups.topgen import _compute_maturity_summary

    project, cfg, match_report = build_project_ir_with_match_report(TRIGGER_DESIGN, contracts_from=TRIGGER_MODULES)
    summary = _compute_maturity_summary(cfg, match_report)
    assert summary["modules"]["contract_driven_names"] == sorted(match_report.contract_driven_modules)
    assert summary["modules"]["compat_mode_names"] == sorted(match_report.compat_mode_modules)
    assert len(summary["modules"]["contract_driven_names"]) == summary["modules"]["contract_driven"]


# ── Diagnostic linking: id-space fix (Defect 3) ──────────────────────────

def test_parse_object_reference_handles_the_two_real_structured_conventions():
    assert parse_object_reference("module:dec") == parse_object_reference("module:dec")
    ref = parse_object_reference("module:dec")
    assert ref.kind == "module-definition" and ref.id == "dec"

    iface_ref = parse_object_reference("module:dec#interface:clock_primary")
    assert iface_ref.kind == "interface" and iface_ref.id == "dec#clock_primary"

    # Free-text validator locations (e.g. "connections[2].port_map") are
    # honestly unresolved, never guessed.
    assert parse_object_reference("connections[2].port_map") is None
    assert parse_object_reference(None) is None


def test_validator_diagnostic_object_id_round_trips_to_a_real_module_group_node(tmp_path: Path):
    """Real, end-to-end proof for the build.py:489 fix: a genuinely
    module-scoped validator issue (missing/mismatched module field) now
    gets a real, structured object_id and resolves to the real
    MODULE_GROUP node for that module — never fanned out onto an
    arbitrary instance, per Defect 3's resolution policy."""
    design_yml = tmp_path / "design.yml"
    (tmp_path / "foo.v").write_text("module foo_top(); endmodule\n")
    design_yml.write_text(textwrap.dedent("""\
        part: xcvu13p
        clock_period: 4.0
        modules:
          - name: foo
            top: foo_top
            src: [foo.v]
        connections: []
        """))

    project, cfg, match_report = build_project_ir_with_match_report(design_yml)
    linked = [d for d in project.design.diagnostics if d.object_id == "module:foo"]
    assert linked, "expected at least one validator diagnostic to round-trip to module:foo"

    graph = build_design_graph(project, source_roots=[tmp_path])
    module_group = next(n for n in graph.nodes if n.id == "module:foo")
    assert len(module_group.diagnostics) >= len(linked)
    messages = {d.message for d in module_group.diagnostics}
    assert any("non-C++ source" in m for m in messages)

    # Never fanned out onto an arbitrary instance id.
    for n in graph.nodes:
        if n.kind == GraphNodeKind.INSTANCE:
            assert n.diagnostics == ()  # direct instance-level diagnostics: none exist for this issue
            assert n.inherited_diagnostics == module_group.diagnostics


# ── Verification overlay: conservative "flow entry points" join ─────────

def test_verification_join_resolves_a_real_flow_to_its_real_declared_module():
    project, cfg, match_report = build_project_ir_with_match_report(TRIGGER_DESIGN, contracts_from=TRIGGER_MODULES)
    result = join_flow_entry_points(
        project, verify_design_path=TRIGGER_VERIFY_DESIGN,
        results_payload={"flow_name": "hit_decoder_xsim", "backend_id": "xsim"},
    )
    assert result == {"hit_decoder_xsim": "module:dec"}


def test_verification_join_is_honestly_empty_for_an_unknown_flow():
    project, cfg, match_report = build_project_ir_with_match_report(TRIGGER_DESIGN, contracts_from=TRIGGER_MODULES)
    result = join_flow_entry_points(
        project, verify_design_path=TRIGGER_VERIFY_DESIGN,
        results_payload={"flow_name": "no-such-flow"},
    )
    assert result == {}


def test_design_graph_carries_verification_flow_entry_points_in_the_object_registry():
    project, cfg, match_report = build_project_ir_with_match_report(TRIGGER_DESIGN, contracts_from=TRIGGER_MODULES)
    entry_points = join_flow_entry_points(
        project, verify_design_path=TRIGGER_VERIFY_DESIGN,
        results_payload={"flow_name": "hit_decoder_xsim", "backend_id": "xsim"},
    )
    graph = build_design_graph(project, verification_flow_entry_points=entry_points, source_roots=[REPO_ROOT])
    dec_object = next(o for o in graph.objects if o.id == "module:dec")
    assert dec_object.data["verification_flow_entry_points"] == ["hit_decoder_xsim"]
    assert graph.overlay_hashes.get("verification")
