"""
Golden-generation proof: write_structural_verilog must produce
byte-identical output whether it's
fed the conn_map/global_nets auto_match_ports returns directly, or the
same structures reconstructed by projecting them through the canonical IR
(forge.ir.project.project_to_conn_map). This is what makes it safe to
switch cmd_gen_top's actual generation input to be IR-driven without
changing a single byte of any existing generated algo_top.v.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.contracts.config import DesignConfig
from forge.contracts.contract_loader import load_contracts_for_design, synthesize_ip_info
from forge.contracts.matcher import auto_match_ports
from forge.generation.generators.structural_verilog import write_structural_verilog
from forge.generation.generators.structural_vhdl import write_structural_vhdl
from forge.generation.generators.block_design import write_bd_tcl

from forge.ir.build import assemble_project_ir
from forge.ir.project import project_to_conn_map

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

    project = assemble_project_ir(
        cfg, design_path,
        contracts=contracts, ip_info_data=ip_info,
        conn_map=conn_map, global_nets=global_nets, match_report=match_report,
    )
    projected_conn_map, projected_global_nets = project_to_conn_map(project)
    return cfg, contracts, ip_info, conn_map, global_nets, projected_conn_map, projected_global_nets


def _assert_byte_identical_generation(design_path: Path, modules_path: Path, tmp_path: Path) -> None:
    cfg, contracts, ip_info, conn_map, global_nets, projected_conn_map, projected_global_nets = _resolve(
        design_path, modules_path,
    )

    ip_root = tmp_path / "ips"
    direct_out = tmp_path / "direct" / "algo_top.v"
    ir_out = tmp_path / "ir_projected" / "algo_top.v"

    write_structural_verilog(
        cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
        ip_root=ip_root, out_path=direct_out, top_name="algo_top",
        system_yml=None, contracts=contracts,
    )
    write_structural_verilog(
        cfg=cfg, ip_info=ip_info, conn_map=projected_conn_map, global_nets=projected_global_nets,
        ip_root=ip_root, out_path=ir_out, top_name="algo_top",
        system_yml=None, contracts=contracts,
    )

    direct_bytes = direct_out.read_bytes()
    ir_bytes = ir_out.read_bytes()
    assert direct_bytes == ir_bytes, (
        "generation from the IR-projected conn_map/global_nets diverged "
        "byte-for-byte from the original direct conn_map/global_nets"
    )
    assert b"module algo_top" in direct_bytes  # sanity: real content, not empty files


def test_passthrough_demo_generation_is_byte_identical(tmp_path):
    _assert_byte_identical_generation(PASSTHROUGH_DESIGN, PASSTHROUGH_MODULES, tmp_path)


def test_trigger_demo_generation_is_byte_identical(tmp_path):
    """The bigger, more representative case: 7 modules, 45 connections,
    register-stage/signal-delay instances whose names (reg_stage_N/delay_N)
    are exactly what would break first if iteration order diverged."""
    _assert_byte_identical_generation(TRIGGER_DESIGN, TRIGGER_MODULES, tmp_path)


def _assert_byte_identical_vhdl(design_path: Path, modules_path: Path, tmp_path: Path) -> None:
    cfg, contracts, ip_info, conn_map, global_nets, projected_conn_map, projected_global_nets = _resolve(
        design_path, modules_path,
    )
    ip_root = tmp_path / "ips"
    direct_out = tmp_path / "direct" / "algo_top.vhd"
    ir_out = tmp_path / "ir_projected" / "algo_top.vhd"

    write_structural_vhdl(
        cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
        ip_root=ip_root, out_path=direct_out, top_name="algo_top", system_yml=None,
    )
    write_structural_vhdl(
        cfg=cfg, ip_info=ip_info, conn_map=projected_conn_map, global_nets=projected_global_nets,
        ip_root=ip_root, out_path=ir_out, top_name="algo_top", system_yml=None,
    )

    direct_bytes = direct_out.read_bytes()
    ir_bytes = ir_out.read_bytes()
    assert direct_bytes == ir_bytes, (
        "VHDL generation from the IR-projected conn_map/global_nets diverged "
        "byte-for-byte from the original direct conn_map/global_nets"
    )
    assert len(direct_bytes) > 0


def test_passthrough_demo_vhdl_generation_is_byte_identical(tmp_path):
    _assert_byte_identical_vhdl(PASSTHROUGH_DESIGN, PASSTHROUGH_MODULES, tmp_path)


def test_trigger_demo_vhdl_generation_is_byte_identical(tmp_path):
    _assert_byte_identical_vhdl(TRIGGER_DESIGN, TRIGGER_MODULES, tmp_path)


def _assert_byte_identical_bd(
    design_path: Path, modules_path: Path, tmp_path: Path, *, project_root: Path | None = None,
) -> None:
    cfg, contracts, ip_info, conn_map, global_nets, projected_conn_map, projected_global_nets = _resolve(
        design_path, modules_path,
    )
    ip_root = tmp_path / "ips"
    direct_out = tmp_path / "direct" / "block_design.tcl"
    ir_out = tmp_path / "ir_projected" / "block_design.tcl"
    direct_out.parent.mkdir(parents=True)
    ir_out.parent.mkdir(parents=True)

    write_bd_tcl(
        cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
        out_path=direct_out, bd_name="top_bd", src_root=design_path.parent,
        ip_root=ip_root, project_root=project_root,
    )
    write_bd_tcl(
        cfg=cfg, ip_info=ip_info, conn_map=projected_conn_map, global_nets=projected_global_nets,
        out_path=ir_out, bd_name="top_bd", src_root=design_path.parent,
        ip_root=ip_root, project_root=project_root,
    )

    direct_bytes = direct_out.read_bytes()
    ir_bytes = ir_out.read_bytes()
    assert direct_bytes == ir_bytes, (
        "Block Design TCL generation from the IR-projected conn_map/global_nets "
        "diverged byte-for-byte from the original direct conn_map/global_nets"
    )
    assert len(direct_bytes) > 0


def test_passthrough_demo_bd_generation_is_byte_identical(tmp_path):
    _assert_byte_identical_bd(PASSTHROUGH_DESIGN, PASSTHROUGH_MODULES, tmp_path)


def test_trigger_demo_bd_generation_is_byte_identical(tmp_path):
    """trigger_demo's design.yml declares register_stages (col->trig) and
    delay_cycles (tfan fanout) — write_bd_tcl instantiates real
    RegisterStage/signal_delay cells for these (project_root is required so
    it can find RegisterStage.v/signal_delay.v under
    plugins/trigger_demo/algo/rtl/, the same way
    generate_build_manifest's verilog-mode search already does)."""
    _assert_byte_identical_bd(
        TRIGGER_DESIGN, TRIGGER_MODULES, tmp_path, project_root=TRIGGER_DESIGN.parents[2],
    )


def test_write_bd_tcl_rejects_boundary_tagged_delay(tmp_path):
    """A boundary-tagged delay needs the protected slr_crossing_delay
    module (KEEP_HIERARCHY/DONT_TOUCH), not plain signal_delay —
    write_bd_tcl doesn't implement that yet and must reject the design
    outright rather than silently using the wrong module."""
    design_yml = tmp_path / "design.yml"
    modules_yml = tmp_path / "modules.yml"
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
        "    clock_primary: {raw_port: ap_clk, direction: input, width: 1}\n"
        "    reset_primary: {raw_port: ap_rst, direction: input, width: 1}\n"
        "    din: {raw_port: din, direction: input, width: 8}\n"
    )
    modules_yml.write_text(
        "registry_version: '1'\n"
        "modules:\n"
        "  - name: src\n    kind: rtl\n    top: src_top\n    src: [src.v]\n"
        "    interface_contract: interfaces/src.interface.yaml\n"
        "  - name: dst\n    kind: rtl\n    top: dst_top\n    src: [dst.v]\n"
        "    interface_contract: interfaces/dst.interface.yaml\n"
    )
    design_yml.write_text(
        "part: xcvu13p\nclock_period: 4.0\n"
        "modules:\n"
        "  - name: src\n    top: src_top\n    src: [src.v]\n"
        "  - name: dst\n    top: dst_top\n    src: [dst.v]\n"
        "connections:\n"
        "  - from: src\n    to: dst\n    port_map: [[dout, din]]\n"
        "    delay_cycles: 3\n    boundary: slr0_to_slr1\n"
    )

    cfg, contracts, ip_info, conn_map, global_nets, _p, _g = _resolve(design_yml, modules_yml)
    with pytest.raises(ValueError, match="boundary"):
        write_bd_tcl(
            cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
            out_path=tmp_path / "block_design.tcl", bd_name="top_bd",
            src_root=design_yml.parent, ip_root=tmp_path / "ips",
        )
