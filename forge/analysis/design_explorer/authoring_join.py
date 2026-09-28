"""Turn the connections a project has *not* made into explorer decisions —
Phase J1/J2/J3's data half.

``forge.project.discovery`` already decides which ports are unwired and
which candidates are equally valid, and refuses to choose between them
(that is what ``forge check`` reports and ``forge adopt --interactive``
asks about). This module reshapes those same questions into
:class:`~forge.analysis.design_explorer.graph_model.OpenDecision`s so the
visual explorer can display them, and attaches to each one the exact
``forge.yml`` entry and CLI command that records an answer.

It computes no candidates of its own. That matters more here than anywhere
else in the explorer: a UI that worked out its own candidate list would be
proposing connections the rest of FORGE does not know about, and a
connection authored from it would come from a different reading of the
design than the one generation uses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional, Sequence

from .graph_model import OpenDecision

#: The action ids ``forge.project.discovery`` gives its two connection
#: questions. Read rather than re-derived from wording, the same way
#: ``forge.project.interactive`` reads them.
_PRODUCER_SUBJECT_ACTION = "resolve-ambiguous-consumer"


def _module_of(endpoint: str) -> str:
    return endpoint.split(".", 1)[0]


def open_decisions_from_discovery(
    discovery: Any,
    *,
    instance_ids: Optional[Sequence[str]] = None,
) -> List[OpenDecision]:
    """Every open connection question in *discovery*, as decisions.

    *instance_ids* are the graph's real instance node ids, so a decision can
    point at the node it is about. A question whose module has no instance
    in this design (it was excluded, or the graph is of a different design)
    still comes back — with an empty ``node_id`` rather than a fabricated
    one — because a question the user must answer is not less real for
    having nothing to highlight.
    """
    known = set(instance_ids or ())
    decisions: List[OpenDecision] = []
    for ambiguity in getattr(discovery, "ambiguities", ()):
        if ambiguity.kind != "connection" or not ambiguity.options:
            continue
        if ambiguity.id.startswith("connection:declared:"):
            # A broken `connections:` entry: fixed by editing the entry, not
            # by choosing between candidates. `forge check` reports it.
            continue
        module = _module_of(ambiguity.subject)
        action_id = ambiguity.action.id if ambiguity.action else ""
        decisions.append(OpenDecision(
            id=ambiguity.id,
            subject=ambiguity.subject,
            question=ambiguity.question,
            candidates=tuple(ambiguity.options),
            subject_is_producer=action_id == _PRODUCER_SUBJECT_ACTION,
            node_id=module if module in known else "",
        ))
    return decisions


def open_decisions_for_project(
    root: "str | Path",
    *,
    instance_ids: Optional[Sequence[str]] = None,
) -> List[OpenDecision]:
    """Scan the FORGE project at *root* and return its open decisions.

    Best-effort by design: a directory that is not a FORGE project, or one
    whose config will not parse, has no decisions to show and must not stop
    the explorer from rendering. The topology view has always worked with
    zero overlay data and still does.
    """
    try:
        from forge.project.config import ForgeConfig
        from forge.project.discovery import ProjectDiscovery
        from forge.project.paths import ROOT_CONFIG_NAME

        root_path = Path(root).expanduser().resolve()
        if not (root_path / ROOT_CONFIG_NAME).is_file():
            return []
        config = ForgeConfig.load(root_path / ROOT_CONFIG_NAME)
        discovery = ProjectDiscovery(decisions=config.decisions()).scan(root_path)
    except Exception:  # noqa: BLE001
        return []
    return open_decisions_from_discovery(discovery, instance_ids=instance_ids)
