"""Delay, boundary and CDC maps are built once, for every output mode.

Block design mode once ignored the delay/boundary of ``topology_groups`` (verilog mode applied
them), so the block design silently lacked every crossing and delay a group declared. Both
generators now call ``build_connection_maps``; these tests pin what it returns and that the
generators do not build the maps themselves again.
"""
from __future__ import annotations

import inspect
import re

from forge.contracts.config import DesignConfig
from forge.generation.generators import block_design, structural_verilog
from forge.generation.generators._port_resolution import build_connection_maps

_YAML = """\
schema_version: "1.0"
part: xcvu13p
clock_period: 4.0
block_protocol: none

modules:
  - name: src
    top: src_top
    src: []
    instances: 2
  - name: dst
    top: dst_top
    src: []
    instances: 3
  - name: sink
    top: sink_top
    src: []
    instance_name: snk
    instance_offset: 4

connections:
  - from: src
    to: sink
    port_map: [[o, i]]
    register_stages: 2
  - from: dst
    to: sink
    port_map: [[o, i]]
    delay_cycles: 5
    boundary: conn_tag

topology_groups:
  - name: tg
    family: wk
    from: src
    to: dst
    role_pairs: [[a_out, b_in]]
    delay_cycles: 7
    boundary: group_tag
  - name: tg_plain_delay
    family: wk
    from: dst
    to: src
    role_pairs: [[c_out, d_in]]
    delay_cycles: 3
"""


def _cfg(tmp_path):
    path = tmp_path / "design.yml"
    path.write_text(_YAML)
    return DesignConfig.load(path)


def test_topology_group_delay_and_boundary_reach_every_instance_pair(tmp_path):
    _, delays, boundaries, _ = build_connection_maps(_cfg(tmp_path))
    pairs = {(f"src_{i}", f"dst_{j}") for i in range(2) for j in range(3)}
    assert {k for k, v in boundaries.items() if v == "group_tag"} == pairs
    assert all(delays[k] == 7 for k in pairs)


def test_topology_group_without_boundary_is_a_plain_delay(tmp_path):
    _, delays, boundaries, _ = build_connection_maps(_cfg(tmp_path))
    back = {(f"dst_{j}", f"src_{i}") for j in range(3) for i in range(2)}
    assert all(delays[k] == 3 for k in back)
    assert not back & set(boundaries)


def test_connections_use_the_resolved_instance_ids(tmp_path):
    stages, delays, boundaries, _ = build_connection_maps(_cfg(tmp_path))
    assert stages == {("src_0", "snk_4"): 2, ("src_1", "snk_4"): 2}
    assert delays[("dst_2", "snk_4")] == 5 and boundaries[("dst_2", "snk_4")] == "conn_tag"


def test_generators_do_not_build_the_maps_themselves():
    """A second construction site is how the two modes drifted apart."""
    for mod in (block_design, structural_verilog):
        src = inspect.getsource(mod)
        assert "build_connection_maps(cfg)" in src, mod.__name__
        assert not re.search(r"^\s*(delay_cycles_map|boundary_map|reg_stages_map)\[.*\]\s*=", src, re.M), mod.__name__
