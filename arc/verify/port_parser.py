#!/usr/bin/env python3
"""HLS Verilog port-map parser — backward-compatibility shim.

.. deprecated::
    Use :mod:`fw_verify.rtl_introspection` directly.  This module now
    delegates all parsing to ``rtl_introspection`` and exists only for
    callers that have not yet updated their imports.

Original responsibility boundary
---------------------------------
This module parses port declarations from **HLS-generated** Verilog files
(``ap_ctrl_none`` style, Vivado HLS / Vitis HLS output) and writes the
resulting ``port_map.yaml`` consumed by ``gen_sim.generate_testbench()``.

It is intentionally scoped to HLS RTL output conventions:
  * ``input [W:0] name;``   — bus input
  * ``input name;``          — scalar input
  * ``output [W:0] name;``  — bus output
  * ``output name;``         — scalar output

It does NOT attempt to parse full Verilog module declarations, handle
``inout``, resolve parameters, or support SV-style port lists.  Plugins
with non-HLS RTL sources should supply ``port_map.yaml`` directly rather
than calling this parser.

Public API (shim — delegates to fw_verify.rtl_introspection)
-----------------------------------------------------------
  parse_hls_verilog_ports(verilog_path) → list[dict]
      Each dict has keys: ``name`` (str), ``direction`` (str), ``width`` (int).

  write_port_map_yaml(ports, out_path) → None
      Writes ``port_map.yaml`` in the format expected by gen_sim.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from arc.verify.rtl_introspection import (
    extract_ports as _extract_ports,
    write_port_map_yaml as _write_port_map_yaml,
)


def parse_hls_verilog_ports(verilog_path: Path) -> list[dict[str, Any]]:
    """Parse port declarations from an HLS-generated Verilog file.

    Delegates to :func:`fw_verify.rtl_introspection.extract_ports` using
    ``mode="regex"`` (HLS-output fallback) for backward compatibility.
    Prefer :func:`fw_verify.rtl_introspection.extract_ports` with
    ``mode="auto"`` in new code.

    Returns:
        List of port dicts, each with ``name``, ``direction``, and ``width``.
    """
    ports = _extract_ports(Path(verilog_path), mode="regex")
    return [{"name": p.name, "direction": p.direction, "width": p.width} for p in ports]


def write_port_map_yaml(ports: list[dict[str, Any]], out_path: Path) -> None:
    """Write a ``port_map.yaml`` from a list of port dicts.

    Delegates to :func:`fw_verify.rtl_introspection.write_port_map_yaml`.
    Prefer calling that function directly in new code.

    Args:
        ports:    List of port dicts (``name``, ``direction``, ``width``).
        out_path: Destination path for the YAML file.
    """
    _write_port_map_yaml(ports, Path(out_path))
