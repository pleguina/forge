"""Tests for forge.analyze.design_explorer.graph_model — the
DesignGraph projection's shape/provenance/determinism.

Runs against both real reference plugins' real IR (via
build_project_ir_with_match_report, reused, not reimplemented) — same
house convention as test_ir_provenance.py/test_ir_plan.py.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from forge.analyze.design_explorer.graph_model import (
    DESIGN_GRAPH_SCHEMA,
    GraphNodeKind,
    build_design_graph,
)
from forge.ir.build import build_project_ir_with_match_report
from forge.ir.serialize import content_hash

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSTHROUGH_DESIGN = REPO_ROOT / "plugins/passthrough_demo/forge/designs/design.yml"
PASSTHROUGH_MODULES = REPO_ROOT / "plugins/passthrough_demo/forge/modules.yml"
TRIGGER_DESIGN = REPO_ROOT / "plugins/trigger_demo/forge/designs/design.yml"
TRIGGER_MODULES = REPO_ROOT / "plugins/trigger_demo/forge/modules.yml"


def _build(design: Path, modules: Path):
    project, cfg, match_report = build_project_ir_with_match_report(design, contracts_from=modules)
    return project


@pytest.mark.parametrize("design,modules", [
    (PASSTHROUGH_DESIGN, PASSTHROUGH_MODULES),
    (TRIGGER_DESIGN, TRIGGER_MODULES),
])
def test_design_graph_provenance_matches_real_content_hash(design, modules):
    project = _build(design, modules)
    graph = build_design_graph(project, source_roots=[REPO_ROOT])

    assert graph.schema == DESIGN_GRAPH_SCHEMA
    assert graph.source_ir_schema_version == project.schema_version
    assert graph.source_ir_content_hash == content_hash(project)


def test_trigger_demo_real_node_and_edge_counts():
    """Confirmed real counts: 7
    modules, 10 instances, 45 connections. Every real connection must
    resolve to exactly one GraphEdge — the direct "complete edge
    coverage" proof, including external-port connections."""
    project = _build(TRIGGER_DESIGN, TRIGGER_MODULES)
    graph = build_design_graph(project, source_roots=[REPO_ROOT])

    assert len(project.design.modules) == 7
    assert len(project.design.instances) == 10
    assert len(project.design.connections) == 45
    assert len(graph.edges) == 45

    kinds = {}
    for n in graph.nodes:
        kinds[n.kind] = kinds.get(n.kind, 0) + 1
    assert kinds[GraphNodeKind.INSTANCE] == 10
    assert kinds[GraphNodeKind.MODULE_GROUP] == 7

    # Every edge endpoint is a real, resolvable node id.
    node_ids = {n.id for n in graph.nodes}
    for edge in graph.edges:
        assert edge.source in node_ids, edge
        assert edge.target in node_ids, edge

    # Every real IR connection id appears exactly once.
    conn_ids = {c.id for c in project.design.connections}
    edge_ids = [e.id for e in graph.edges]
    assert sorted(edge_ids) == sorted(conn_ids)
    assert len(edge_ids) == len(set(edge_ids))


@pytest.mark.parametrize("design,modules", [
    (PASSTHROUGH_DESIGN, PASSTHROUGH_MODULES),
    (TRIGGER_DESIGN, TRIGGER_MODULES),
])
def test_every_external_endpoint_connection_resolves_to_a_real_external_port_node(design, modules):
    project = _build(design, modules)
    graph = build_design_graph(project, source_roots=[REPO_ROOT])
    node_ids = {n.id for n in graph.nodes}
    external_conns = [c for c in project.design.connections if c.producer.instance_id == "$external"]
    assert external_conns, "expected at least one real $external connection in this fixture"
    for c in external_conns:
        sig = c.producer.interface_name or c.producer.port
        expected_id = f"top:{sig}"
        assert expected_id in node_ids
        matching_edge = next(e for e in graph.edges if e.id == c.id)
        assert matching_edge.source == expected_id


def test_design_graph_construction_is_deterministic():
    project = _build(TRIGGER_DESIGN, TRIGGER_MODULES)
    graph_a = build_design_graph(project, source_roots=[REPO_ROOT])
    graph_b = build_design_graph(project, source_roots=[REPO_ROOT])

    def _ser(g):
        return json.dumps(dataclasses.asdict(g), sort_keys=True, default=str)

    assert _ser(graph_a) == _ser(graph_b)


@pytest.mark.parametrize("design,modules", [
    (PASSTHROUGH_DESIGN, PASSTHROUGH_MODULES),
    (TRIGGER_DESIGN, TRIGGER_MODULES),
])
def test_design_graph_never_leaks_an_absolute_path(design, modules):
    project = _build(design, modules)
    graph = build_design_graph(project, source_roots=[REPO_ROOT])
    payload = json.dumps(dataclasses.asdict(graph), default=str)
    repo_str = str(REPO_ROOT)
    assert repo_str not in payload, "DesignGraph must never leak the real checkout's absolute path"
    assert str(Path.home()) not in payload or str(Path.home()) == "/"


def test_module_group_and_domain_group_nodes_carry_real_membership():
    project = _build(TRIGGER_DESIGN, TRIGGER_MODULES)
    graph = build_design_graph(project, source_roots=[REPO_ROOT])

    dec_group = next(n for n in graph.nodes if n.id == "module:dec")
    assert dec_group.kind == GraphNodeKind.MODULE_GROUP
    assert set(dec_group.members) == {"dec_0", "dec_1", "dec_2", "dec_3"}

    for inst in project.design.instances:
        if inst.module == "dec":
            node = next(n for n in graph.nodes if n.id == inst.id)
            assert node.parent == "module:dec"

    domain_groups = [n for n in graph.nodes if n.kind == GraphNodeKind.DOMAIN_GROUP]
    assert domain_groups, "expected at least one real clock/reset domain group"


def test_object_registry_covers_every_instance_module_and_connection():
    project = _build(TRIGGER_DESIGN, TRIGGER_MODULES)
    graph = build_design_graph(project, source_roots=[REPO_ROOT])
    object_ids = {(o.kind, o.id) for o in graph.objects}

    for inst in project.design.instances:
        assert ("instance", inst.id) in object_ids
    for mod in project.design.modules:
        assert ("module-definition", f"module:{mod.name}") in object_ids
    for conn in project.design.connections:
        assert ("connection", conn.id) in object_ids
