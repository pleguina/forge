"""forge.contracts — cardinality, CDC, contract loading/verification,
coordinates, domain resolution, matching, and topology derivation: the
foundational design semantics every other subsystem (generation,
verification, analysis, the IR) builds on."""

from .parser import collect_all, write_summary, parse_component
from .matcher import auto_match_ports, load_ip_info
from .unpacker import unpack_ip_archives

__all__ = [
    "collect_all",
    "write_summary", 
    "parse_component",
    "auto_match_ports",
    "load_ip_info",
    "unpack_ip_archives",
]
