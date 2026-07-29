"""
Tests for CDC synchronizer RTL emission (release-plan §3.2, Phase 3
slice 3) in forge.topgen.generators.structural_verilog.write_structural_verilog.

Structural crossing *detection* (forge.topgen.ip.cdc.verify_cdc) is tested
in test_cdc.py; these tests cover the actual generated Verilog text for a
declared `cdc:` adapter.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.topgen.config import Connection, DesignConfig, Module
from forge.topgen.ip.contract_loader import LoadedContract
from forge.topgen.ip.matcher import auto_match_ports
from forge.topgen.generators.structural_verilog import write_structural_verilog

REPO_ROOT = Path(__file__).resolve().parents[2]
TRIGGER_DESIGN = REPO_ROOT / "plugins/trigger_demo/forge/designs/design.yml"
TRIGGER_MODULES = REPO_ROOT / "plugins/trigger_demo/forge/modules.yml"


def _contract(name, clock_port, reset_port="rst"):
    spec = {
        "ip_interface": {
            "module_name": name, "ip_info_key": name, "source_type": "rtl",
            "roles": {
                "clock_primary": {"raw_port": clock_port, "direction": "input", "width": 1},
                "reset_primary": {"raw_port": reset_port, "direction": "input", "width": 1},
            },
        }
    }
    return LoadedContract(path=Path(f"/fake/{name}.interface.yaml"), spec=spec)


def _port(name, direction, width=8):
    return {"name": name, "direction": direction, "width": width, "type": "wire"}


def _two_domain_setup(*, cdc, clock_a="clk_a", clock_b="clk_b", reset_a="rst", reset_b="rst"):
    src = Module(name="src", top="src_top", src=["x.v"])
    dst = Module(name="dst", top="dst_top", src=["x.v"])
    cfg = DesignConfig(
        part="xcvu13p", clock_period=4.0, modules=[src, dst],
        connections=[Connection(from_="src", to="dst", port_map=[("dout", "din")], cdc=cdc)],
    )
    ip_info = {
        "src": {"ports": [_port(clock_a, "IN", 1), _port(reset_a, "IN", 1), _port("dout", "OUT")]},
        "dst": {"ports": [_port(clock_b, "IN", 1), _port(reset_b, "IN", 1), _port("din", "IN")]},
    }
    contracts = {
        "src": _contract("src", clock_a, reset_a),
        "dst": _contract("dst", clock_b, reset_b),
    }
    conn_map, global_nets, report = auto_match_ports(cfg, ip_info, contracts=contracts)
    return cfg, ip_info, contracts, conn_map, global_nets, report


class TestCdcSync2ffEmission:
    def test_emits_synchronizer_wired_to_destination_domain(self, tmp_path):
        cfg, ip_info, contracts, conn_map, global_nets, report = _two_domain_setup(
            cdc={"kind": "2ff_sync"},
        )
        out = tmp_path / "algo_top.v"
        write_structural_verilog(
            cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
            ip_root=tmp_path, out_path=out, contracts=contracts, match_report=report,
        )
        text = out.read_text()
        assert "cdc_sync2ff #(" in text
        assert ".dst_clk(clk_b)," in text  # destination's OWN clock, not ap_clk
        assert ".din(net_src_dout)," in text
        # Destination instance consumes the synchronized net, not the raw one.
        assert ".din(sync_net_src_dst_dout)" in text

    def test_standard_reset_name_variant_maps_to_ap_rst_not_raw_name(self, tmp_path):
        """Regression: resolve_domain_nets' domain identity is the raw port
        name ("rst"), but the generator's own clock/reset auto-map aliases
        every standard variant onto the literal ap_rst top-level port —
        the synchronizer must reference the net that actually exists in
        the generated file, not the raw domain string."""
        cfg, ip_info, contracts, conn_map, global_nets, report = _two_domain_setup(
            cdc={"kind": "2ff_sync"}, reset_a="rst", reset_b="rst",
        )
        out = tmp_path / "algo_top.v"
        write_structural_verilog(
            cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
            ip_root=tmp_path, out_path=out, contracts=contracts, match_report=report,
        )
        text = out.read_text()
        assert ".dst_rst(ap_rst)" in text
        assert "input ap_rst" in text  # the referenced net must actually be declared

    def test_non_standard_clock_name_stays_its_own_net(self, tmp_path):
        cfg, ip_info, contracts, conn_map, global_nets, report = _two_domain_setup(
            cdc={"kind": "2ff_sync"}, clock_a="clk_a", clock_b="clk_b",
        )
        out = tmp_path / "algo_top.v"
        write_structural_verilog(
            cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
            ip_root=tmp_path, out_path=out, contracts=contracts, match_report=report,
        )
        text = out.read_text()
        assert "input clk_b" in text
        assert ".dst_clk(clk_b)" in text

    def test_async_fifo_wires_directly_with_a_visible_note(self, tmp_path):
        cfg, ip_info, contracts, conn_map, global_nets, report = _two_domain_setup(
            cdc={"kind": "async_fifo", "depth": 8},
        )
        out = tmp_path / "algo_top.v"
        write_structural_verilog(
            cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
            ip_root=tmp_path, out_path=out, contracts=contracts, match_report=report,
        )
        text = out.read_text()
        assert "cdc_sync2ff" not in text
        assert "async_fifo" in text  # visible NOTE comment
        assert "not implemented yet" in text
        assert ".din(net_src_dout)" in text  # wired directly, no intermediate net

    def test_cdc_declared_without_match_report_raises(self, tmp_path):
        cfg, ip_info, contracts, conn_map, global_nets, report = _two_domain_setup(
            cdc={"kind": "2ff_sync"},
        )
        out = tmp_path / "algo_top.v"
        with pytest.raises(ValueError, match="match_report"):
            write_structural_verilog(
                cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
                ip_root=tmp_path, out_path=out, contracts=contracts,
            )

    def test_no_cdc_declared_needs_no_match_report_backward_compatible(self, tmp_path):
        src = Module(name="src", top="src_top", src=["x.v"])
        dst = Module(name="dst", top="dst_top", src=["x.v"])
        cfg = DesignConfig(
            part="xcvu13p", clock_period=4.0, modules=[src, dst],
            connections=[Connection(from_="src", to="dst", port_map=[("dout", "din")])],
        )
        ip_info = {
            "src": {"ports": [_port("ap_clk", "IN", 1), _port("ap_rst", "IN", 1), _port("dout", "OUT")]},
            "dst": {"ports": [_port("ap_clk", "IN", 1), _port("ap_rst", "IN", 1), _port("din", "IN")]},
        }
        conn_map = {("src", "dst"): [("dout", "din")]}
        global_nets = {
            "ap_clk": [("src", "ap_clk"), ("dst", "ap_clk")],
            "ap_rst": [("src", "ap_rst"), ("dst", "ap_rst")],
        }
        out = tmp_path / "algo_top.v"
        report = write_structural_verilog(
            cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
            ip_root=tmp_path, out_path=out,
        )
        assert out.exists()
        assert "cdc_sync2ff" not in out.read_text()


def test_build_manifest_includes_cdc_sync2ff_when_declared(tmp_path):
    """generate_build_manifest's framework-support-RTL search (mirrors the
    existing RegisterStage.v/signal_delay.v/slr_crossing_delay.v pattern)
    must find and include cdc_sync2ff.v when a connection declares
    cdc: {kind: 2ff_sync} — and must NOT include it when nothing does."""
    from forge.core.cli.groups.topgen import generate_build_manifest

    src = Module(name="src", top="src_top", src=["src.v"])
    dst = Module(name="dst", top="dst_top", src=["dst.v"])
    (tmp_path / "src.v").write_text("module src_top(); endmodule\n")
    (tmp_path / "dst.v").write_text("module dst_top(); endmodule\n")
    (tmp_path / "cdc_sync2ff.v").write_text("module cdc_sync2ff(); endmodule\n")
    src.abs_src = [tmp_path / "src.v"]
    dst.abs_src = [tmp_path / "dst.v"]

    import json

    manifest_path = tmp_path / "build_manifest.json"

    cfg_with_cdc = DesignConfig(
        part="xcvu13p", clock_period=4.0, modules=[src, dst],
        connections=[Connection(from_="src", to="dst", cdc={"kind": "2ff_sync"})],
    )
    generate_build_manifest(
        cfg_with_cdc, ip_info={}, ip_root=tmp_path,
        algo_top=tmp_path / "algo_top.v", design_file=tmp_path / "design.yml",
        manifest_output=manifest_path, project_root=tmp_path,
    )
    manifest = json.loads(manifest_path.read_text())
    assert any(f.endswith("cdc_sync2ff.v") for f in manifest["verilog_files"])

    cfg_without_cdc = DesignConfig(
        part="xcvu13p", clock_period=4.0, modules=[src, dst],
        connections=[Connection(from_="src", to="dst")],
    )
    generate_build_manifest(
        cfg_without_cdc, ip_info={}, ip_root=tmp_path,
        algo_top=tmp_path / "algo_top.v", design_file=tmp_path / "design.yml",
        manifest_output=manifest_path, project_root=tmp_path,
    )
    manifest2 = json.loads(manifest_path.read_text())
    assert not any(f.endswith("cdc_sync2ff.v") for f in manifest2["verilog_files"])


def test_trigger_demo_generation_is_unaffected_by_cdc_support(tmp_path):
    """trigger_demo declares no `cdc:` connections — real-design regression
    guard: adding CDC support must not change its generated output."""
    from forge.topgen.config import DesignConfig as _DC
    from forge.topgen.ip.contract_loader import load_contracts_for_design, synthesize_ip_info

    cfg = _DC.load_relaxed(TRIGGER_DESIGN)
    contracts = load_contracts_for_design(TRIGGER_MODULES, TRIGGER_DESIGN.parent)
    mapped = {}
    for m in cfg.modules:
        c = contracts.get(m.name) or (m.ip_info_key and contracts.get(m.ip_info_key))
        if c:
            mapped[m.name] = c
    ip_info = synthesize_ip_info(mapped)
    conn_map, global_nets, report = auto_match_ports(cfg, ip_info, contracts=contracts or None)

    out = tmp_path / "algo_top.v"
    write_structural_verilog(
        cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
        ip_root=tmp_path / "ips", out_path=out, contracts=contracts, match_report=report,
    )
    assert "cdc_sync2ff" not in out.read_text()
