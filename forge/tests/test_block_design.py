"""Unit coverage for forge.generation.generators.block_design.write_bd_tcl —
report shape, tie-off/deterministic-port Tcl content, and the
CDC/register-stage/delay-cycle rejection guard.

Uses the same PASSTHROUGH_DESIGN/TRIGGER_DESIGN fixtures and resolution
helper test_generation_ir_equivalence.py already relies on.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from forge.contracts.config import Connection, DesignConfig, Module
from forge.contracts.contract_loader import load_contracts_for_design, synthesize_ip_info
from forge.contracts.matcher import auto_match_ports
from forge.generation.generators.block_design import (
    _canon_pin,
    _create_port_cmd,
    _gather_hdl_sources_for_mod,
    _is_hdl,
    _pins_are_numeric,
    _unsupported_bd_features,
    write_bd_tcl,
)
from forge.generation.generators.structural_verilog import write_structural_verilog
from forge.generation.support_rtl import SUPPORT_RTL_DIR

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSTHROUGH_DESIGN = REPO_ROOT / "plugins/passthrough_demo/forge/designs/design.yml"
PASSTHROUGH_MODULES = REPO_ROOT / "plugins/passthrough_demo/forge/modules.yml"
TRIGGER_DESIGN = REPO_ROOT / "plugins/trigger_demo/forge/designs/design.yml"
TRIGGER_MODULES = REPO_ROOT / "plugins/trigger_demo/forge/modules.yml"


def _resolve(design_path: Path, modules_path: Path):
    cfg = DesignConfig.load_relaxed(design_path)
    contracts = load_contracts_for_design(modules_path, design_path.parent)
    mapped = {}
    for m in cfg.modules:
        c = contracts.get(m.name) or (m.ip_info_key and contracts.get(m.ip_info_key))
        if c:
            mapped[m.name] = c
    ip_info = synthesize_ip_info(mapped)
    conn_map, global_nets, match_report = auto_match_ports(cfg, ip_info, contracts=contracts or None)
    return cfg, ip_info, conn_map, global_nets


def test_write_bd_tcl_returns_report_dict_with_verilog_report_shape(tmp_path: Path) -> None:
    cfg, ip_info, conn_map, global_nets = _resolve(PASSTHROUGH_DESIGN, PASSTHROUGH_MODULES)

    report = write_bd_tcl(
        cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
        out_path=tmp_path / "block_design.tcl", bd_name="top_bd",
        src_root=PASSTHROUGH_DESIGN.parent, ip_root=tmp_path / "ips",
    )

    assert report is not None
    for key in ("open_outputs", "tied_to_zero", "total_modules", "total_instances", "total_connections", "top_ports"):
        assert key in report
    assert report["total_modules"] == 1
    assert report["total_instances"] == 1
    assert report["open_outputs"] == []
    assert report["tied_to_zero"] == []
    assert {p["name"] for p in report["top_ports"]} == {
        "ap_clk", "ap_rst", "pt_data_in", "pt_data_in_valid", "pt_data_out", "pt_data_out_valid",
    }


def test_write_bd_tcl_top_ports_match_verilog_mode_for_same_design(tmp_path: Path) -> None:
    """The core parity assertion: bd and verilog mode must resolve the same
    top-level port set/names/widths for the same design, since both call
    the same shared resolver."""
    cfg, ip_info, conn_map, global_nets = _resolve(PASSTHROUGH_DESIGN, PASSTHROUGH_MODULES)

    bd_report = write_bd_tcl(
        cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
        out_path=tmp_path / "bd" / "block_design.tcl", bd_name="top_bd",
        src_root=PASSTHROUGH_DESIGN.parent, ip_root=tmp_path / "ips",
    )
    verilog_report = write_structural_verilog(
        cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
        ip_root=tmp_path / "ips", out_path=tmp_path / "verilog" / "algo_top.v",
        top_name="algo_top", system_yml=None,
    )

    assert bd_report["top_ports"] == verilog_report["top_ports"]
    assert bd_report["open_outputs"] == verilog_report["open_outputs"]
    assert bd_report["tied_to_zero"] == verilog_report["tied_to_zero"]


def test_write_bd_tcl_tie_off_emits_xlconstant_and_connect_bd_net(tmp_path: Path) -> None:
    (tmp_path / "interfaces").mkdir()
    (tmp_path / "interfaces" / "src.interface.yaml").write_text(
        "ip_interface:\n"
        "  module_name: src\n"
        "  ip_info_key: src\n"
        "  source_type: rtl\n"
        "  roles:\n"
        "    clock_primary: {raw_port: ap_clk, direction: input, width: 1}\n"
        "    reset_primary: {raw_port: ap_rst, direction: input, width: 1}\n"
        "    unconnected_in: {raw_port: unconnected_in, direction: input, width: 8}\n"
        "    dout: {raw_port: dout, direction: output, width: 8}\n"
    )
    modules_yml = tmp_path / "modules.yml"
    modules_yml.write_text(
        "registry_version: '1'\n"
        "modules:\n"
        "  - name: src\n"
        "    kind: rtl\n"
        "    top: src_top\n"
        "    src: [src.v]\n"
        "    interface_contract: interfaces/src.interface.yaml\n"
    )
    design_yml = tmp_path / "design.yml"
    design_yml.write_text(
        "part: xcvu13p\n"
        "clock_period: 4.0\n"
        "modules:\n"
        "  - name: src\n"
        "    top: src_top\n"
        "    src: [src.v]\n"
        "connections: []\n"
    )

    cfg, ip_info, conn_map, global_nets = _resolve(design_yml, modules_yml)
    out_path = tmp_path / "block_design.tcl"
    report = write_bd_tcl(
        cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
        out_path=out_path, bd_name="top_bd", src_root=design_yml.parent, ip_root=tmp_path / "ips",
    )

    assert report["tied_to_zero"] == [("src", "unconnected_in", 8)]
    assert report["open_outputs"] == [("src", "dout", 8)]

    tcl = out_path.read_text()
    assert "create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 tie_const_8" in tcl
    assert "CONFIG.CONST_WIDTH {8}" in tcl
    assert "CONFIG.CONST_VAL {0}" in tcl
    assert "connect_bd_net [get_bd_pins tie_const_8/dout] [get_bd_pins src/unconnected_in]" in tcl


def test_write_bd_tcl_external_ports_use_create_bd_port_not_make_bd_pins_external(tmp_path: Path) -> None:
    cfg, ip_info, conn_map, global_nets = _resolve(PASSTHROUGH_DESIGN, PASSTHROUGH_MODULES)

    out_path = tmp_path / "block_design.tcl"
    write_bd_tcl(
        cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
        out_path=out_path, bd_name="top_bd", src_root=PASSTHROUGH_DESIGN.parent, ip_root=tmp_path / "ips",
    )

    tcl = out_path.read_text()
    assert "make_bd_pins_external" not in tcl
    assert "create_bd_port -dir I -from 7 -to 0 pt_data_in" in tcl
    assert "connect_bd_net [get_bd_ports pt_data_in] [get_bd_pins pt/data_in]" in tcl
    assert "create_bd_port -dir O -from 7 -to 0 pt_data_out" in tcl
    assert "connect_bd_net [get_bd_pins pt/data_out] [get_bd_ports pt_data_out]" in tcl


def test_write_bd_tcl_instantiates_register_stage_and_signal_delay_cells(tmp_path: Path) -> None:
    """trigger_demo declares register_stages (col->trig, 2 stages) and
    delay_cycles (trig->tfan, 3 cycles) — write_bd_tcl must instantiate real
    RegisterStage/signal_delay cells for these, from the framework's own
    packaged support RTL — no plugin-tree search involved."""
    cfg, ip_info, conn_map, global_nets = _resolve(TRIGGER_DESIGN, TRIGGER_MODULES)

    out_path = tmp_path / "block_design.tcl"
    write_bd_tcl(
        cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
        out_path=out_path, bd_name="top_bd", src_root=TRIGGER_DESIGN.parent,
        ip_root=tmp_path / "ips",
    )

    tcl = out_path.read_text()
    assert "create_bd_cell -type module -reference RegisterStage reg_stage_0" in tcl
    assert "set_property CONFIG.STAGES {2} [get_bd_cells reg_stage_0]" in tcl
    assert "connect_bd_net [get_bd_ports ap_clk] [get_bd_pins reg_stage_0/clk]" in tcl
    assert "create_bd_cell -type module -reference signal_delay delay_0" in tcl
    assert "set_property CONFIG.DEPTH {3} [get_bd_cells delay_0]" in tcl
    assert "connect_bd_net [get_bd_ports ap_rst] [get_bd_pins delay_0/rst]" in tcl
    # The packaged support RTL got added to the project alongside the
    # design's own modules.
    assert (SUPPORT_RTL_DIR / "RegisterStage.v").as_posix() in tcl
    assert (SUPPORT_RTL_DIR / "signal_delay.v").as_posix() in tcl


# ---------------------------------------------------------------------------
# Pure helper-function unit tests
# ---------------------------------------------------------------------------

def test_unsupported_bd_features_flags_cdc() -> None:
    cfg = SimpleNamespace(
        connections=[Connection(from_="a", to="b", cdc={"kind": "level_sync"})],
        reset_domains={},
    )
    problems = _unsupported_bd_features(cfg)
    assert len(problems) == 1
    assert "cdc" in problems[0]


def test_unsupported_bd_features_empty_for_a_reset_sync_domain() -> None:
    """reset_domains.*.sync: reset_sync is supported (see
    test_write_bd_tcl_instantiates_reset_sync_cell below) — only
    boundary/cdc are still rejected."""
    cfg = SimpleNamespace(
        connections=[],
        reset_domains={"rst_b": {"sync": "reset_sync"}},
    )
    assert _unsupported_bd_features(cfg) == []


def test_unsupported_bd_features_empty_for_a_plain_design() -> None:
    cfg = SimpleNamespace(
        connections=[Connection(from_="a", to="b")],
        reset_domains={"rst_b": {}},
    )
    assert _unsupported_bd_features(cfg) == []


def _write_reset_sync_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """A two-module RTL design where dst lives in its own clk_dst/rst_dst
    domain, with rst_dst declared as a reset_domains.*.sync: reset_sync
    destination — the smallest fixture that exercises write_bd_tcl's
    reset-synchronizer path without any cdc: connection (still rejected)."""
    (tmp_path / "interfaces").mkdir()
    (tmp_path / "interfaces" / "src.interface.yaml").write_text(
        "ip_interface:\n"
        "  module_name: src\n  ip_info_key: src\n  source_type: rtl\n"
        "  roles:\n"
        "    clock_primary: {raw_port: ap_clk, direction: input, width: 1}\n"
        "    reset_primary: {raw_port: ap_rst, direction: input, width: 1}\n"
        "    dout: {raw_port: dout, direction: output, width: 8}\n"
    )
    (tmp_path / "interfaces" / "dst.interface.yaml").write_text(
        "ip_interface:\n"
        "  module_name: dst\n  ip_info_key: dst\n  source_type: rtl\n"
        "  roles:\n"
        "    clock_primary: {raw_port: clk_dst, direction: input, width: 1}\n"
        "    reset_primary: {raw_port: rst_dst, direction: input, width: 1}\n"
        "    din: {raw_port: din, direction: input, width: 8}\n"
    )
    modules_yml = tmp_path / "modules.yml"
    modules_yml.write_text(
        "registry_version: '1'\n"
        "modules:\n"
        "  - name: src\n    kind: rtl\n    top: src_top\n    src: [src.v]\n"
        "    interface_contract: interfaces/src.interface.yaml\n"
        "  - name: dst\n    kind: rtl\n    top: dst_top\n    src: [dst.v]\n"
        "    interface_contract: interfaces/dst.interface.yaml\n"
    )
    design_yml = tmp_path / "design.yml"
    design_yml.write_text(
        "part: xcvu13p\nclock_period: 4.0\nblock_protocol: none\n"
        "reset_domains:\n"
        "  rst_dst:\n    derived_from: ap_rst\n    sync: reset_sync\n"
        "modules:\n"
        "  - name: src\n    top: src_top\n    src: [src.v]\n"
        "  - name: dst\n    top: dst_top\n    src: [dst.v]\n"
        "connections:\n"
        "  - from: src\n    to: dst\n    port_map: [[dout, din]]\n"
    )
    return design_yml, modules_yml


def test_write_bd_tcl_instantiates_reset_sync_cell(tmp_path: Path) -> None:
    """dst's own rst_dst domain (reset_domains.rst_dst.sync: reset_sync)
    gets a real cdc_reset_sync cell, clocked by dst's own clk_dst domain,
    with sync_rst_out fanned to dst's reset pin directly — no intermediate
    net name needed, unlike verilog mode. The raw rst_dst top-level port
    still gets created but is left unconnected (nothing drives it once a
    real synchronizer exists for the domain)."""
    design_yml, modules_yml = _write_reset_sync_fixture(tmp_path)
    cfg = DesignConfig.load_relaxed(design_yml)
    contracts = load_contracts_for_design(modules_yml, design_yml.parent)
    mapped = {m.name: contracts[m.name] for m in cfg.modules if m.name in contracts}
    ip_info = synthesize_ip_info(mapped)
    conn_map, global_nets, match_report = auto_match_ports(cfg, ip_info, contracts=contracts or None)

    out_path = tmp_path / "block_design.tcl"
    write_bd_tcl(
        cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
        out_path=out_path, bd_name="top_bd", ip_root=tmp_path / "ips",
        contracts=contracts, match_report=match_report,
    )
    tcl = out_path.read_text()

    assert "create_bd_cell -type module -reference cdc_reset_sync rst_sync_0" in tcl
    assert "connect_bd_net [get_bd_ports clk_dst] [get_bd_pins rst_sync_0/dst_clk]" in tcl
    assert "connect_bd_net [get_bd_ports ap_rst] [get_bd_pins rst_sync_0/async_rst_in]" in tcl
    assert "connect_bd_net [get_bd_pins rst_sync_0/sync_rst_out] [get_bd_pins dst/rst_dst]" in tcl
    assert "create_bd_port -dir I rst_dst" in tcl
    # rst_dst's own top-level port is never wired anywhere: dst's real
    # reset pin comes from rst_sync_0, not the raw domain port.
    assert "[get_bd_ports rst_dst]" not in tcl


def test_write_bd_tcl_requires_match_report_for_reset_sync(tmp_path: Path) -> None:
    design_yml, modules_yml = _write_reset_sync_fixture(tmp_path)
    cfg = DesignConfig.load_relaxed(design_yml)
    contracts = load_contracts_for_design(modules_yml, design_yml.parent)
    mapped = {m.name: contracts[m.name] for m in cfg.modules if m.name in contracts}
    ip_info = synthesize_ip_info(mapped)
    conn_map, global_nets, _match_report = auto_match_ports(cfg, ip_info, contracts=contracts or None)

    with pytest.raises(ValueError, match="reset_domains.*reset_sync"):
        write_bd_tcl(
            cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
            out_path=tmp_path / "block_design.tcl", bd_name="top_bd",
            ip_root=tmp_path / "ips", contracts=contracts,
            # no match_report
        )


def test_pins_are_numeric_true_for_a_shared_base() -> None:
    assert _pins_are_numeric(["mod_0", "mod_1", "mod_2"]) == (True, "mod")


def test_pins_are_numeric_false_for_differing_bases() -> None:
    numeric, base = _pins_are_numeric(["mod_0", "other_1"])
    assert numeric is False
    assert base in ("mod", "other")  # arbitrary set element, not meaningful when False


def test_pins_are_numeric_false_for_a_non_numeric_pin() -> None:
    assert _pins_are_numeric(["nonumeric"]) == (False, "")


def test_pins_are_numeric_false_for_an_empty_list() -> None:
    assert _pins_are_numeric([]) == (False, "")


def test_canon_pin_exact_match() -> None:
    ip_info = {"m": {"ports": [{"name": "cfg_vec"}]}}
    assert _canon_pin(ip_info, "m", "cfg_vec") == "cfg_vec"


def test_canon_pin_strips_trailing_index_to_base() -> None:
    ip_info = {"m": {"ports": [{"name": "cfg_vec"}]}}
    assert _canon_pin(ip_info, "m", "cfg_vec15") == "cfg_vec"


def test_canon_pin_falls_back_to_original_when_nothing_matches() -> None:
    ip_info = {"m": {"ports": [{"name": "unrelated"}]}}
    assert _canon_pin(ip_info, "m", "cfg_vec15") == "cfg_vec15"


@pytest.mark.parametrize("meta,expected", [
    ({"kind": "hdl"}, True),
    ({"kind": "HDL"}, True),
    ({"library": "hdl"}, True),
    ({"version": "rtl"}, True),
    ({"entity": "my_entity"}, True),
    ({"entity": "my_entity", "vendor": "xilinx.com"}, False),
    ({"kind": "hls", "vendor": "xilinx.com"}, False),
    ({}, False),
])
def test_is_hdl(meta, expected) -> None:
    assert _is_hdl(meta) is expected


@pytest.mark.parametrize("direction,name,width,expected", [
    ("in", "ap_clk", 1, "create_bd_port -dir I ap_clk"),
    ("out", "dout", 1, "create_bd_port -dir O dout"),
    ("in", "data_in", 8, "create_bd_port -dir I -from 7 -to 0 data_in"),
    ("out", "data_out", 32, "create_bd_port -dir O -from 31 -to 0 data_out"),
])
def test_create_port_cmd(direction, name, width, expected) -> None:
    assert _create_port_cmd(direction, name, width) == expected


def test_gather_hdl_sources_prefers_ip_info_sources_over_yaml(tmp_path: Path) -> None:
    mod = Module(name="m", top="m_top", src=["ignored.v"])
    meta = {"sources": ["/abs/path/m.v"], "packages": ["/abs/path/pkg.vhd"]}

    out = _gather_hdl_sources_for_mod(mod, meta, tmp_path)

    assert out == [("/abs/path/m.v", "verilog"), ("/abs/path/pkg.vhd", "vhdl")]


def test_gather_hdl_sources_falls_back_to_yaml_src_on_disk(tmp_path: Path) -> None:
    (tmp_path / "leaf.v").write_text("module leaf_top(); endmodule\n")
    mod = Module(name="leaf", top="leaf_top", src=["leaf.v"])

    out = _gather_hdl_sources_for_mod(mod, {}, tmp_path)

    assert len(out) == 1
    path, lang = out[0]
    assert path.endswith("leaf.v")
    assert lang == "verilog"


def test_gather_hdl_sources_returns_empty_when_nothing_resolves(tmp_path: Path) -> None:
    mod = Module(name="ghost", top="ghost_top", src=["does_not_exist.v"])
    assert _gather_hdl_sources_for_mod(mod, {}, tmp_path) == []
