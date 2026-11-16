"""``forge dev verify generate`` checks a flow's port_map.yaml against the RTL
it tests: a hand-written map that disagrees stops generation, a map generate
derived itself is derived again."""

from __future__ import annotations

import pytest
import yaml

from forge.verification.prepare import generate_flows
from forge.verification.rtl_introspection import port_map_drift

# the port style Vitis HLS writes
RTL = """module counter (
        ap_clk,
        ap_rst,
        din,
        dout
);

input   ap_clk;
input   ap_rst;
input  [7:0] din;
output  [7:0] dout;

endmodule
"""

PORTS = [
    {"name": "ap_clk", "direction": "input", "width": 1},
    {"name": "ap_rst", "direction": "input", "width": 1},
    {"name": "din", "direction": "input", "width": 8},
    {"name": "dout", "direction": "output", "width": 8},
]


def _consumer(tmp_path, port_map=None):
    rtl_dir = tmp_path / "build" / "counter"
    rtl_dir.mkdir(parents=True)
    (rtl_dir / "counter.v").write_text(RTL)
    verify = tmp_path / "verify"
    verify.mkdir()
    (verify / "design.verification.yml").write_text(
        "schema_version: '1.0'\n"
        "plugin: drift\n"
        "datasets:\n"
        "  golden: {xml: golden.xml}\n"
        "defaults: {clk_period_ns: 4.0, reset_cycles: 4, idle_cycles_after_reset: 0, post_stimulus_drain_cycles: 4}\n"
        "flows:\n"
        "  - name: counter_xsim\n"
        "    kind: single_module_rtl\n"
        "    backend: xsim\n"
        "    top_module: counter\n"
        "    tb_module: tb_counter\n"
        "    dut_rtl_source: build/counter\n"
        "    dataset: golden\n"
    )
    flow_dir = verify / "counter_xsim"
    if port_map is not None:
        flow_dir.mkdir()
        (flow_dir / "port_map.yaml").write_text(yaml.safe_dump(port_map, sort_keys=False))
    return verify / "design.verification.yml", flow_dir


def _generate(design, tmp_path):
    return generate_flows(design, consumer_root=tmp_path)


def test_a_matching_map_has_no_drift(tmp_path):
    design, flow_dir = _consumer(tmp_path, {"ports": PORTS})
    assert port_map_drift(flow_dir / "port_map.yaml", tmp_path / "build/counter/counter.v") == []


def test_a_map_without_the_clock_stops_generation(tmp_path, capsys):
    design, _ = _consumer(tmp_path, {"ports": PORTS[1:]})
    assert _generate(design, tmp_path) == 1
    err = capsys.readouterr().err
    assert "ap_clk is in the RTL but not in the map" in err
    assert not (tmp_path / "verify/counter_xsim/tb_counter.sv").exists()


@pytest.mark.parametrize("edit, message", [
    (lambda ports: ports + [{"name": "stale", "direction": "input", "width": 1}], "stale is in the map but not in the RTL"),
    (lambda ports: [dict(p, width=16) if p["name"] == "din" else p for p in ports], "din is 16 bits in the map, 8 in the RTL"),
    (lambda ports: [dict(p, direction="input") if p["name"] == "dout" else p for p in ports], "dout is input in the map, output in the RTL"),
])
def test_each_disagreement_is_named(tmp_path, edit, message):
    _, flow_dir = _consumer(tmp_path, {"ports": edit(list(PORTS))})
    assert message in port_map_drift(flow_dir / "port_map.yaml", tmp_path / "build/counter/counter.v")


def test_a_derived_map_is_derived_again(tmp_path, capsys):
    design, flow_dir = _consumer(
        tmp_path, {"derived_from": "build/counter/counter.v", "ports": PORTS[1:]},
    )
    assert _generate(design, tmp_path) == 0
    assert "deriving it again" in capsys.readouterr().out
    written = yaml.safe_load((flow_dir / "port_map.yaml").read_text())
    assert [p["name"] for p in written["ports"]] == [p["name"] for p in PORTS]
    assert written["derived_from"] == "build/counter/counter.v"
    assert (flow_dir / "tb_counter.sv").exists()


def test_without_a_map_generate_derives_one_and_records_its_source(tmp_path):
    design, flow_dir = _consumer(tmp_path)
    assert _generate(design, tmp_path) == 0
    written = yaml.safe_load((flow_dir / "port_map.yaml").read_text())
    assert written["derived_from"] == "build/counter/counter.v"
