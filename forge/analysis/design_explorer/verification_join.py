"""The conservative "flow entry points" verification overlay join.

``flow.top_module`` names a flow's *configured entry point* — it does not
prove independent behavioral verification of every instance reachable
from it. This module joins only that honest, provable fact: `flow X's
declared entry point is module Y`. It deliberately does not claim
"module Y was verified" — see ``forge.verification.results.VerificationTarget``
for the real, typed extension point a future slice can populate once a
plugin's flow declaration says what it actually covers.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from ...ir.model import ResolvedProject
from ...ir.verification_plan import resolve_entry_point_module
from ...verification.design_contract import load_verify_design


def _module_group_node_id(module_name: str) -> str:
    # Mirrors forge.analysis.design_explorer.graph_model's own convention —
    # duplicated as a bare string formatter (not imported) to avoid a
    # verification -> visualization import for one f-string.
    return f"module:{module_name}"


def join_flow_entry_points(
    project: ResolvedProject,
    *,
    verify_design_path: "str | Path",
    results_payload: Dict[str, Any],
) -> Dict[str, str]:
    """Return ``{flow_name: module_group_node_id}`` for the one real flow
    named in *results_payload* (a ``FlowResult.to_dict()``-shaped dict, as
    read from a real ``--results-json`` file — exactly what ``forge
    report``'s existing verification section already consumes), when its
    declared ``top_module`` resolves to a real module in *project*.

    *top_module* is matched against both the design's own module name and
    its ``ip_info_key`` (the ``modules.yml`` ``ref`` a module may resolve
    through) — ``flow.top_module`` is documented to use the ``modules.yml``
    ref, which is not always the same string as the design.yml module name.

    Returns an empty dict — never raises — when the flow isn't declared in
    the verification contract, or its entry point doesn't resolve to any
    real module: an honest absence, not a fabricated join.
    """
    flow_name = results_payload.get("flow_name")
    if not flow_name:
        return {}

    contract = load_verify_design(Path(verify_design_path))
    top_module_by_flow = {fl.name: fl.top_module for fl in contract.flows}
    declared_top_module = top_module_by_flow.get(flow_name)
    if not declared_top_module:
        return {}

    module_name = resolve_entry_point_module(project, declared_top_module)
    if module_name is None:
        return {}

    return {flow_name: _module_group_node_id(module_name)}
