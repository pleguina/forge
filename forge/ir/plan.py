"""
Deterministic generation-plan artifact.

Deliberately separate from ``model.py``/``build.py``/``serialize.py`` —
same rationale as ``serialize.py``'s own docstring: a plan is a *reshape*
of already-computed IR/matching facts (``ResolvedProject``, ``MatchReport``,
the 3 structured strict-check issue lists) into 8 defined sections, not a
new computation. This keeps it reusable by any future
consumer (CLI, CI, explorer) without pulling in argparse or the CLI layer
— enforced the same way the rest of ``forge/ir/`` is, by
``ci/import_direction_check.sh``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .model import ResolvedProject
from ..core.utils.content_hash import hash_bytes

PLAN_SCHEMA_VERSION = "0.1.0"

# ResolvedConnection.wiring_method vocabulary (forge/contracts/matcher.py's
# MatchReport.wiring_method_counts keys) split into "inferred" (the matcher
# guessed) vs "explicit" (the design.yml/contract said so outright).
_INFERRED_WIRING_METHODS = frozenset({"auto_match", "topology_group"})
_EXPLICIT_WIRING_METHODS = frozenset({"contract_wiring", "port_map", "port_map_ranges"})


@dataclass
class PlannedConnection:
    """One connection's identity + selected wiring strategy, for the
    inferred_connections/explicit_connections plan sections."""
    id: str
    producer: str  # "instance.port"
    consumer: str
    wiring_method: Optional[str]


@dataclass
class PlannedTransformation:
    """One generated-or-declared transformation, attributed back to its
    owning connection."""
    connection_id: str
    kind: str
    cycles: Optional[int]
    tag: Optional[str]


@dataclass
class UnresolvedIssue:
    """One entry in the plan's unresolved_issues section — a reshape of an
    existing structured issue (TopologyGroupIssue/CardinalityIssue/
    CdcIssue/DiagnosticReference), not a new check."""
    severity: str  # 'error' | 'warning' | 'info'
    category: str  # 'topology_group' | 'cardinality' | 'cdc' | 'diagnostic'
    message: str
    object_id: Optional[str] = None


@dataclass
class GenerationPlan:
    """The plan's 8 required sections. ``generated_from`` is
    provenance-only and deliberately excluded from ``plan_hash`` — same
    exclusion rationale as ``serialize.py``'s ``_canonical_design_json``
    excluding ``ResolvedProject.generated_from``: a plan hash should
    answer "did the plan change?", not "did the caller's filesystem
    layout change?"."""
    schema_version: str = PLAN_SCHEMA_VERSION
    design_name: str = ""
    inferred_connections: List[PlannedConnection] = field(default_factory=list)
    explicit_connections: List[PlannedConnection] = field(default_factory=list)
    generated_transformations: List[PlannedTransformation] = field(default_factory=list)
    # Descriptive filtered view of generated_transformations (kinds that
    # carry cycle counts) — NOT a new latency computation. `forge analyze
    # latency-check` remains the actual latency-analysis tool.
    latency_changes: List[PlannedTransformation] = field(default_factory=list)
    # Per-connection MatchingEvidence (as plain dicts,
    # already JSON-shaped via asdict) plus MatchReport.rejected_matches
    # under the "unmatched_candidates" key.
    matching_evidence: Dict[str, Any] = field(default_factory=dict)
    compat_mode_modules: List[str] = field(default_factory=list)
    unresolved_issues: List[UnresolvedIssue] = field(default_factory=list)
    output_artifacts: List[str] = field(default_factory=list)
    generated_from: Dict[str, Optional[str]] = field(default_factory=dict)


_LATENCY_BEARING_KINDS = frozenset({"pipeline_register", "latency_delay", "slr_crossing"})


