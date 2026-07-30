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

    def test_2ff_sync_alias_normalizes_to_level_sync(self, tmp_path):
        """The '2ff_sync' spelling (constructed directly, bypassing
        DesignConfig.load's own normalization) must still emit the real
        cdc_sync2ff instance, identically to 'level_sync'."""
        cfg, ip_info, contracts, conn_map, global_nets, report = _two_domain_setup(
            cdc={"kind": "level_sync"},
        )
        out = tmp_path / "algo_top.v"
        write_structural_verilog(
            cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
            ip_root=tmp_path, out_path=out, contracts=contracts, match_report=report,
        )
        assert "cdc_sync2ff #(" in out.read_text()

    def test_pulse_sync_emits_toggle_synchronizer(self, tmp_path):
        cfg, ip_info, contracts, conn_map, global_nets, report = _two_domain_setup(
            cdc={"kind": "pulse_sync", "min_spacing_cycles": 8},
        )
        out = tmp_path / "algo_top.v"
        write_structural_verilog(
            cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
            ip_root=tmp_path, out_path=out, contracts=contracts, match_report=report,
        )
        text = out.read_text()
        assert "cdc_pulse_sync" in text
        assert ".src_clk(clk_a)," in text
        assert ".dst_clk(clk_b)," in text
        assert ".pulse_in(net_src_dout)," in text
        assert ".pulse_out(sync_net_src_dst_dout)" in text

    def test_mailbox_transfer_emits_handshake_synchronizer(self, tmp_path):
        cfg, ip_info, contracts, conn_map, global_nets, report = _two_domain_setup(
            cdc={"kind": "mailbox_transfer"},
        )
        out = tmp_path / "algo_top.v"
        write_structural_verilog(
            cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
            ip_root=tmp_path, out_path=out, contracts=contracts, match_report=report,
        )
        text = out.read_text()
        assert "cdc_mailbox #(" in text
        assert ".WIDTH(8)" in text
        assert ".din(net_src_dout)," in text
        assert ".dout(sync_net_src_dst_dout)," in text

    def test_async_fifo_emits_real_fifo_rtl(self, tmp_path):
        """release-plan §10.0B closes the previously-documented gap:
        async_fifo now emits a real dual-clock FIFO instance, not a
        placeholder comment."""
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
        assert "not implemented yet" not in text
        assert "cdc_async_fifo #(" in text
        assert ".WIDTH(8)," in text
        assert ".DEPTH(8)" in text
        assert ".wr_clk(clk_a)," in text
        assert ".rd_clk(clk_b)," in text
        assert ".dout(sync_net_src_dst_dout)," in text
        # Destination instance consumes the FIFO's dout, not the raw net.
        assert ".din(sync_net_src_dst_dout)" in text

    def test_async_fifo_emits_occupancy_and_high_water_telemetry(self, tmp_path):
        """release-plan §10.0C: real occupancy/high_water instrumentation
        signals (Decision B), not just full/empty/overflow/underflow."""
        cfg, ip_info, contracts, conn_map, global_nets, report = _two_domain_setup(
            cdc={"kind": "async_fifo", "depth": 8},
        )
        out = tmp_path / "algo_top.v"
        write_structural_verilog(
            cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
            ip_root=tmp_path, out_path=out, contracts=contracts, match_report=report,
        )
        text = out.read_text()
        assert ".occupancy(sync_net_src_dst_dout_occupancy)," in text
        assert ".high_water(sync_net_src_dst_dout_high_water)," in text
        assert "wire [3:0] sync_net_src_dst_dout_occupancy, sync_net_src_dst_dout_high_water;" in text

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


class TestResetSyncEmission:
    def test_reset_domains_sync_emits_reset_synchronizer(self, tmp_path):
        """reset_domains.<name>.sync: reset_sync (release-plan §10.0B) emits
        a real cdc_reset_sync instance clocked by the domain's own resolved
        clock — a reset crossing is a domain property, not a Connection.cdc
        declaration, so this is driven by cfg.reset_domains directly."""
        mod = Module(name="mod", top="mod_top", src=["x.v"])
        cfg = DesignConfig(
            part="xcvu13p", clock_period=4.0, modules=[mod],
            connections=[],
        )
        cfg.reset_domains = {
            "rst_slow": {"derived_from": "ap_rst", "ratio": None, "sync": "reset_sync"},
        }
        ip_info = {
            "mod": {"ports": [_port("clk_b", "IN", 1), _port("rst_slow", "IN", 1)]},
        }
        contracts = {"mod": _contract("mod", "clk_b", "rst_slow")}
        conn_map, global_nets, report = auto_match_ports(cfg, ip_info, contracts=contracts)

        out = tmp_path / "algo_top.v"
        write_structural_verilog(
            cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
            ip_root=tmp_path, out_path=out, contracts=contracts, match_report=report,
        )
        text = out.read_text()
        assert "cdc_reset_sync" in text
        assert ".dst_clk(clk_b)," in text
        assert ".async_rst_in(ap_rst)," in text

    def test_no_reset_domains_sync_emits_nothing(self, tmp_path):
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
        write_structural_verilog(
            cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
            ip_root=tmp_path, out_path=out,
        )
        assert "cdc_reset_sync" not in out.read_text()


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


