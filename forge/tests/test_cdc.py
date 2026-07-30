"""
Tests for forge.topgen.ip.cdc.verify_cdc (release-plan §3.2) — structural
clock/reset-domain-crossing detection against what auto_match_ports
actually wired.
"""

from __future__ import annotations

from pathlib import Path

from forge.topgen.config import Connection, DesignConfig, Module
from forge.topgen.ip.cdc import report_all_crossings, verify_cdc
from forge.topgen.ip.contract_loader import LoadedContract
from forge.topgen.ip.matcher import auto_match_ports


def _contract(module_name, ip_info_key, clock_port, reset_port="rst"):
    spec = {
        "ip_interface": {
            "module_name": module_name, "ip_info_key": ip_info_key, "source_type": "rtl",
            "roles": {
                "clock_primary": {"raw_port": clock_port, "direction": "input", "width": 1},
                "reset_primary": {"raw_port": reset_port, "direction": "input", "width": 1},
            },
        }
    }
    return LoadedContract(path=Path(f"/fake/{module_name}.interface.yaml"), spec=spec)


def _port(name, direction, width=8):
    return {"name": name, "direction": direction, "width": width, "type": "wire"}


def _two_module_setup(*, cdc=None, clock_a="clk_a", clock_b="clk_b", reset_a="rst", reset_b="rst"):
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
        "src": _contract("src", "src", clock_a, reset_a),
        "dst": _contract("dst", "dst", clock_b, reset_b),
    }
    conn_map, global_nets, report = auto_match_ports(cfg, ip_info, contracts=contracts)
    return cfg, contracts, report, conn_map, global_nets


class TestVerifyCdc:
    def test_undeclared_clock_crossing_is_an_error(self):
        cfg, contracts, report, conn_map, global_nets = _two_module_setup()
        issues = verify_cdc(cfg, contracts, report, conn_map, global_nets)
        assert any("clock-domain crossing" in i.message for i in issues)
        assert all(i.severity == "error" for i in issues)
        assert all(i.code == "ATG023" for i in issues)

    def test_declared_2ff_sync_suppresses_clock_crossing(self):
        cfg, contracts, report, conn_map, global_nets = _two_module_setup(cdc={"kind": "2ff_sync"})
        issues = verify_cdc(cfg, contracts, report, conn_map, global_nets)
        assert issues == []

    def test_declared_level_sync_suppresses_clock_crossing(self):
        cfg, contracts, report, conn_map, global_nets = _two_module_setup(cdc={"kind": "level_sync"})
        issues = verify_cdc(cfg, contracts, report, conn_map, global_nets)
        assert issues == []

    def test_declared_pulse_sync_suppresses_clock_crossing(self):
        cfg, contracts, report, conn_map, global_nets = _two_module_setup(
            cdc={"kind": "pulse_sync", "min_spacing_cycles": 4},
        )
        issues = verify_cdc(cfg, contracts, report, conn_map, global_nets)
        assert issues == []

    def test_declared_mailbox_transfer_suppresses_clock_crossing(self):
        cfg, contracts, report, conn_map, global_nets = _two_module_setup(
            cdc={"kind": "mailbox_transfer"},
        )
        issues = verify_cdc(cfg, contracts, report, conn_map, global_nets)
        assert issues == []

    def test_declared_async_fifo_suppresses_clock_crossing(self):
        cfg, contracts, report, conn_map, global_nets = _two_module_setup(
            cdc={"kind": "async_fifo", "depth": 4},
        )
        issues = verify_cdc(cfg, contracts, report, conn_map, global_nets)
        assert issues == []

    def test_same_clock_domain_is_never_flagged(self):
        cfg, contracts, report, conn_map, global_nets = _two_module_setup(clock_a="ap_clk", clock_b="ap_clk")
        issues = verify_cdc(cfg, contracts, report, conn_map, global_nets)
        assert issues == []

    def test_reset_only_crossing_detected_independently(self):
        cfg, contracts, report, conn_map, global_nets = _two_module_setup(
            clock_a="ap_clk", clock_b="ap_clk", reset_a="rst_a", reset_b="rst_b",
        )
        issues = verify_cdc(cfg, contracts, report, conn_map, global_nets)
        assert len(issues) == 1
        assert "reset-domain crossing" in issues[0].message
        assert issues[0].code == "ATG024"

    def test_clock_free_module_is_never_flagged(self):
        src = Module(name="src", top="src_top", src=["x.v"])
        dst = Module(name="dst", top="dst_top", src=["x.v"])
        cfg = DesignConfig(
            part="xcvu13p", clock_period=4.0, modules=[src, dst],
            connections=[Connection(from_="src", to="dst", port_map=[("dout", "din")])],
        )
        ip_info = {
            "src": {"ports": [_port("rst", "IN", 1), _port("dout", "OUT")]},
            "dst": {"ports": [_port("clk_b", "IN", 1), _port("rst", "IN", 1), _port("din", "IN")]},
        }
        src_spec = {
            "ip_interface": {
                "module_name": "src", "ip_info_key": "src", "source_type": "rtl",
                "clock_free": True,
                "roles": {"reset_primary": {"raw_port": "rst", "direction": "input", "width": 1}},
            }
        }
        contracts = {
            "src": LoadedContract(path=Path("/fake/src.interface.yaml"), spec=src_spec),
            "dst": _contract("dst", "dst", "clk_b"),
        }
        conn_map, global_nets, report = auto_match_ports(cfg, ip_info, contracts=contracts)
        issues = verify_cdc(cfg, contracts, report, conn_map, global_nets)
        assert issues == []


