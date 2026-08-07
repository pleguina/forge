"""
Tests for forge.contracts.domains.resolve_domain_nets — the shared
clock/reset domain resolution algorithm used by both the canonical IR
(forge/ir/build.py) and the CDC checker (forge/topgen/ip/cdc.py).
"""

from __future__ import annotations

from pathlib import Path

from forge.contracts.config import DesignConfig, Module
from forge.contracts.contract_loader import LoadedContract
from forge.contracts.domains import resolve_domain_nets
from forge.contracts.matcher import auto_match_ports


def _contract(module_name, ip_info_key, *, clock_free=False, reset_free=False, clock_port=None, reset_port=None):
    roles = {}
    if not clock_free:
        roles["clock_primary"] = {"raw_port": clock_port or "ap_clk", "direction": "input", "width": 1}
    if not reset_free:
        roles["reset_primary"] = {"raw_port": reset_port or "ap_rst", "direction": "input", "width": 1}
    spec = {
        "ip_interface": {
            "module_name": module_name, "ip_info_key": ip_info_key, "source_type": "rtl",
            "clock_free": clock_free, "reset_free": reset_free, "roles": roles,
        }
    }
    return LoadedContract(path=Path(f"/fake/{module_name}.interface.yaml"), spec=spec)


def _port(name, direction, width=1):
    return {"name": name, "direction": direction, "width": width, "type": "wire"}


def _mod_of_instance(cfg):
    out = {}
    for mod in cfg.modules:
        for idx in range(max(1, mod.instances)):
            out[mod.name if mod.instances == 1 else f"{mod.name}_{idx}"] = mod.name
    return out


class TestContractDriven:
    def test_resolves_declared_clock_and_reset_port(self):
        mod = Module(name="m1", top="m1_top", src=["x.v"])
        cfg = DesignConfig(part="xcvu13p", clock_period=4.0, modules=[mod], connections=[])
        ip_info = {"m1": {"ports": [_port("ap_clk", "IN"), _port("ap_rst", "IN")]}}
        contracts = {"m1": _contract("m1", "m1")}
        conn_map, global_nets, report = auto_match_ports(cfg, ip_info, contracts=contracts)

        clock_of, reset_of, unresolved = resolve_domain_nets(
            cfg, contracts, report, global_nets, _mod_of_instance(cfg),
        )
        assert clock_of["m1"] == "ap_clk"
        assert reset_of["m1"] == "ap_rst"
        assert unresolved == []

    def test_clock_free_module_resolves_to_none(self):
        mod = Module(name="m1", top="m1_top", src=["x.v"])
        cfg = DesignConfig(part="xcvu13p", clock_period=4.0, modules=[mod], connections=[])
        ip_info = {"m1": {"ports": [_port("ap_rst", "IN")]}}
        contracts = {"m1": _contract("m1", "m1", clock_free=True)}
        conn_map, global_nets, report = auto_match_ports(cfg, ip_info, contracts=contracts)

        clock_of, reset_of, unresolved = resolve_domain_nets(
            cfg, contracts, report, global_nets, _mod_of_instance(cfg),
        )
        assert clock_of["m1"] is None
        assert reset_of["m1"] == "ap_rst"
        assert unresolved == []

    def test_reset_free_module_resolves_to_none(self):
        mod = Module(name="m1", top="m1_top", src=["x.v"])
        cfg = DesignConfig(part="xcvu13p", clock_period=4.0, modules=[mod], connections=[])
        ip_info = {"m1": {"ports": [_port("ap_clk", "IN")]}}
        contracts = {"m1": _contract("m1", "m1", reset_free=True)}
        conn_map, global_nets, report = auto_match_ports(cfg, ip_info, contracts=contracts)

        clock_of, reset_of, unresolved = resolve_domain_nets(
            cfg, contracts, report, global_nets, _mod_of_instance(cfg),
        )
        assert clock_of["m1"] == "ap_clk"
        assert reset_of["m1"] is None


class TestNoContractHeuristic:
    def test_resolves_via_heuristic_name(self):
        mod = Module(name="m1", top="m1_top", src=["x.v"])
        cfg = DesignConfig(part="xcvu13p", clock_period=4.0, modules=[mod], connections=[])
        ip_info = {"m1": {"ports": [_port("clk", "IN"), _port("rst_n", "IN")]}}
        conn_map, global_nets, report = auto_match_ports(cfg, ip_info)

        clock_of, reset_of, unresolved = resolve_domain_nets(
            cfg, {}, report, global_nets, _mod_of_instance(cfg),
        )
        assert clock_of["m1"] == "clk"
        assert reset_of["m1"] == "rst_n"
        assert unresolved == []

    def test_unresolvable_module_is_reported(self):
        mod = Module(name="weird", top="weird_top", src=["x.v"])
        cfg = DesignConfig(part="xcvu13p", clock_period=4.0, modules=[mod], connections=[])
        ip_info = {"weird": {"ports": [_port("totally_custom", "IN")]}}
        conn_map, global_nets, report = auto_match_ports(cfg, ip_info)

        clock_of, reset_of, unresolved = resolve_domain_nets(
            cfg, {}, report, global_nets, _mod_of_instance(cfg),
        )
        assert clock_of["weird"] is None
        assert reset_of["weird"] is None
        assert ("weird", "clock") in unresolved
        assert ("weird", "reset") in unresolved

    def test_connect_clock_false_suppresses_unresolved_report(self):
        mod = Module(name="weird", top="weird_top", src=["x.v"])
        cfg = DesignConfig(
            part="xcvu13p", clock_period=4.0, modules=[mod], connections=[],
            connect_clock=False, connect_reset=False,
        )
        ip_info = {"weird": {"ports": [_port("totally_custom", "IN")]}}
        conn_map, global_nets, report = auto_match_ports(cfg, ip_info)

        clock_of, reset_of, unresolved = resolve_domain_nets(
            cfg, {}, report, global_nets, _mod_of_instance(cfg),
        )
        assert unresolved == []
