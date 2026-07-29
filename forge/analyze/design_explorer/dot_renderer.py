"""Deterministic Graphviz DOT + SVG rendering from a ``DesignGraph``
(release-plan Phase 8, §8.1).

Plain Python string templating — no ``graphviz``/``pydot`` binding is
installed or declared as a dependency (DOT is a text format; none is
needed). Rendering SVG shells out to the real ``dot`` binary, matching
this codebase's own established pattern of shelling out to a real
external tool (Phase 7's ``subprocess_wrapper.py``) rather than adding a
Python binding dependency.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from .escaping import dot_html_label_text, dot_id, dot_label
from .graph_model import DesignGraph, GraphEdge, GraphNode, GraphNodeKind

# Visual style per real `wiring_method` value — every real value the
# matcher produces (`forge.topgen.ip.matcher.MatchReport.wiring_method_counts`'s
# keys), plus the honest "unknown" fallback for an unclassified/global-net
# edge (wiring_method=None).
_WIRING_METHOD_COLOR: Dict[str, str] = {
    "contract_wiring": "#2e7d32",
    "port_map_ranges": "#1565c0",
    "port_map": "#1565c0",
    "auto_match": "#ef6c00",
    "topology_group": "#6a1b9a",
    "heuristic": "#c62828",
}
_UNKNOWN_WIRING_COLOR = "#757575"

_SEVERITY_COLOR = {"error": "#c62828", "warning": "#ef6c00", "info": "#1565c0"}


def _node_diagnostic_border(node: GraphNode) -> Optional[str]:
    if not node.diagnostics:
        return None
    worst = max(node.diagnostics, key=lambda d: {"error": 2, "warning": 1, "info": 0}.get(d.severity, 0))
    return _SEVERITY_COLOR.get(worst.severity)


def _instance_node_dot(node: GraphNode) -> str:
    label_lines = [node.id]
    if node.clock_domain:
        label_lines.append(f"clk: {node.clock_domain}")
    if node.maturity is not None:
        label_lines.append(f"maturity: {node.maturity.status.value}")
    if node.latency is not None and node.latency.get("cycles") is not None:
        label_lines.append(f"latency: {node.latency['cycles']}c")
    if node.diagnostics or node.inherited_diagnostics:
        label_lines.append(f"⚠ {len(node.diagnostics) + len(node.inherited_diagnostics)} diagnostic(s)")

    label = dot_label("\n".join(label_lines))
    border = _node_diagnostic_border(node) or (
        _SEVERITY_COLOR.get(
            max((d.severity for d in node.inherited_diagnostics), default="",
                key=lambda s: {"error": 2, "warning": 1, "info": 0}.get(s, -1)),
        ) if node.inherited_diagnostics else None
    )
    color = f', color="{border}", penwidth=2' if border else ""
    return f'  {dot_id(node.id)} [shape=box, style="rounded,filled", fillcolor="#eef2f7", label={label}{color}];'


def _external_port_node_dot(node: GraphNode) -> str:
    label = dot_label(node.label)
    return f'  {dot_id(node.id)} [shape=cds, style=filled, fillcolor="#fff8e1", label={label}];'


def _module_cluster_open(node: GraphNode) -> List[str]:
    border = _node_diagnostic_border(node)
    color = border or "#90a4ae"
    header = dot_html_label_text(f"module: {node.label}")
    if node.maturity is not None:
        header += dot_html_label_text(f"  [{node.maturity.status.value}]")
    return [
        f"  subgraph {_cluster_id(node.module)} {{",
        f'    label=<{header}>;',
        f'    color="{color}";',
        "    style=rounded;",
    ]


def _cluster_id(module_name: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in module_name)
    return f"cluster_module_{safe}"


def _edge_dot(edge: GraphEdge) -> str:
    color = _WIRING_METHOD_COLOR.get(edge.wiring_method or "", _UNKNOWN_WIRING_COLOR)
    label_parts: List[str] = []
    for xform in edge.transformations:
        tag = f":{xform['tag']}" if xform.get("tag") else ""
        cycles = f" ({xform['cycles']}c)" if xform.get("cycles") is not None else ""
        label_parts.append(f"{xform['kind']}{tag}{cycles}")
    if edge.crosses_clock_domain:
        label_parts.append("CDC")
    if edge.crosses_reset_domain:
        label_parts.append("reset-domain-crossing")
    # `id=` carries the real, real ResolvedConnection.id verbatim into the
    # rendered DOT/SVG (mapped straight through to the SVG element's own
    # id attribute by `dot`) — the direct, literal "every real connection
    # id appears in the rendered artifact" proof (release-plan §8.5), and
    # a real hook for future click-through interactivity.
    attrs = [f"id={dot_id(edge.id)}", f'color="{color}"']
    if label_parts:
        attrs.append(f"label={dot_label(chr(10).join(label_parts))}")
    if edge.diagnostics:
        attrs.append('style=bold')
        worst = max(edge.diagnostics, key=lambda d: {"error": 2, "warning": 1, "info": 0}.get(d.severity, 0))
        attrs.append(f'fontcolor="{_SEVERITY_COLOR.get(worst.severity, "#000000")}"')
    return f"  {dot_id(edge.source)} -> {dot_id(edge.target)} [{', '.join(attrs)}];"


def render_dot(graph: DesignGraph) -> str:
    """Render *graph* as deterministic Graphviz DOT text.

    Default view (release-plan §8.1): module-definition grouping via real
    ``subgraph cluster_*`` blocks (from real ``MODULE_GROUP`` nodes' real
    ``members``), external ports as visually distinct nodes, one edge per
    real ``GraphEdge`` (including every external-port connection — no
    endpoint is ever fabricated or silently dropped), styled by
    ``wiring_method``, with generated-transformation labels and
    diagnostic color/badges from real, resolved links. All text passed
    through the centralized escaping helpers (``escaping.py``).

    Clock/reset-domain grouping is not rendered as a second, nested
    cluster hierarchy here (Graphviz clusters are not well-suited to a
    node belonging to two independent groupings at once) — that toggle is
    the interactive HTML explorer's job (§8.3), which can freely re-parent
    nodes client-side. The static view's module-definition grouping is
    the load-bearing, always-on default.
    """
    lines: List[str] = ["digraph design {", '  rankdir="LR";', "  node [fontname=\"Helvetica\"];", "  edge [fontname=\"Helvetica\", fontsize=9];"]

    module_groups = sorted(
        (n for n in graph.nodes if n.kind == GraphNodeKind.MODULE_GROUP), key=lambda n: n.id,
    )
    instances_by_module: Dict[str, List[GraphNode]] = {}
    for n in graph.nodes:
        if n.kind == GraphNodeKind.INSTANCE and n.module is not None:
            instances_by_module.setdefault(n.module, []).append(n)

    for mg in module_groups:
        lines.extend(_module_cluster_open(mg))
        for inst in sorted(instances_by_module.get(mg.module, []), key=lambda n: n.id):
            lines.append(_instance_node_dot(inst))
        lines.append("  }")

    for n in sorted(graph.nodes, key=lambda n: n.id):
        if n.kind == GraphNodeKind.EXTERNAL_PORT:
            lines.append(_external_port_node_dot(n))

    for edge in sorted(graph.edges, key=lambda e: e.id):
        lines.append(_edge_dot(edge))

    lines.append("}")
    return "\n".join(lines) + "\n"


def dot_available() -> bool:
    """Whether the real ``dot`` binary is on ``PATH`` — the internal check
    ``forge report``'s graceful-degradation path uses. Never raises."""
    import shutil
    return shutil.which("dot") is not None


def render_svg(dot_text: str, out_path: "str | Path") -> bool:
    """Render *dot_text* to a real SVG file at *out_path* via the real
    ``dot`` binary. Returns ``False`` (never raises) only for the
    internal "is dot available" degradation check — a caller that wants a
    real, actionable error when ``dot`` is missing (``forge inspect
    --svg``) should check :func:`dot_available` itself first and raise
    there, not rely on this function's return value alone."""
    if not dot_available():
        return False
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["dot", "-Tsvg"], input=dot_text, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return False
    out_path.write_text(result.stdout)
    return True