class TestReportAllCrossings:
    """release-plan §10.0C: report_all_crossings returns one entry per
    real crossing — passing (declared+approved) and failing (undeclared)
    alike, not just verify_cdc's failures."""

    def test_undeclared_crossing_is_a_failing_entry(self):
        cfg, contracts, report, conn_map, global_nets = _two_module_setup()
        crossings = report_all_crossings(cfg, contracts, report, conn_map, global_nets)
        assert len(crossings) == 1
        c = crossings[0]
        assert c["connection"] == "src->dst"
        assert c["passed"] is False
        assert c["kind"] is None
        assert "undeclared crossing" in c["message"]

    def test_declared_level_sync_is_a_passing_entry(self):
        cfg, contracts, report, conn_map, global_nets = _two_module_setup(cdc={"kind": "level_sync"})
        crossings = report_all_crossings(cfg, contracts, report, conn_map, global_nets)
        assert len(crossings) == 1
        c = crossings[0]
        assert c["passed"] is True
        assert c["kind"] == "level_sync"
        assert "approved" in c["message"]

    def test_same_clock_domain_with_no_cdc_is_skipped_entirely(self):
        """A same-domain pair has nothing to verify — report_all_crossings
        emits no entry for it at all (not a spurious 'passed' one)."""
        cfg, contracts, report, conn_map, global_nets = _two_module_setup(clock_a="ap_clk", clock_b="ap_clk")
        crossings = report_all_crossings(cfg, contracts, report, conn_map, global_nets)
        assert crossings == []

    def test_reset_only_crossing_is_a_failing_entry_with_reset_domains_set(self):
        cfg, contracts, report, conn_map, global_nets = _two_module_setup(
            clock_a="ap_clk", clock_b="ap_clk", reset_a="rst_a", reset_b="rst_b",
        )
        crossings = report_all_crossings(cfg, contracts, report, conn_map, global_nets)
        assert len(crossings) == 1
        c = crossings[0]
        assert c["passed"] is False
        assert c["source_reset_domain"] == "rst_a"
        assert c["destination_reset_domain"] == "rst_b"
        assert c["source_clock_domain"] == c["destination_clock_domain"] == "ap_clk"
