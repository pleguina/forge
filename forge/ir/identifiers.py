"""The one shared instance-id construction rule the canonical IR and its
downstream consumers must agree on.

Before this module, ``forge/ir/build.py`` and
``forge/analyze/latency_static/graph.py`` each re-derived their own
instance-id string independently. They agreed for every single-instance
module (both reduce to the bare module name) but silently diverged for any
multi-instance module: the IR used ``"dec_0"`` while the latency graph used
``"dec[0]"``. Both reference plugins' committed fixtures are dominated by
single-instance modules, so this was never caught — until ``trigger_demo``'s
one multi-instance module (``dec``, 4 instances) needed to join latency
data by instance id for the visual design explorer (release-plan Phase 8).

This is the one real implementation of the convention; every caller that
needs an instance id calls this instead of re-deriving its own.
"""
from __future__ import annotations

from typing import Optional


def resolved_instance_id(module_name: str, instance_index: Optional[int], instance_count: int) -> str:
    """The canonical instance id for one instance of a module definition.

    A module with exactly one instance keeps its bare module name (e.g.
    ``"col"``) — *not* ``"col_0"`` — matching every existing single-
    instance fixture and consumer unchanged. A module with more than one
    instance gets ``f"{module_name}_{instance_index}"`` (e.g. ``"dec_0"``,
    ``"dec_1"``).
    """
    if instance_count == 1:
        return module_name
    return f"{module_name}_{instance_index}"
