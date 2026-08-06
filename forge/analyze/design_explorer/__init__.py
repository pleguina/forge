"""forge.analyze.design_explorer — visual design outputs.

Builds a deterministic, read-only visualization projection (``DesignGraph``)
of the canonical IR (``forge.ir.model.ResolvedProject``), and renders it as
static Graphviz DOT/SVG and a self-contained, offline, interactive
HTML explorer.

``DesignGraph`` never independently resolves topology or infers semantics
— every fact it states about topology is already a fact the IR states; it
only re-shapes and enriches (with latency/maturity/verification overlays)
what the IR already says. See ``graph_model.py``'s module docstring for
the full invariant.
"""
from __future__ import annotations

from .graph_model import (
    DESIGN_GRAPH_SCHEMA,
    DesignGraph,
    GraphEdge,
    GraphNode,
    GraphNodeKind,
    MaturityStatus,
    MaturitySummary,
    ObjectReference,
    ObjectRecord,
    ProjectionDiagnostic,
    build_design_graph,
)

__all__ = [
    "DESIGN_GRAPH_SCHEMA",
    "DesignGraph",
    "GraphEdge",
    "GraphNode",
    "GraphNodeKind",
    "MaturityStatus",
    "MaturitySummary",
    "ObjectReference",
    "ObjectRecord",
    "ProjectionDiagnostic",
    "build_design_graph",
]
