"""Coverage for forge/verify/port_parser.py (previously 0%).

This module is a documented backward-compatibility shim that delegates
entirely to forge.verify.rtl_introspection — kept for external plugin
callers that haven't migrated their imports yet, not itself dead code.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.verify.port_parser import parse_hls_verilog_ports, write_port_map_yaml
from forge.verify.rtl_introspection import RegexFallbackUnsupported

# Non-ANSI, one-declaration-per-statement style — what the regex fallback
# (and real Vitis HLS ap_ctrl_none output) actually produces.
_HLS_STYLE_VERILOG = """\
module dut(
    ap_clk,
    ap_rst,
    data_in,
    data_out
);

input ap_clk;
input ap_rst;
input [15:0] data_in;
output data_out;

endmodule
"""


def test_parse_hls_verilog_ports_extracts_scalar_and_bus_ports(tmp_path: Path) -> None:
    verilog = tmp_path / "dut.v"
    verilog.write_text(_HLS_STYLE_VERILOG)

    ports = parse_hls_verilog_ports(verilog)

    assert ports == [
        {"name": "ap_clk", "direction": "input", "width": 1},
        {"name": "ap_rst", "direction": "input", "width": 1},
        {"name": "data_in", "direction": "input", "width": 16},
        {"name": "data_out", "direction": "output", "width": 1},
    ]


def test_parse_hls_verilog_ports_rejects_ansi_style(tmp_path: Path) -> None:
    verilog = tmp_path / "dut.v"
    verilog.write_text(
        "module dut (\n  input ap_clk,\n  output data_out\n);\nendmodule\n"
    )

    with pytest.raises(RegexFallbackUnsupported):
        parse_hls_verilog_ports(verilog)


def test_write_port_map_yaml_round_trips(tmp_path: Path) -> None:
    ports = [
        {"name": "ap_clk", "direction": "input", "width": 1},
        {"name": "data_in", "direction": "input", "width": 16},
    ]
    out_path = tmp_path / "port_map.yaml"

    write_port_map_yaml(ports, out_path)

    text = out_path.read_text()
    assert "name:      ap_clk" in text
    assert "name:      data_in" in text
    assert "width:     16" in text
