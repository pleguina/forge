"""Utilities package."""

# Export commonly used utilities from hdl_parser
from .hdl_parser import (
    _scan_ports,
    _scan_verilog_ports,
    _vhdl_entity_name,
    _verilog_module_name
)

__all__ = [
    "_scan_ports",
    "_scan_verilog_ports", 
    "_vhdl_entity_name",
    "_verilog_module_name"
]
