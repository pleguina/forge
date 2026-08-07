"""
FORGE - Framework for Orchestrated RTL Generation & Evaluation
================================================================

Contract-driven DUT generation, optional HLS orchestration, and reusable
verification runtime flows.

Main functionality (forge.contracts / forge.generation):
- Parse IP metadata from component.xml, VHDL, Verilog
- Auto-match ports between connected modules
- Generate structural VHDL or Block Design TCL
- Extract IP archives

Usage:
    from forge import DesignConfig

    cfg = DesignConfig.load("design.yaml")
"""

__version__ = "2.0.0"

from .contracts.config import DesignConfig, Module, Connection
from .generation.generators.structural_vhdl import write_structural_vhdl
from .generation.generators.block_design import write_bd_tcl
from .contracts.parser import collect_all, write_summary, parse_component
from .contracts.matcher import auto_match_ports, load_ip_info
from .contracts.unpacker import unpack_ip_archives

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
