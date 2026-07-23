"""forge.analyze.latency_static.graph
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Build a directed latency graph from design.yml + modules.yml + optional HLS
synthesis reports.

Each node represents a named module group (one or more instances); its
``latency_cycles`` is the number of clock cycles from valid-in to valid-out
for a single pipeline pass.

Latency resolution order
------------------------
1. ``latency_cycles`` field in modules.yml entry (explicit override)
2. HLS synthesis report worst-case latency (if hls_reports dict is supplied)
3. ``latency_hint`` field in modules.yml entry (rough manual estimate)
4. ``None`` — unknown; reported as a warning in the checker

Edges are inferred from ``connections`` and ``topology_groups`` in design.yml.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class LatencyNode:
    name: str             # design.yml module name (e.g. "dec")
    ref: str              # modules.yml module ref  (e.g. "hit_decoder")
    instances: int
    kind: str             # "hls" | "rtl" | "unknown"
    latency_cycles: Optional[int]
    latency_source: str   # "explicit" | "hls_report" | "hint" | "unknown"
    is_variable: bool = False


@dataclasses.dataclass
class LatencyEdge:
    src: str
    dst: str


@dataclasses.dataclass
class LatencyGraph:
    nodes: Dict[str, LatencyNode]
    edges: List[LatencyEdge]

    def predecessors(self, name: str) -> List[str]:
        return [e.src for e in self.edges if e.dst == name]

    def successors(self, name: str) -> List[str]:
        return [e.dst for e in self.edges if e.src == name]

    def sources(self) -> List[str]:
        """Nodes with no incoming edges (pipeline inputs)."""
        dst_set = {e.dst for e in self.edges}
        return [n for n in self.nodes if n not in dst_set]

    def sinks(self) -> List[str]:
        """Nodes with no outgoing edges (pipeline outputs)."""
        src_set = {e.src for e in self.edges}
        return [n for n in self.nodes if n not in src_set]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _load_registry(design: dict, design_path: Path) -> dict:
    reg_field = design.get("registry")
    if reg_field:
        reg_path = (design_path.parent / reg_field).resolve()
        if reg_path.exists():
            return _load_yaml(reg_path)
    return {}


def _build_latency_map(
    registry: dict,
    hls_reports: Optional[Dict[str, int]],
) -> Dict[str, Tuple[Optional[int], str]]:
    """Return {ref_name: (latency_cycles, source)}."""
    result: Dict[str, Tuple[Optional[int], str]] = {}
    for entry in registry.get("modules", []):
        name = entry.get("name", "")
        if "latency_cycles" in entry:
            result[name] = (int(entry["latency_cycles"]), "explicit")
        elif hls_reports and name in hls_reports:
            result[name] = (hls_reports[name], "hls_report")
        elif "latency_hint" in entry:
            result[name] = (int(entry["latency_hint"]), "hint")
        else:
            result[name] = (None, "unknown")
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_graph(
    design_path: Path,
    modules_yml_path: Optional[Path] = None,
    hls_reports: Optional[Dict[str, int]] = None,
) -> LatencyGraph:
    """Build a :class:`LatencyGraph` from *design_path*.

    Parameters
    ----------
    design_path:
        Path to the plugin's ``design.yml``.
    modules_yml_path:
        Optional explicit override for the modules registry.  If not given,
        the ``registry:`` field inside ``design.yml`` is used.
    hls_reports:
        Optional mapping ``{module_name: worst_case_latency_cycles}`` produced
        by :func:`forge.analyze.hls_reports.extractor.latency_map_from_reports`.
    """
    design = _load_yaml(design_path)

    if modules_yml_path is not None:
        registry = _load_yaml(modules_yml_path)
    else:
        registry = _load_registry(design, design_path)

    latency_map = _build_latency_map(registry, hls_reports)
    reg_by_name: Dict[str, dict] = {e["name"]: e for e in registry.get("modules", [])}

    # ── Nodes ──────────────────────────────────────────────────────────────
    nodes: Dict[str, LatencyNode] = {}
    for mod in design.get("modules", []):
        name = mod["name"]
        ref = mod.get("ref", name)
        instances = mod.get("instances", 1)
        reg_entry = reg_by_name.get(ref, {})
        kind = reg_entry.get("kind", "unknown")
        lat, src = latency_map.get(ref, (None, "unknown"))
        is_var = bool(reg_entry.get("variable_latency", False))
        nodes[name] = LatencyNode(
            name=name,
            ref=ref,
            instances=instances,
            kind=kind,
            latency_cycles=lat,
            latency_source=src,
            is_variable=is_var,
        )

    # ── Edges ──────────────────────────────────────────────────────────────
    def _add_edge(src: str, dst: str, edges: List[LatencyEdge]) -> None:
        if src in nodes and dst in nodes:
            if not any(e.src == src and e.dst == dst for e in edges):
                edges.append(LatencyEdge(src=src, dst=dst))

    def _endpoints(value) -> List[str]:
        """Normalise a connection endpoint into a list of module names.

        A ``from``/``to`` endpoint may be a single module name or, for fan-out
        and fan-in connections, a list of module names.  Both forms are
        expanded into individual edges.
        """
        if isinstance(value, list):
            return [v for v in value if isinstance(v, str) and v]
        if isinstance(value, str) and value:
            return [value]
        return []

    edges: List[LatencyEdge] = []
    for conn in design.get("connections", []):
        for src in _endpoints(conn.get("from", "")):
            for dst in _endpoints(conn.get("to", "")):
                _add_edge(src, dst, edges)
    for tg in design.get("topology_groups", []):
        for src in _endpoints(tg.get("from", "")):
            for dst in _endpoints(tg.get("to", "")):
                _add_edge(src, dst, edges)

    return LatencyGraph(nodes=nodes, edges=edges)
