"""An external din/dout port of a parameterized module takes the instance WIDTH parameter.

slr_crossing_delay (and signal_delay) declare din/dout as 1 bit in their interface contract and are
instantiated with WIDTH=67 for a CSP word. The internal net of the instance already had the WIDTH
width; the external top-level port was declared from the contract (1 bit), so
`assign top_dout = net_dout` kept bit 0 only and the other 66 bits were dropped without a message.
"""
from __future__ import annotations

from types import SimpleNamespace

from forge.generation.generators import _port_resolution as pr


def _mod(name, width):
    n = 2 if width == 67 else 1
    return SimpleNamespace(
        name=name, instances=n, parameters={"DEPTH": 1, "WIDTH": width},
        external_in_ports=[], external_out_ports=["dout"], debug=False, ip_info_key=None,
        instance_id=lambda i: f"{name}_{i}" if n > 1 else name,
    )


CONTRACT_PORTS = [
    {"name": "clk", "dir": "input", "width": 1},
    {"name": "rst", "dir": "input", "width": 1},
    {"name": "din", "dir": "input", "width": 1},
    {"name": "dout", "dir": "output", "width": 1},
]


def test_external_dout_uses_width_parameter(tmp_path, monkeypatch):
    cfg = SimpleNamespace(
        modules=[_mod("dly", 67), _mod("orbit_dly", 1)], connect_clock=True, connect_reset=True,
        control_signals={},
    )
    monkeypatch.setattr(pr, "_fetch_module_ports", lambda mod, info, root: [dict(p) for p in CONTRACT_PORTS])
    monkeypatch.setattr(pr, "_parse_system_aliases", lambda cfg, system_yml: ({}, {}))
    res = pr.resolve_top_ports(cfg, {"dly": {}, "orbit_dly": {}}, tmp_path, {})
    width = {p["name"]: p["width"] for p in res.top_ports if p["origin"] == "external"}
    assert width["dly_0_dout"] == 67
    assert width["dly_1_dout"] == 67
    assert width["orbit_dly_dout"] == 1  # WIDTH 1 stays 1 bit
