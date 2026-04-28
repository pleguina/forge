"""
Topgen - Top-Level Generator
============================

A standalone library for generating top-level HDL wrappers from IP metadata.

Main functionality:
- Parse IP metadata from component.xml, VHDL, Verilog
- Auto-match ports between connected modules
- Generate structural VHDL or Block Design TCL
- Extract IP archives

Usage:
    from topgen import DesignConfig

    cfg = DesignConfig.load("design.yaml")
"""

__version__ = "1.1.0"

from .config import DesignConfig, Module, Connection
from .generators.structural_vhdl import write_structural_vhdl
from .generators.block_design import write_bd_tcl
from .ip.parser import collect_all, write_summary, parse_component
from .ip.matcher import auto_match_ports, load_ip_info
from .ip.unpacker import unpack_ip_archives

__all__ = [
    # Main classes
    "DesignConfig",
    "Module", 
    "Connection",
    # Generators
    "write_structural_vhdl",
    "write_bd_tcl",
    # IP tools
    "collect_all",
    "write_summary",
    "parse_component",
    "auto_match_ports",
    "load_ip_info",
    "unpack_ip_archives",
    # Version
    "__version__",
]
