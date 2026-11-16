"""The latency model counts exactly the stages the generator puts on a connection.

A cdc: crossing combined with register_stages or delay_cycles used to be emitted
with the stages unconnected (the consumer reads the synchronizer, which reads the
source) while the latency graph added them up; it is refused instead (ATG050).
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

from forge.analysis.latency_static.graph import _edge_latency_from_connection
from forge.contracts.config import Connection, DesignConfig, Module, TopologyGroup
from forge.contracts.contract_loader import LoadedContract
from forge.contracts.matcher import auto_match_ports
from forge.generation.generators._port_resolution import build_connection_maps
from forge.generation.generators.structural_verilog import write_structural_verilog

_CDC_CYCLES = {"cdc_sync2ff": 2, "cdc_pulse_sync": 3}


def _contract(name, clock):
    return LoadedContract(path=Path(f"/fake/{name}.interface.yaml"), spec={"ip_interface": {
        "module_name": name, "ip_info_key": name, "source_type": "rtl",
        "roles": {"clock_primary": {"raw_port": clock, "direction": "input", "width": 1},
                  "reset_primary": {"raw_port": "rst", "direction": "input", "width": 1}}}})


def _rtl(tmp_path, conn: Connection) -> str:
    clock_b = "clk_b" if conn.cdc else "clk_a"
    cfg = DesignConfig(part="xcvu13p", clock_period=4.0,
                       modules=[Module(name="src", top="src_top", src=["x.v"]),
                                Module(name="dst", top="dst_top", src=["x.v"])],
                       connections=[conn])
    port = lambda n, d, w=8: {"name": n, "direction": d, "width": w, "type": "wire"}
    ip_info = {"src": {"ports": [port("clk_a", "IN", 1), port("rst", "IN", 1), port("dout", "OUT")]},
               "dst": {"ports": [port(clock_b, "IN", 1), port("rst", "IN", 1), port("din", "IN")]}}
    contracts = {"src": _contract("src", "clk_a"), "dst": _contract("dst", clock_b)}
    conn_map, global_nets, report = auto_match_ports(cfg, ip_info, contracts=contracts)
    out = tmp_path / "algo_top.v"
    write_structural_verilog(cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
                             ip_root=tmp_path, out_path=out, contracts=contracts, match_report=report)
    return out.read_text()


def _emitted_cycles(text: str) -> int:
    cycles = sum(int(n) for n in re.findall(r"\.(?:STAGES|DEPTH)\((\d+)\)", text))
    return cycles + sum(c * len(re.findall(rf"^\s*{m}\b", text, re.M)) for m, c in _CDC_CYCLES.items())


@pytest.mark.parametrize("stages, delay, cdc", [
    (2, 0, None), (0, 3, None), (2, 3, None),
    (0, 0, {"kind": "level_sync"}), (0, 0, {"kind": "pulse_sync", "min_spacing_cycles": 4}),
])
def test_predicted_latency_is_the_emitted_latency(tmp_path, stages, delay, cdc):
    conn = Connection(from_="src", to="dst", port_map=[("dout", "din")],
                      register_stages=stages, delay_cycles=delay, cdc=cdc)
    text = _rtl(tmp_path, conn)
    assert _edge_latency_from_connection(conn).cycles == _emitted_cycles(text)
    # every staged net feeds something: nothing is emitted and left unconnected
    for net in set(re.findall(r"\b((?:reg|delay|sync)_net_\w+)", text)):
        assert len(re.findall(rf"\b{net}\b", text)) >= 3, net   # declaration, driver, consumer


def _yaml(tmp_path, connection: str) -> Path:
    p = tmp_path / "design.yml"
    p.write_text(textwrap.dedent("""\
        schema_version: "1.0"
        part: xcvu13p
        clock_period: 4.0
        block_protocol: none
        modules:
          - {name: src, top: src_top, src: []}
          - {name: dst, top: dst_top, src: []}
        connections:
        """) + textwrap.indent(textwrap.dedent(connection), "  "))
    return p


@pytest.mark.parametrize("kind", ["level_sync", "2ff_sync", "pulse_sync", "mailbox_transfer", "async_fifo"])
@pytest.mark.parametrize("stages", ["register_stages: 1", "delay_cycles: 2", "delay_cycles: auto"])
def test_a_crossing_with_stages_is_refused_at_load(tmp_path, kind, stages):
    extra = {"pulse_sync": ", min_spacing_cycles: 4", "async_fifo": ", depth: 16"}.get(kind, "")
    path = _yaml(tmp_path, f"""\
        - from: src
          to: dst
          port_map: [[dout, din]]
          {stages}
          cdc: {{kind: {kind}{extra}}}
        """)
    with pytest.raises(ValueError, match="ATG050"):
        DesignConfig.load(path)


def test_a_waived_crossing_keeps_its_stages(tmp_path):
    path = _yaml(tmp_path, """\
        - from: src
          to: dst
          port_map: [[dout, din]]
          register_stages: 2
          cdc: {kind: waived, reason: quasi-static configuration word}
        """)
    assert DesignConfig.load(path).connections[0].register_stages == 2


def test_a_topology_group_delay_on_a_crossing_pair_is_refused():
    cfg = DesignConfig(part="xcvu13p", clock_period=4.0,
                       modules=[Module(name="src", top="s", src=["x.v"]), Module(name="dst", top="d", src=["x.v"])],
                       connections=[Connection(from_="src", to="dst", port_map=[("dout", "din")],
                                               cdc={"kind": "level_sync"})],
                       topology_groups=[TopologyGroup(name="g", family="f", from_="src", to="dst",
                                                      role_pairs=[], delay_cycles=3)])
    with pytest.raises(ValueError, match=r"ATG050.*src -> dst"):
        build_connection_maps(cfg)