def build_generation_plan(
    project: ResolvedProject,
    match_report: Any,
    *,
    topology_group_issues: List[Any],
    cardinality_issues: List[Any],
    cdc_issues: List[Any],
    compat_mode_modules: List[str],
    output_artifacts: List[str],
) -> GenerationPlan:
    """Reshape an already-built ``ResolvedProject`` + ``MatchReport`` + the
    3 structured strict-check issue lists (``forge.core.cli.groups.topgen
    .compute_gen_top_plan``'s return value) into a ``GenerationPlan``. No
    new matching/generation/analysis is performed here — every field is a
    reshape of data that already exists.
    """
    inferred: List[PlannedConnection] = []
    explicit: List[PlannedConnection] = []
    transformations: List[PlannedTransformation] = []
    latency_changes: List[PlannedTransformation] = []
    matching_evidence: Dict[str, Any] = {}

    for conn in project.design.connections:
        pc = PlannedConnection(
            id=conn.id,
            producer=f"{conn.producer.instance_id}.{conn.producer.port}",
            consumer=f"{conn.consumer.instance_id}.{conn.consumer.port}",
            wiring_method=conn.wiring_method,
        )
        if conn.wiring_method in _INFERRED_WIRING_METHODS:
            inferred.append(pc)
        elif conn.wiring_method in _EXPLICIT_WIRING_METHODS:
            explicit.append(pc)
        # wiring_method in {"heuristic", None} (global clock/reset nets):
        # not part of either bucket — reflected via compat_mode_modules
        # instead, matching this plan's own scope (connections/
        # topology_groups, not clock/reset fan-out).

        for xform in conn.transformations:
            pt = PlannedTransformation(
                connection_id=conn.id, kind=xform.kind,
                cycles=xform.cycles, tag=xform.tag,
            )
            transformations.append(pt)
            if xform.kind in _LATENCY_BEARING_KINDS:
                latency_changes.append(pt)

        if conn.matching_evidence is not None:
            matching_evidence[conn.id] = asdict(conn.matching_evidence)

    if getattr(match_report, "rejected_matches", None):
        matching_evidence["unmatched_candidates"] = [
            asdict(r) for r in match_report.rejected_matches
        ]

    unresolved: List[UnresolvedIssue] = []
    for i in topology_group_issues:
        unresolved.append(UnresolvedIssue(
            severity=i.severity, category="topology_group", message=i.message, object_id=i.group,
        ))
    for i in cardinality_issues:
        unresolved.append(UnresolvedIssue(
            severity=i.severity, category="cardinality", message=i.message, object_id=i.role,
        ))
    for i in cdc_issues:
        unresolved.append(UnresolvedIssue(
            severity=i.severity, category="cdc", message=i.message, object_id=i.connection,
        ))
    for d in project.design.diagnostics:
        unresolved.append(UnresolvedIssue(
            severity=d.severity, category="diagnostic", message=d.message, object_id=d.object_id,
        ))

    return GenerationPlan(
        design_name=project.design.name,
        inferred_connections=inferred,
        explicit_connections=explicit,
        generated_transformations=transformations,
        latency_changes=latency_changes,
        matching_evidence=matching_evidence,
        compat_mode_modules=list(compat_mode_modules),
        unresolved_issues=unresolved,
        output_artifacts=list(output_artifacts),
        generated_from=dict(project.generated_from),
    )


def _canonical_plan_json(plan: GenerationPlan) -> str:
    """The subset of the plan that participates in the hash — everything
    except ``generated_from`` (provenance-only, path-specific; see
    ``GenerationPlan``'s docstring)."""
    payload = asdict(plan)
    payload.pop("generated_from", None)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def plan_hash(plan: GenerationPlan) -> str:
    """Deterministic sha256 hex digest of the plan's content, for
    ``forge build --accept-plan-hash``. Reuses the generic
    ``content_hash.hash_bytes`` primitive — same pattern
    ``serialize.py::content_hash`` already uses for the IR itself."""
    return hash_bytes(_canonical_plan_json(plan).encode("utf-8"))
