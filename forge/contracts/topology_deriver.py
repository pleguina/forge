"""
Topology group derivation engine.

Resolves ``topology_groups`` entries in design.yml into concrete wire pairs
using interface contracts.  This replaces ``port_map_ranges`` for grouped
data-path connections.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from .coordinates import CoordinateKey, coordinate_key, coordinate_label

if TYPE_CHECKING:
    from .config import TopologyGroup, InstanceAssign
    from .contract_loader import LoadedContract


def derive_topology_group(
    tg: "TopologyGroup",
    src_contract: "LoadedContract",
    dst_contract: "LoadedContract",
) -> List[Tuple[str, str, Optional[int], Optional[dict]]]:
    """
    Resolve a topology_group into wire pairs ``(src_pin, dst_pin, slot, meta)``.

    If *instance_assign* is present, produces pairs annotated with instance-slot
    metadata so the caller can wire the correct producer instances to the correct
    consumer partition slots.

    If *role_pairs* is specified, uses explicit role pairing.
    Otherwise, matches roles by ``wiring_kind`` + ``partition`` label.

    Returns the same 4-tuple format as ``auto_match_ports`` base_pairs:
    ``(src_port, dst_port, slot_or_none, meta_or_none)``
    """
    pairs: List[Tuple[str, str, Optional[int], Optional[dict]]] = []

    # Add supplementary scalar port_map pairs first
    for s, d in tg.port_map:
        pairs.append((s, d, None, None))

    # Collect roles filtered by wiring_kind
    src_roles = _get_matching_roles(src_contract, "output", tg.wiring_kind)
    dst_roles = _get_matching_roles(dst_contract, "input", tg.wiring_kind)

    if tg.role_pairs is not None:
        # Explicit role pairing — designer specifies exact role → role mapping.
        # When role_pairs are explicit, use ALL roles (unfiltered by wiring_kind)
        # so that generic modules (like signal_delay) without wiring_kind can be
        # referenced by name.
        all_src = src_contract.get_connection_roles("output", require_wiring_kind=False)
        all_dst = dst_contract.get_connection_roles("input", require_wiring_kind=False)
        pairs.extend(_resolve_explicit_role_pairs(
            tg.role_pairs, all_src, all_dst, tg.name,
        ))
    elif tg.instance_assign:
        # Instance-partition assignment — multi-instance producer to partitioned consumer
        pairs.extend(_resolve_instance_partition(
            tg, src_roles, dst_roles,
        ))
    else:
        # Automatic matching — partition-aware or 1:1 wiring_kind
        pairs.extend(_resolve_auto_match(
            src_roles, dst_roles, tg.name,
        ))

    return pairs


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_matching_roles(
    contract: "LoadedContract",
    direction: str,
    wiring_kind_filter: Optional[str],
) -> List[Dict]:
    """Get contract roles matching the direction and optional wiring_kind filter."""
    roles = contract.get_connection_roles(direction)
    if wiring_kind_filter:
        roles = [r for r in roles if r["wiring_kind"] == wiring_kind_filter]
    return roles


def _resolve_explicit_role_pairs(
    role_pairs: List[Tuple[str, str]],
    src_roles: List[Dict],
    dst_roles: List[Dict],
    group_name: str,
) -> List[Tuple[str, str, None, None]]:
    """Resolve explicit role_pairs into wire pairs."""
    src_by_name = {r["role_name"]: r for r in src_roles}
    dst_by_name = {r["role_name"]: r for r in dst_roles}

    pairs: List[Tuple[str, str, None, None]] = []
    for src_role_name, dst_role_name in role_pairs:
        src_role = src_by_name.get(src_role_name)
        dst_role = dst_by_name.get(dst_role_name)
        if src_role is None:
            raise ValueError(
                f"topology_group '{group_name}': source role '{src_role_name}' "
                f"not found in producer contract"
            )
        if dst_role is None:
            raise ValueError(
                f"topology_group '{group_name}': destination role '{dst_role_name}' "
                f"not found in consumer contract"
            )
        pairs.extend(_expand_role_pair(src_role, dst_role))
    return pairs


def _resolve_instance_partition(
    tg: "TopologyGroup",
    src_roles: List[Dict],
    dst_roles: List[Dict],
) -> List[Tuple[str, str, Optional[int], Optional[dict]]]:
    """
    Resolve instance-partition assignment.

    When a multi-instance producer connects to a partitioned consumer, each
    ``instance_assign`` entry maps a range of producer instances to a consumer
    coordinate.  The producer contract is expected to have a single output
    role for the matching wiring_kind (since each instance produces one
    output).  The consumer contract has multiple roles with different
    coordinates, declared as either a legacy ``partition:`` string or a
    structured ``coordinates:`` mapping (see ``forge.contracts.coordinates``).
    """
    # Producer: expect exactly one output role per wiring_kind
    # (each instance of the producer has this single output)
    if len(src_roles) != 1:
        raise ValueError(
            f"topology_group '{tg.name}': instance_assign requires exactly one "
            f"producer output role for wiring_kind '{tg.wiring_kind}', "
            f"found {len(src_roles)}"
        )
    src_role = src_roles[0]

    # Consumer: group input roles by coordinate key
    dst_by_coord: Dict[CoordinateKey, Dict] = {}
    for r in dst_roles:
        coord = coordinate_key(r)
        if coord is None:
            raise ValueError(
                f"topology_group '{tg.name}': consumer role '{r['role_name']}' "
                f"has wiring_kind '{r['wiring_kind']}' but no partition/coordinates label"
            )
        if coord in dst_by_coord:
            raise ValueError(
                f"topology_group '{tg.name}': duplicate coordinate "
                f"'{coordinate_label(coord)}' in consumer roles"
            )
        dst_by_coord[coord] = r

    pairs: List[Tuple[str, str, Optional[int], Optional[dict]]] = []
    for ia in tg.instance_assign:
        ia_coord = ia.coordinate_key()
        dst_role = dst_by_coord.get(ia_coord)
        if dst_role is None:
            raise ValueError(
                f"topology_group '{tg.name}': coordinate "
                f"'{coordinate_label(ia_coord)}' not found in consumer contract. "
                f"Available: {sorted(coordinate_label(k) for k in dst_by_coord)}"
            )

        start, end = ia.instances
        instance_count = end - start
        dst_count = dst_role.get("count", 1)

        if instance_count != dst_count:
            raise ValueError(
                f"topology_group '{tg.name}': instance range [{start},{end}) "
                f"has {instance_count} instances but coordinate "
                f"'{coordinate_label(ia_coord)}' has count={dst_count}"
            )

        # Resolve the physical port names
        src_port = _get_single_port_name(src_role)
        dst_prefix = dst_role.get("raw_port_prefix", "")

        for i in range(instance_count):
            src_inst_idx = start + i
            dst_port = f"{dst_prefix}{i}"
            pairs.append((
                src_port,
                dst_port,
                src_inst_idx,
                {"kind": "topology_group", "group": tg.name,
                 "partition": coordinate_label(ia_coord), "src_instance": src_inst_idx},
            ))

    return pairs


def _resolve_auto_match(
    src_roles: List[Dict],
    dst_roles: List[Dict],
    group_name: str,
) -> List[Tuple[str, str, None, None]]:
    """
    Auto-match producer and consumer roles by wiring_kind + coordinates.

    When coordinates (or the legacy ``partition`` label) exist, matches by
    ``(wiring_kind, coordinate_key)``. When no coordinates exist, matches by
    wiring_kind alone (1:1 only).
    """
    # Group by (wiring_kind, coordinate_key) — coordinate_key=None for
    # unpartitioned roles
    src_grouped: Dict[Tuple[str, Optional[CoordinateKey]], List[Dict]] = defaultdict(list)
    for r in src_roles:
        key = (r["wiring_kind"], coordinate_key(r))
        src_grouped[key].append(r)

    dst_grouped: Dict[Tuple[str, Optional[CoordinateKey]], List[Dict]] = defaultdict(list)
    for r in dst_roles:
        key = (r["wiring_kind"], coordinate_key(r))
        dst_grouped[key].append(r)

    pairs: List[Tuple[str, str, None, None]] = []

    # Match by (wiring_kind, coordinate_key) keys
    for key in src_grouped:
        if key not in dst_grouped:
            continue
        sg = src_grouped[key]
        dg = dst_grouped[key]
        if len(sg) != 1 or len(dg) != 1:
            wk, coord = key
            raise ValueError(
                f"topology_group '{group_name}': ambiguous match for "
                f"wiring_kind='{wk}', coordinate='{coordinate_label(coord)}' — "
                f"{len(sg)} src roles, {len(dg)} dst roles. "
                f"Use role_pairs for explicit disambiguation."
            )
        pairs.extend(_expand_role_pair(sg[0], dg[0]))

    return pairs


def _expand_role_pair(
    src_role: Dict, dst_role: Dict,
) -> List[Tuple[str, str, None, None]]:
    """Expand a matched src/dst role pair into concrete wire pairs."""
    pairs: List[Tuple[str, str, None, None]] = []

    sk, dk = src_role["kind"], dst_role["kind"]

    if sk == "nd_tpl" and dk == "nd_tpl":
        # N-D template expansion — import from matcher
        from .matcher import _expand_nd
        expanded = _expand_nd(
            src_role["raw_port_tpl"],
            dst_role["raw_port_tpl"],
            dims=src_role["dims"],
        )
        pairs.extend((s, d, None, None) for s, d in expanded)

    elif sk == "prefix_array" and dk == "prefix_array":
        n = min(src_role["count"], dst_role["count"])
        pfx_s = src_role["raw_port_prefix"]
        pfx_d = dst_role["raw_port_prefix"]
        for i in range(n):
            pairs.append((f"{pfx_s}{i}", f"{pfx_d}{i}", None, None))

    elif sk == "scalar" and dk == "scalar":
        pairs.append((src_role["raw_port"], dst_role["raw_port"], None, None))

    elif sk == "prefix_array" and dk == "scalar":
        # Scatter: one producer's array elements → individual consumer instances
        pfx = src_role["raw_port_prefix"]
        port = dst_role["raw_port"]
        for i in range(src_role["count"]):
            pairs.append((
                f"{pfx}{i}", port, None,
                {"kind": "scatter", "target_instance": i},
            ))

    elif sk == "scalar" and dk == "prefix_array":
        # Gather: individual producer instances → one consumer's array elements.
        # `slot` (4th element) already fully determines instance selection in
        # the matcher's replication loop, so tagging `meta` here (symmetric
        # with scatter's tag above) is purely additive evidence — it does not
        # change which pairs get wired.
        port = src_role["raw_port"]
        pfx = dst_role["raw_port_prefix"]
        for i in range(dst_role["count"]):
            pairs.append((
                port, f"{pfx}{i}", i,
                {"kind": "gather", "target_index": i},
            ))

    else:
        raise ValueError(
            f"Cannot pair roles of different kinds: "
            f"src={src_role['role_name']} ({sk}), "
            f"dst={dst_role['role_name']} ({dk})"
        )

    return pairs


def _get_single_port_name(role: Dict) -> str:
    """Get the single port name for a scalar producer role."""
    if "raw_port" in role:
        return role["raw_port"]
    if "raw_port_prefix" in role:
        return role["raw_port_prefix"].rstrip("_")
    raise ValueError(f"Cannot determine single port name for role '{role['role_name']}'")
