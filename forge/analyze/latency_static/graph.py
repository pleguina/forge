"""forge.analyze.latency_static.graph
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Build a directed latency graph from the canonical resolved design IR
(migration step 7 — docs/development/release-readiness.md), falling back
to an independent design.yml/modules.yml re-parse only for the narrow,
undocumented case of a `modules_yml_path` override that genuinely differs
from design.yml's own `registry:` field (never observed in real/documented
usage, but the parameter's public contract allows it).

Each node represents a named module group (one or more instances); its
``latency_cycles`` is the number of clock cycles from valid-in to valid-out
for a single pipeline pass.

Latency resolution order
------------------------
1. ``latency_cycles`` field in modules.yml entry (explicit override)
2. HLS synthesis report worst-case latency (if hls_reports dict is supplied)
3. ``latency_hint`` field in modules.yml entry (rough manual estimate)
4. ``None`` — unknown; reported as a warning in the checker

Edges are inferred from ``connections`` and ``topology_groups`` in design.yml
(via the IR's resolved connections in the primary path; via a raw re-parse
in the fallback path).
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


def _resolve_latency(
    latency_cycles: Optional[int],
    latency_hint: Optional[int],
    ref_name: str,
    hls_reports: Optional[Dict[str, int]],
) -> Tuple[Optional[int], str]:
    """Shared explicit > hls_report > hint > unknown precedence — used by
    both the IR-driven path and the legacy raw-YAML fallback, so the rule
    is defined in exactly one place."""
    if latency_cycles is not None:
        return latency_cycles, "explicit"
    if hls_reports and ref_name in hls_reports:
        return hls_reports[ref_name], "hls_report"
    if latency_hint is not None:
        return latency_hint, "hint"
    return None, "unknown"


def _build_latency_map(
    registry: dict,
    hls_reports: Optional[Dict[str, int]],
) -> Dict[str, Tuple[Optional[int], str]]:
    """Return {ref_name: (latency_cycles, source)}."""
    result: Dict[str, Tuple[Optional[int], str]] = {}
    for entry in registry.get("modules", []):
        name = entry.get("name", "")
        lat_cycles = int(entry["latency_cycles"]) if "latency_cycles" in entry else None
        lat_hint = int(entry["latency_hint"]) if "latency_hint" in entry else None
        result[name] = _resolve_latency(lat_cycles, lat_hint, name, hls_reports)
    return result


def _resolved_registry_path(design_path: Path) -> Optional[Path]:
    """The registry path design.yml's own `registry:` field resolves to,
    or None if it doesn't declare one — used to decide whether a caller's
    `modules_yml_path` override is genuinely different (see build_graph)."""
    design = _load_yaml(design_path)
    reg_field = design.get("registry")
    if not reg_field:
        return None
    return (design_path.parent / reg_field).resolve()


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
        the ``registry:`` field inside ``design.yml`` is used. When given
        and it resolves to the *same* file design.yml's own ``registry:``
        field already points to (the documented, real-world usage), the IR-
        driven path is used. When it resolves to a genuinely different
        file, this falls back to an independent raw-YAML re-parse — a
        narrow compatibility path for an override never observed in real
        usage, not the primary implementation.
    hls_reports:
        Optional mapping ``{module_name: worst_case_latency_cycles}`` produced
        by :func:`forge.analyze.hls_reports.extractor.latency_map_from_reports`.
    """
    design_path = Path(design_path).resolve()
    modules_yml_path = Path(modules_yml_path).resolve() if modules_yml_path is not None else None

    own_registry_path = _resolved_registry_path(design_path)
    override_differs = (
        modules_yml_path is not None
        and modules_yml_path != own_registry_path
    )

    if override_differs:
        return _build_graph_legacy(design_path, modules_yml_path, hls_reports)
    return _build_graph_from_ir(design_path, hls_reports)


def _build_graph_from_ir(
    design_path: Path,
    hls_reports: Optional[Dict[str, int]] = None,
) -> LatencyGraph:
    """Migration step 7: build the LatencyGraph from ``DesignConfig`` — the
    same shared loader the canonical IR itself is built from
    (``forge.topgen.config``) — instead of independently re-parsing
    design.yml/modules.yml as raw YAML.

    Deliberately stops at ``DesignConfig``/``Module`` rather than going
    through the full matched IR (``forge.ir.build_project_ir``): latency
    analysis only ever needed module-level topology (name/kind/instances/
    timing, and module-to-module connectivity) — it never needed IP/port-
    level physical matching, and forcing it through the full IR would
    require ``ip_info``/contracts to resolve successfully, breaking the
    tool's existing "usable before synthesis" property (a design.yml can be
    latency-checked long before any IP is built or contract is written).
    ``Connection``/``TopologyGroup`` already carry singular, fan-out-
    expanded ``from_``/``to`` module names, so no port matching is needed
    to build module-to-module edges either.
    """
    from forge.topgen.config import DesignConfig

    cfg = DesignConfig.load_relaxed(design_path)

    nodes: Dict[str, LatencyNode] = {}
    for mod in cfg.modules:
        ref = mod.ip_info_key or mod.name
        timing = mod.timing
        lat_cycles = timing.latency_cycles if timing else None
        lat_hint = timing.latency_hint if timing else None
        is_var = timing.variable_latency if timing else False
        lat, src = _resolve_latency(lat_cycles, lat_hint, ref, hls_reports)
        nodes[mod.name] = LatencyNode(
            name=mod.name,
            ref=ref,
            instances=mod.instances,
            kind=mod.kind,
            latency_cycles=lat,
            latency_source=src,
            is_variable=is_var,
        )

    edges: List[LatencyEdge] = []
    seen_pairs: set = set()

    def _add_edge(src: str, dst: str) -> None:
        if src in nodes and dst in nodes and (src, dst) not in seen_pairs:
            seen_pairs.add((src, dst))
            edges.append(LatencyEdge(src=src, dst=dst))

    for conn in cfg.connections:
        _add_edge(conn.from_, conn.to)
    for tg in cfg.topology_groups:
        _add_edge(tg.from_, tg.to)

    return LatencyGraph(nodes=nodes, edges=edges)


def _build_graph_legacy(
    design_path: Path,
    modules_yml_path: Optional[Path] = None,
    hls_reports: Optional[Dict[str, int]] = None,
) -> LatencyGraph:
    """Pre-migration implementation: independently re-parses design.yml/
    modules.yml as raw YAML. Kept only as a compatibility fallback for a
    `modules_yml_path` override that genuinely differs from design.yml's
    own `registry:` field — never observed in real/documented usage, but
    the public `build_graph` signature has always allowed it."""
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
