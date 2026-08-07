"""
Proofs that write_structural_verilog's report["top_ports"] must exactly match what
forge.core.utils.hdl_parser._scan_verilog_ports would parse back out of
the generated file — proving the new structured data is a faithful,
lossless stand-in for the regex re-parse that generate_port_map used to
do, before generate_port_map is switched to consume it instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

from forge.contracts.config import DesignConfig
from forge.contracts.contract_loader import load_contracts_for_design, synthesize_ip_info
from forge.contracts.matcher import auto_match_ports
from forge.generation.generators.structural_verilog import write_structural_verilog
from core.utils.hdl_parser import _scan_verilog_ports
from core.cli.groups.topgen import generate_port_map

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSTHROUGH_DESIGN = REPO_ROOT / "plugins/passthrough_demo/forge/designs/design.yml"
PASSTHROUGH_MODULES = REPO_ROOT / "plugins/passthrough_demo/forge/modules.yml"
TRIGGER_DESIGN = REPO_ROOT / "plugins/trigger_demo/forge/designs/design.yml"
TRIGGER_MODULES = REPO_ROOT / "plugins/trigger_demo/forge/modules.yml"


def _report_top_ports_to_scan_shape(top_ports) -> Dict[str, Tuple[str, int]]:
    return {p["name"]: (p["direction"], p["width"]) for p in top_ports}


def _assert_top_ports_matches_scan(design_path: Path, modules_path: Path, tmp_path: Path) -> None:
    cfg = DesignConfig.load_relaxed(design_path)
    contracts = load_contracts_for_design(modules_path, design_path.parent)
    mapped = {}
    for m in cfg.modules:
        c = contracts.get(m.name) or (m.ip_info_key and contracts.get(m.ip_info_key))
        if c:
            mapped[m.name] = c
    ip_info = synthesize_ip_info(mapped)
    conn_map, global_nets, _match_report = auto_match_ports(cfg, ip_info, contracts=contracts or None)

    out_path = tmp_path / "algo_top.v"
    report = write_structural_verilog(
        cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
        ip_root=tmp_path / "ips", out_path=out_path, top_name="algo_top",
        system_yml=None, contracts=contracts,
    )

    assert report["top_ports"]  # sanity: not empty
    from_report = _report_top_ports_to_scan_shape(report["top_ports"])
    from_scan = _scan_verilog_ports(out_path)

    assert from_report == from_scan, (
        "write_structural_verilog's report['top_ports'] diverged from what "
        "_scan_verilog_ports parses back out of the generated file"
    )


def test_passthrough_demo_top_ports_matches_scan(tmp_path):
    _assert_top_ports_matches_scan(PASSTHROUGH_DESIGN, PASSTHROUGH_MODULES, tmp_path)


def test_trigger_demo_top_ports_matches_scan(tmp_path):
    """The bigger, more representative case: 7 modules, plenty of
    externalized/aliased/debug ports for the mapping to get subtly wrong."""
    _assert_top_ports_matches_scan(TRIGGER_DESIGN, TRIGGER_MODULES, tmp_path)


def _assert_port_map_byte_identical(design_path: Path, modules_path: Path, tmp_path: Path) -> None:
    cfg = DesignConfig.load_relaxed(design_path)
    contracts = load_contracts_for_design(modules_path, design_path.parent)
    mapped = {}
    for m in cfg.modules:
        c = contracts.get(m.name) or (m.ip_info_key and contracts.get(m.ip_info_key))
        if c:
            mapped[m.name] = c
    ip_info = synthesize_ip_info(mapped)
    conn_map, global_nets, _match_report = auto_match_ports(cfg, ip_info, contracts=contracts or None)

    out_path = tmp_path / "algo_top.v"
    report = write_structural_verilog(
        cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
        ip_root=tmp_path / "ips", out_path=out_path, top_name="algo_top",
        system_yml=None, contracts=contracts,
    )
    structured_ports = _report_top_ports_to_scan_shape(report["top_ports"])

    old_path_out = tmp_path / "old_path" / "port_map.yaml"
    new_path_out = tmp_path / "new_path" / "port_map.yaml"

    generate_port_map(out_path, old_path_out, cfg.interface_metadata)  # ports=None -> re-parse
    generate_port_map(out_path, new_path_out, cfg.interface_metadata, ports=structured_ports)

    old_bytes = old_path_out.read_bytes()
    new_bytes = new_path_out.read_bytes()
    assert old_bytes == new_bytes, (
        "port_map.yaml diverged between the legacy re-parse path (ports=None) "
        "and the new structured-data path (ports=report['top_ports'])"
    )
    assert len(old_bytes) > 0


def test_passthrough_demo_port_map_is_byte_identical(tmp_path):
    _assert_port_map_byte_identical(PASSTHROUGH_DESIGN, PASSTHROUGH_MODULES, tmp_path)


def test_trigger_demo_port_map_is_byte_identical(tmp_path):
    _assert_port_map_byte_identical(TRIGGER_DESIGN, TRIGGER_MODULES, tmp_path)
