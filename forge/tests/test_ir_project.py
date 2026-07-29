"""
Tests for forge.ir.project.project_to_conn_map — migration step 5's
equivalence proof (docs/development/release-readiness.md): the IR must be
able to reproduce the exact original conn_map/global_nets construction
order (via ResolvedConnection.emission_order), not just be set-equivalent
to it, before generation can safely be switched to consume it.

Covers both reference designs: plugins/passthrough_demo (1 module,
tool-free) and plugins/trigger_demo (7 modules, resolved from contracts
alone — no HLS build artifacts needed in this environment).
"""

from __future__ import annotations

from pathlib import Path

from topgen.config import DesignConfig
from topgen.ip.contract_loader import load_contracts_for_design, synthesize_ip_info
from topgen.ip.matcher import auto_match_ports

from forge.ir.build import assemble_project_ir
from forge.ir.project import project_to_conn_map

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSTHROUGH_DESIGN = REPO_ROOT / "plugins/passthrough_demo/forge/designs/design.yml"
PASSTHROUGH_MODULES = REPO_ROOT / "plugins/passthrough_demo/forge/modules.yml"
TRIGGER_DESIGN = REPO_ROOT / "plugins/trigger_demo/forge/designs/design.yml"
TRIGGER_MODULES = REPO_ROOT / "plugins/trigger_demo/forge/modules.yml"


def _resolve(design_path: Path, modules_path: Path):
    """Same resolution sequence forge.ir.build.build_project_ir uses,
    exposed here so the test can keep the intermediate conn_map/global_nets
    as ground truth to compare the IR's projection against."""
    cfg = DesignConfig.load_relaxed(design_path)
    contracts = load_contracts_for_design(modules_path, design_path.parent)
    mapped = {}
    for m in cfg.modules:
        c = contracts.get(m.name) or (m.ip_info_key and contracts.get(m.ip_info_key))
        if c:
            mapped[m.name] = c
    ip_info = synthesize_ip_info(mapped)
    conn_map, global_nets, match_report = auto_match_ports(cfg, ip_info, contracts=contracts or None)
    return cfg, contracts, ip_info, conn_map, global_nets, match_report


def _assert_ordered_equal(design_path, modules_path):
    cfg, contracts, ip_info, conn_map, global_nets, match_report = _resolve(design_path, modules_path)

    project = assemble_project_ir(
        cfg, design_path,
        contracts=contracts, ip_info_data=ip_info,
        conn_map=conn_map, global_nets=global_nets, match_report=match_report,
    )
    projected_conn_map, projected_global_nets = project_to_conn_map(project)

    # Ordered equality: both the key order AND each key's pair-list order
    # must match exactly — plain dict `==` would ignore insertion order and
    # hide exactly the class of bug this proof exists to catch.
    assert list(conn_map.items()) == list(projected_conn_map.items())
    assert list(global_nets.items()) == list(projected_global_nets.items())
    return project


def test_emission_order_is_contiguous_and_matches_construction_order():
    cfg, contracts, ip_info, conn_map, global_nets, match_report = _resolve(
        PASSTHROUGH_DESIGN, PASSTHROUGH_MODULES,
    )
    project = assemble_project_ir(
        cfg, PASSTHROUGH_DESIGN,
        contracts=contracts, ip_info_data=ip_info,
        conn_map=conn_map, global_nets=global_nets, match_report=match_report,
    )
    orders = sorted(c.emission_order for c in project.design.connections)
    assert orders == list(range(len(project.design.connections)))


def test_passthrough_demo_projection_matches_original_exactly():
    _assert_ordered_equal(PASSTHROUGH_DESIGN, PASSTHROUGH_MODULES)


def test_trigger_demo_projection_matches_original_exactly():
    """The bigger, more representative case: 7 modules, 45 connections,
    mixed contract_wiring/topology_group/port_map wiring methods."""
    project = _assert_ordered_equal(TRIGGER_DESIGN, TRIGGER_MODULES)
    assert len(project.design.connections) > 40  # sanity: not a degenerate case
