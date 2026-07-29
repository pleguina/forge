"""
Project the canonical IR back into the legacy ``conn_map``/``global_nets``
shapes generators consume (``forge.topgen.ip.matcher.auto_match_ports``'s
return shape) — the inverse of what ``forge.ir.build.assemble_project_ir``
builds them into.

This exists for migration step 5: generators (currently
``write_structural_verilog``, verilog mode only) can be fed from the IR
instead of directly from the matcher, without changing their internal
logic or the exact bytes of what they emit. Byte-for-byte equivalence
depends entirely on ``ResolvedConnection.emission_order`` reproducing the
*original* construction order — the IR's stored connection list itself is
sorted by ``id`` for reproducible hashing/diffing/visualization, which is a
different (and for this purpose, wrong) order.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from .model import ResolvedProject

_EXTERNAL = "$external"
_TIE_OFF = "$tie_off"

ConnMap = Dict[Tuple[str, str], List[Tuple[str, str]]]
GlobalNets = Dict[str, List[Tuple[str, str]]]


def project_to_conn_map(project: ResolvedProject) -> Tuple[ConnMap, GlobalNets]:
    """Reconstruct ``(conn_map, global_nets)`` from *project*'s connections,
    in their original construction order (``emission_order``), exactly as
    ``forge.topgen.ip.matcher.auto_match_ports`` would have returned them.
    """
    conn_map: ConnMap = {}
    global_nets: GlobalNets = {}

    ordered = sorted(project.design.connections, key=lambda c: c.emission_order)
    for conn in ordered:
        if conn.producer.instance_id == _TIE_OFF:
            # Informational only (release-plan §3.3) — a tied-to-zero port
            # has no real driver; never part of a real conn_map/global_nets.
            continue
        if conn.producer.instance_id == _EXTERNAL:
            sig = conn.producer.interface_name
            global_nets.setdefault(sig, []).append((conn.consumer.instance_id, conn.consumer.port))
        else:
            key = (conn.producer.instance_id, conn.consumer.instance_id)
            conn_map.setdefault(key, []).append((conn.producer.port, conn.consumer.port))

    return conn_map, global_nets