@pytest.mark.parametrize("kind,cdc,filename", [
    ("pulse_sync", {"kind": "pulse_sync", "min_spacing_cycles": 4}, "cdc_pulse_sync.v"),
    ("mailbox_transfer", {"kind": "mailbox_transfer"}, "cdc_mailbox.v"),
    ("async_fifo", {"kind": "async_fifo", "depth": 8}, "cdc_async_fifo.v"),
])
def test_build_manifest_includes_new_cdc_primitives_when_declared(tmp_path, kind, cdc, filename):
    """release-plan §10.0B: the 3 new CDC kinds' RTL files must be found
    and included the same way cdc_sync2ff.v already is, and must NOT be
    included when nothing declares that kind."""
    from forge.core.cli.groups.topgen import generate_build_manifest

    src = Module(name="src", top="src_top", src=["src.v"])
    dst = Module(name="dst", top="dst_top", src=["dst.v"])
    (tmp_path / "src.v").write_text("module src_top(); endmodule\n")
    (tmp_path / "dst.v").write_text("module dst_top(); endmodule\n")
    (tmp_path / filename).write_text(f"module {filename[:-2]}(); endmodule\n")
    src.abs_src = [tmp_path / "src.v"]
    dst.abs_src = [tmp_path / "dst.v"]

    import json

    manifest_path = tmp_path / "build_manifest.json"

    cfg_with_cdc = DesignConfig(
        part="xcvu13p", clock_period=4.0, modules=[src, dst],
        connections=[Connection(from_="src", to="dst", cdc=cdc)],
    )
    generate_build_manifest(
        cfg_with_cdc, ip_info={}, ip_root=tmp_path,
        algo_top=tmp_path / "algo_top.v", design_file=tmp_path / "design.yml",
        manifest_output=manifest_path, project_root=tmp_path,
    )
    manifest = json.loads(manifest_path.read_text())
    assert any(f.endswith(filename) for f in manifest["verilog_files"])

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
    assert not any(f.endswith(filename) for f in manifest2["verilog_files"])


def test_build_manifest_includes_cdc_reset_sync_when_declared(tmp_path):
    """release-plan §10.0B: cdc_reset_sync.v is needed based on
    reset_domains.*.sync, not a Connection.cdc declaration."""
    from forge.core.cli.groups.topgen import generate_build_manifest

    mod = Module(name="mod", top="mod_top", src=["mod.v"])
    (tmp_path / "mod.v").write_text("module mod_top(); endmodule\n")
    (tmp_path / "cdc_reset_sync.v").write_text("module cdc_reset_sync(); endmodule\n")
    mod.abs_src = [tmp_path / "mod.v"]

    import json

    manifest_path = tmp_path / "build_manifest.json"

    cfg_with_sync = DesignConfig(part="xcvu13p", clock_period=4.0, modules=[mod], connections=[])
    cfg_with_sync.reset_domains = {
        "rst_slow": {"derived_from": "ap_rst", "ratio": None, "sync": "reset_sync"},
    }
    generate_build_manifest(
        cfg_with_sync, ip_info={}, ip_root=tmp_path,
        algo_top=tmp_path / "algo_top.v", design_file=tmp_path / "design.yml",
        manifest_output=manifest_path, project_root=tmp_path,
    )
    manifest = json.loads(manifest_path.read_text())
    assert any(f.endswith("cdc_reset_sync.v") for f in manifest["verilog_files"])

    cfg_without_sync = DesignConfig(part="xcvu13p", clock_period=4.0, modules=[mod], connections=[])
    generate_build_manifest(
        cfg_without_sync, ip_info={}, ip_root=tmp_path,
        algo_top=tmp_path / "algo_top.v", design_file=tmp_path / "design.yml",
        manifest_output=manifest_path, project_root=tmp_path,
    )
    manifest2 = json.loads(manifest_path.read_text())
    assert not any(f.endswith("cdc_reset_sync.v") for f in manifest2["verilog_files"])


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
