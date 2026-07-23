"""forge.core.constants — shared constants used across forge subpackages."""
from __future__ import annotations

HDL_EXTENSIONS: frozenset[str] = frozenset({".v", ".sv", ".vhd", ".vhdl"})
SOURCE_EXTENSIONS: frozenset[str] = frozenset({".cpp", ".c", ".h", ".hpp", ".cc", ".cxx"})
ALL_HLS_STAGES: tuple[str, ...] = ("csim", "synth", "cosim", "export")
