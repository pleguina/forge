"""
Tests for the topology group derivation engine.

Covers all three resolution strategies: explicit role_pairs,
instance-partition assignment, and auto-match.  Uses synthetic
contracts built from minimal YAML specs.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest


_TOPGEN_ROOT = Path(__file__).resolve().parents[1]
if str(_TOPGEN_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOPGEN_ROOT))

from topgen.config import InstanceAssign, TopologyGroup
from topgen.ip.contract_loader import LoadedContract
from topgen.ip.topology_deriver import derive_topology_group


# ─────────────────────────────────────────────────────────────────────────────
# Helpers – synthetic contracts from minimal specs
# ─────────────────────────────────────────────────────────────────────────────

def _make_contract(roles: Dict[str, dict], module_name: str = "test") -> LoadedContract:
    """Build a LoadedContract from a dict of {role_name: role_spec}."""
    spec = {
        "ip_interface": {
            "module_name": module_name,
            "ip_info_key": module_name,
            "roles": roles,
        }
    }
    return LoadedContract(path=Path(f"/fake/{module_name}.interface.yaml"), spec=spec)


def _prefix_role(
    direction: str,
    wiring_kind: str,
    prefix: str,
    count: int,
    *,
    partition: Optional[str] = None,
    coordinates: Optional[dict] = None,
    width: int = 32,
) -> dict:
    """Build a prefix_array role spec."""
    d = {
        "direction": direction,
        "wiring_kind": wiring_kind,
        "raw_port_prefix": prefix,
        "count": count,
        "width": width,
    }
    if partition is not None:
        d["partition"] = partition
    if coordinates is not None:
        d["coordinates"] = coordinates
    return d


def _nd_tpl_role(
    direction: str,
    wiring_kind: str,
    tpl: str,
    dims: List[int],
    *,
    partition: Optional[str] = None,
    width: int = 32,
) -> dict:
    """Build an nd_tpl role spec."""
    d = {
        "direction": direction,
        "wiring_kind": wiring_kind,
        "raw_port_tpl": tpl,
        "dims": dims,
        "width": width,
    }
    if partition is not None:
        d["partition"] = partition
    return d


def _scalar_role(
    direction: str,
    wiring_kind: str,
    raw_port: str,
    *,
    partition: Optional[str] = None,
    width: int = 32,
) -> dict:
    """Build a scalar role spec (has raw_port but no prefix/tpl)."""
    d = {
        "direction": direction,
        "wiring_kind": wiring_kind,
        "raw_port": raw_port,
        "width": width,
    }
    if partition is not None:
        d["partition"] = partition
    return d


def _make_tg(
    name: str = "test_group",
    family: str = "test",
    from_: str = "src",
    to: str = "dst",
    *,
    wiring_kind: Optional[str] = None,
    instance_assign: Optional[List[InstanceAssign]] = None,
    port_map: Optional[List[Tuple[str, str]]] = None,
    role_pairs: Optional[List[Tuple[str, str]]] = None,
    notes: Optional[str] = None,
) -> TopologyGroup:
    """Build a TopologyGroup from keyword arguments."""
    return TopologyGroup(
        name=name,
        family=family,
        from_=from_,
        to=to,
        wiring_kind=wiring_kind,
        instance_assign=instance_assign or [],
        port_map=port_map or [],
        role_pairs=role_pairs,
        notes=notes,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tests – explicit role_pairs
# ─────────────────────────────────────────────────────────────────────────────

class TestExplicitRolePairs:
    """Tests for the explicit role_pairs resolution strategy."""

    def test_prefix_array_pair(self):
        """Two prefix_array roles matched by name → prefix expansion."""
        src = _make_contract({
            "data_out": _prefix_role("output", "processed", "out_data_", 5),
        })
        dst = _make_contract({
            "data_in": _prefix_role("input", "processed", "in_data_", 5),
        })
        tg = _make_tg(wiring_kind="processed", role_pairs=[("data_out", "data_in")])
        pairs = derive_topology_group(tg, src, dst)

        assert len(pairs) == 5
        for i in range(5):
            assert pairs[i] == (f"out_data_{i}", f"in_data_{i}", None, None)

    def test_nd_tpl_pair(self):
        """Two nd_tpl roles matched by name → N-D expansion."""
        src = _make_contract({
            "grid_out": _nd_tpl_role("output", "grid", "src_{0}_{1}", [2, 3]),
        })
        dst = _make_contract({
            "grid_in": _nd_tpl_role("input", "grid", "dst_{0}_{1}", [2, 3]),
        })
        tg = _make_tg(wiring_kind="grid", role_pairs=[("grid_out", "grid_in")])
        pairs = derive_topology_group(tg, src, dst)

        assert len(pairs) == 6  # 2 * 3
        # All pairs should be (src_{i}_{j}, dst_{i}_{j})
        src_pins = {s for s, _, _, _ in pairs}
        dst_pins = {d for _, d, _, _ in pairs}
        assert "src_0_0" in src_pins
        assert "dst_1_2" in dst_pins

    def test_multiple_role_pairs(self):
        """Multiple role_pairs in one topology group."""
        src = _make_contract({
            "a_out": _prefix_role("output", "wk", "a_out_", 3),
            "b_out": _prefix_role("output", "wk", "b_out_", 2),
        })
        dst = _make_contract({
            "a_in": _prefix_role("input", "wk", "a_in_", 3),
            "b_in": _prefix_role("input", "wk", "b_in_", 2),
        })
        tg = _make_tg(
            wiring_kind="wk",
            role_pairs=[("a_out", "a_in"), ("b_out", "b_in")],
        )
        pairs = derive_topology_group(tg, src, dst)
        assert len(pairs) == 5  # 3 + 2

    def test_missing_src_role_errors(self):
        """Reference to non-existent source role raises ValueError."""
        src = _make_contract({
            "data_out": _prefix_role("output", "wk", "out_", 3),
        })
        dst = _make_contract({
            "data_in": _prefix_role("input", "wk", "in_", 3),
        })
        tg = _make_tg(wiring_kind="wk", role_pairs=[("nonexistent", "data_in")])
        with pytest.raises(ValueError, match="source role 'nonexistent'"):
            derive_topology_group(tg, src, dst)

    def test_missing_dst_role_errors(self):
        """Reference to non-existent destination role raises ValueError."""
        src = _make_contract({
            "data_out": _prefix_role("output", "wk", "out_", 3),
        })
        dst = _make_contract({
            "data_in": _prefix_role("input", "wk", "in_", 3),
        })
        tg = _make_tg(wiring_kind="wk", role_pairs=[("data_out", "nonexistent")])
        with pytest.raises(ValueError, match="destination role 'nonexistent'"):
            derive_topology_group(tg, src, dst)

    def test_mixed_kinds_errors(self):
        """Pairing nd_tpl with prefix_array raises ValueError."""
        src = _make_contract({
            "data_out": _nd_tpl_role("output", "wk", "out_{0}", [3]),
        })
        dst = _make_contract({
            "data_in": _prefix_role("input", "wk", "in_", 3),
        })
        tg = _make_tg(wiring_kind="wk", role_pairs=[("data_out", "data_in")])
        with pytest.raises(ValueError, match="different kinds"):
            derive_topology_group(tg, src, dst)


# ─────────────────────────────────────────────────────────────────────────────
# Tests – instance-partition assignment
# ─────────────────────────────────────────────────────────────────────────────

class TestInstancePartition:
    """Tests for the instance_assign resolution strategy."""

    def test_basic_partition(self):
        """15 instances split into 3 partitions of 5."""
        # Producer: single output role per wiring_kind (each instance has csp_out)
        src = _make_contract({
            "csp_output": _scalar_role("output", "dt_stub", "csp_out"),
        })
        # Consumer: 3 partitioned input roles
        dst = _make_contract({
            "dt_mb1": _prefix_role("input", "dt_stub", "in_dt_mb1_", 5, partition="mb1"),
            "dt_mb2": _prefix_role("input", "dt_stub", "in_dt_mb2_", 5, partition="mb2"),
            "dt_mb3": _prefix_role("input", "dt_stub", "in_dt_mb3_", 5, partition="mb3"),
        })
        tg = _make_tg(
            name="dt_to_conc",
            wiring_kind="dt_stub",
            instance_assign=[
                InstanceAssign(instances=(0, 5), partition="mb1"),
                InstanceAssign(instances=(5, 10), partition="mb2"),
                InstanceAssign(instances=(10, 15), partition="mb3"),
            ],
        )
        pairs = derive_topology_group(tg, src, dst)

        assert len(pairs) == 15
        # Each pair has slot metadata
        for s, d, slot, meta in pairs:
            assert s == "csp_out"
            assert slot is not None
            assert meta is not None
            assert meta["kind"] == "topology_group"
            assert meta["group"] == "dt_to_conc"

        # Check slot assignments: instances 0-4 → mb1, 5-9 → mb2, 10-14 → mb3
        slots = [(slot, meta["partition"]) for _, _, slot, meta in pairs]
        for i in range(5):
            assert (i, "mb1") in slots
        for i in range(5, 10):
            assert (i, "mb2") in slots
        for i in range(10, 15):
            assert (i, "mb3") in slots

    def test_destination_port_names(self):
        """Verify destination port naming follows prefix + local_index."""
        src = _make_contract({
            "out": _scalar_role("output", "wk", "data_out"),
        })
        dst = _make_contract({
            "p_a": _prefix_role("input", "wk", "in_a_", 3, partition="a"),
            "p_b": _prefix_role("input", "wk", "in_b_", 2, partition="b"),
        })
        tg = _make_tg(
            wiring_kind="wk",
            instance_assign=[
                InstanceAssign(instances=(0, 3), partition="a"),
                InstanceAssign(instances=(3, 5), partition="b"),
            ],
        )
        pairs = derive_topology_group(tg, src, dst)

        dst_pins = [(d, slot) for _, d, slot, _ in pairs]
        # Partition "a": instances 0,1,2 → in_a_0, in_a_1, in_a_2
        assert ("in_a_0", 0) in dst_pins
        assert ("in_a_1", 1) in dst_pins
        assert ("in_a_2", 2) in dst_pins
        # Partition "b": instances 3,4 → in_b_0, in_b_1
        assert ("in_b_0", 3) in dst_pins
        assert ("in_b_1", 4) in dst_pins

    def test_count_mismatch_errors(self):
        """Instance range size != partition count raises ValueError."""
        src = _make_contract({
            "out": _scalar_role("output", "wk", "data_out"),
        })
        dst = _make_contract({
            "p_a": _prefix_role("input", "wk", "in_a_", 3, partition="a"),
        })
        tg = _make_tg(
            wiring_kind="wk",
            instance_assign=[
                InstanceAssign(instances=(0, 5), partition="a"),  # 5 != 3
            ],
        )
        with pytest.raises(ValueError, match="5 instances but coordinate 'a' has count=3"):
            derive_topology_group(tg, src, dst)

    def test_missing_partition_label_errors(self):
        """Consumer role without partition label raises ValueError."""
        src = _make_contract({
            "out": _scalar_role("output", "wk", "data_out"),
        })
        dst = _make_contract({
            "no_part": _prefix_role("input", "wk", "in_", 3),  # no partition=
        })
        tg = _make_tg(
            wiring_kind="wk",
            instance_assign=[
                InstanceAssign(instances=(0, 3), partition="a"),
            ],
        )
        with pytest.raises(ValueError, match="no partition/coordinates label"):
            derive_topology_group(tg, src, dst)

    def test_multiple_src_roles_errors(self):
        """instance_assign requires exactly one producer output role."""
        src = _make_contract({
            "out_a": _prefix_role("output", "wk", "a_", 3),
            "out_b": _prefix_role("output", "wk", "b_", 3),
        })
        dst = _make_contract({
            "in_x": _prefix_role("input", "wk", "x_", 3, partition="x"),
        })
        tg = _make_tg(
            wiring_kind="wk",
            instance_assign=[
                InstanceAssign(instances=(0, 3), partition="x"),
            ],
        )
        with pytest.raises(ValueError, match="exactly one producer output role"):
            derive_topology_group(tg, src, dst)

    def test_duplicate_partition_errors(self):
        """Two consumer roles with same partition label raises ValueError."""
        src = _make_contract({
            "out": _scalar_role("output", "wk", "data_out"),
        })
        dst = _make_contract({
            "in_a": _prefix_role("input", "wk", "in_a_", 3, partition="dup"),
            "in_b": _prefix_role("input", "wk", "in_b_", 3, partition="dup"),
        })
        tg = _make_tg(
            wiring_kind="wk",
            instance_assign=[
                InstanceAssign(instances=(0, 3), partition="dup"),
            ],
        )
        with pytest.raises(ValueError, match="duplicate coordinate 'dup'"):
            derive_topology_group(tg, src, dst)

    def test_structured_coordinates_resolve_like_partition(self):
        """A structured `coordinates:` mapping resolves instance_assign
        identically to the legacy scalar `partition:` string it's a
        superset of — this is the additive, backward-compatible path
        (audit gap #2)."""
        src = _make_contract({
            "out": _scalar_role("output", "wk", "data_out"),
        })
        dst = _make_contract({
            "lower": _prefix_role("input", "wk", "lower_", 5,
                                   coordinates={"sector": 1, "station": 1}),
            "upper": _prefix_role("input", "wk", "upper_", 5,
                                   coordinates={"sector": 2, "station": 1}),
        })
        tg = _make_tg(
            wiring_kind="wk",
            instance_assign=[
                InstanceAssign(instances=(0, 5), coordinates={"sector": 1, "station": 1}),
                InstanceAssign(instances=(5, 10), coordinates={"sector": 2, "station": 1}),
            ],
        )
        pairs = derive_topology_group(tg, src, dst)
        slots = {(dst_pin, meta["src_instance"]) for _, dst_pin, _, meta in pairs}
        assert ("lower_0", 0) in slots
        assert ("upper_0", 5) in slots
        assert len(pairs) == 10

    def test_structured_coordinates_ambiguity_is_rejected(self):
        """An instance_assign coordinate that doesn't match any consumer
        role's coordinates raises a clear diagnostic, same as the legacy
        partition-not-found case."""
        src = _make_contract({
            "out": _scalar_role("output", "wk", "data_out"),
        })
        dst = _make_contract({
            "lower": _prefix_role("input", "wk", "lower_", 5,
                                   coordinates={"sector": 1, "station": 1}),
        })
        tg = _make_tg(
            wiring_kind="wk",
            instance_assign=[
                InstanceAssign(instances=(0, 5), coordinates={"sector": 9, "station": 1}),
            ],
        )
        with pytest.raises(ValueError, match="not found in consumer contract"):
            derive_topology_group(tg, src, dst)

    def test_duplicate_structured_coordinates_errors(self):
        """Two consumer roles declaring the same structured coordinates
        are rejected, generalizing the legacy duplicate-partition check."""
        src = _make_contract({
            "out": _scalar_role("output", "wk", "data_out"),
        })
        dst = _make_contract({
            "in_a": _prefix_role("input", "wk", "in_a_", 3, coordinates={"sector": 1}),
            "in_b": _prefix_role("input", "wk", "in_b_", 3, coordinates={"sector": 1}),
        })
        tg = _make_tg(
            wiring_kind="wk",
            instance_assign=[
                InstanceAssign(instances=(0, 3), coordinates={"sector": 1}),
            ],
        )
        with pytest.raises(ValueError, match="duplicate coordinate 'sector=1'"):
            derive_topology_group(tg, src, dst)


# ─────────────────────────────────────────────────────────────────────────────
# Tests – auto-match (partition-aware)
# ─────────────────────────────────────────────────────────────────────────────

class TestAutoMatch:
    """Tests for the auto-match resolution strategy (no role_pairs, no instance_assign)."""

    def test_simple_1to1_match(self):
        """Single src and dst role with same wiring_kind → matched."""
        src = _make_contract({
            "data_out": _prefix_role("output", "processed", "out_", 4),
        })
        dst = _make_contract({
            "data_in": _prefix_role("input", "processed", "in_", 4),
        })
        tg = _make_tg(wiring_kind="processed")
        pairs = derive_topology_group(tg, src, dst)

        assert len(pairs) == 4
        for i in range(4):
            assert pairs[i] == (f"out_{i}", f"in_{i}", None, None)

    def test_partition_aware_match(self):
        """Roles with matching wiring_kind + partition get paired."""
        src = _make_contract({
            "alpha_out": _prefix_role("output", "wk", "alpha_out_", 3, partition="A"),
            "beta_out":  _prefix_role("output", "wk", "beta_out_", 2,  partition="B"),
        })
        dst = _make_contract({
            "alpha_in": _prefix_role("input", "wk", "alpha_in_", 3, partition="A"),
            "beta_in":  _prefix_role("input", "wk", "beta_in_", 2,  partition="B"),
        })
        tg = _make_tg(wiring_kind="wk")
        pairs = derive_topology_group(tg, src, dst)

        assert len(pairs) == 5  # 3 + 2
        src_pins = [s for s, _, _, _ in pairs]
        assert "alpha_out_0" in src_pins
        assert "beta_out_1" in src_pins

    def test_ambiguous_match_errors(self):
        """Multiple roles per wiring_kind without partitions → ValueError."""
        src = _make_contract({
            "a_out": _prefix_role("output", "wk", "a_", 3),
            "b_out": _prefix_role("output", "wk", "b_", 3),
        })
        dst = _make_contract({
            "a_in": _prefix_role("input", "wk", "x_", 3),
        })
        tg = _make_tg(wiring_kind="wk")
        with pytest.raises(ValueError, match="ambiguous match"):
            derive_topology_group(tg, src, dst)

    def test_no_matching_roles_yields_empty(self):
        """When no wiring_kind matches, no pairs are returned."""
        src = _make_contract({
            "data_out": _prefix_role("output", "type_a", "out_", 3),
        })
        dst = _make_contract({
            "data_in": _prefix_role("input", "type_b", "in_", 3),
        })
        tg = _make_tg(wiring_kind="type_a")
        # dst has no type_a roles, so nothing matches
        pairs = derive_topology_group(tg, src, dst)
        assert pairs == []

    def test_nd_tpl_auto_match(self):
        """nd_tpl roles can be auto-matched by wiring_kind + partition."""
        src = _make_contract({
            "grid_out": _nd_tpl_role("output", "grid", "g_out_{0}_{1}", [2, 2]),
        })
        dst = _make_contract({
            "grid_in": _nd_tpl_role("input", "grid", "g_in_{0}_{1}", [2, 2]),
        })
        tg = _make_tg(wiring_kind="grid")
        pairs = derive_topology_group(tg, src, dst)

        assert len(pairs) == 4  # 2 * 2
        src_pins = {s for s, _, _, _ in pairs}
        assert "g_out_0_0" in src_pins
        assert "g_out_1_1" in src_pins


# ─────────────────────────────────────────────────────────────────────────────
# Tests – supplementary port_map
# ─────────────────────────────────────────────────────────────────────────────

class TestSupplementaryPortMap:
    """Tests for supplementary scalar port_map within topology groups."""

    def test_port_map_prepended_to_pairs(self):
        """Explicit port_map pairs appear before contract-derived pairs."""
        src = _make_contract({
            "data_out": _prefix_role("output", "wk", "out_", 2),
        })
        dst = _make_contract({
            "data_in": _prefix_role("input", "wk", "in_", 2),
        })
        tg = _make_tg(
            wiring_kind="wk",
            port_map=[("ctrl_out", "ctrl_in")],
        )
        pairs = derive_topology_group(tg, src, dst)

        assert len(pairs) == 3  # 1 scalar + 2 prefix
        assert pairs[0] == ("ctrl_out", "ctrl_in", None, None)

    def test_port_map_only(self):
        """port_map works even with no wiring_kind."""
        src = _make_contract({})
        dst = _make_contract({})
        tg = _make_tg(port_map=[("a", "b"), ("c", "d")])
        pairs = derive_topology_group(tg, src, dst)
        assert len(pairs) == 2


# ─────────────────────────────────────────────────────────────────────────────
# Tests – wiring_kind filter
# ─────────────────────────────────────────────────────────────────────────────

class TestWiringKindFilter:
    """Tests that wiring_kind properly filters roles."""

    def test_filter_excludes_other_wiring_kinds(self):
        """Only roles matching the topology group's wiring_kind participate."""
        src = _make_contract({
            "dt_out": _prefix_role("output", "dt_stub", "dt_out_", 3),
            "cfg_out": _prefix_role("output", "cfg", "cfg_out_", 2),
        })
        dst = _make_contract({
            "dt_in": _prefix_role("input", "dt_stub", "dt_in_", 3),
            "cfg_in": _prefix_role("input", "cfg", "cfg_in_", 2),
        })
        tg = _make_tg(wiring_kind="dt_stub")
        pairs = derive_topology_group(tg, src, dst)

        assert len(pairs) == 3
        for s, d, _, _ in pairs:
            assert s.startswith("dt_out_")
            assert d.startswith("dt_in_")

    def test_no_filter_matches_all(self):
        """When wiring_kind is None, all roles participate."""
        src = _make_contract({
            "a_out": _prefix_role("output", "wk_a", "a_", 2),
            "b_out": _prefix_role("output", "wk_b", "b_", 3),
        })
        dst = _make_contract({
            "a_in": _prefix_role("input", "wk_a", "a_in_", 2),
            "b_in": _prefix_role("input", "wk_b", "b_in_", 3),
        })
        tg = _make_tg(wiring_kind=None)
        pairs = derive_topology_group(tg, src, dst)

        assert len(pairs) == 5  # 2 + 3


# ─────────────────────────────────────────────────────────────────────────────
# Tests – scatter/gather (prefix_array ↔ scalar)
# ─────────────────────────────────────────────────────────────────────────────

class TestScatterGather:
    """Tests for array→scalar (scatter) and scalar→array (gather) patterns."""

    def test_scatter_array_to_scalar(self):
        """prefix_array src → scalar dst produces scatter pairs."""
        src = _make_contract({
            "rpc_out": _prefix_role("output", "rpc", "out_rpc_", 5),
        })
        dst = _make_contract({
            "din": _scalar_role("input", "generic", "din"),
        })
        tg = _make_tg(role_pairs=[("rpc_out", "din")])
        pairs = derive_topology_group(tg, src, dst)

        assert len(pairs) == 5
        for i, (s, d, slot, meta) in enumerate(pairs):
            assert s == f"out_rpc_{i}"
            assert d == "din"
            assert slot is None
            assert meta == {"kind": "scatter", "target_instance": i}

    def test_gather_scalar_to_array(self):
        """scalar src → prefix_array dst produces gather pairs with slot.

        Gather pairs' meta is tagged symmetrically with scatter's
        existing {"kind": "scatter", ...} tag — purely additive evidence,
        doesn't change slot-based instance selection (still the sole
        determinant of wiring, see matcher.py's replication loop).
        """
        src = _make_contract({
            "dout": _scalar_role("output", "generic", "dout"),
        })
        dst = _make_contract({
            "rpc_in": _prefix_role("input", "rpc", "in_rpc_", 5),
        })
        tg = _make_tg(role_pairs=[("dout", "rpc_in")])
        pairs = derive_topology_group(tg, src, dst)

        assert len(pairs) == 5
        for i, (s, d, slot, meta) in enumerate(pairs):
            assert s == "dout"
            assert d == f"in_rpc_{i}"
            assert slot == i
            assert meta == {"kind": "gather", "target_index": i}

    def test_role_pairs_use_all_roles_not_filtered(self):
        """role_pairs look up from ALL roles, not just wiring_kind-filtered."""
        src = _make_contract({
            "data_out": _prefix_role("output", "wk_a", "out_", 3),
        })
        dst = _make_contract({
            "data_in": _scalar_role("input", "wk_b", "din"),  # different wk
        })
        # wiring_kind filter is "wk_a" but dst has "wk_b" — role_pairs bypasses
        tg = _make_tg(wiring_kind="wk_a", role_pairs=[("data_out", "data_in")])
        pairs = derive_topology_group(tg, src, dst)

        assert len(pairs) == 3
        assert pairs[0][0] == "out_0"
        assert pairs[0][1] == "din"

    def test_scalar_to_scalar_role_pairs(self):
        """scalar→scalar role pairing across different wiring_kinds."""
        src = _make_contract({
            "cfg_out": _scalar_role("output", "cfg_word", "cfg64_o"),
        })
        dst = _make_contract({
            "cfg_in": _scalar_role("input", "cfg_word", "cfg64"),
        })
        tg = _make_tg(role_pairs=[("cfg_out", "cfg_in")])
        pairs = derive_topology_group(tg, src, dst)

        assert len(pairs) == 1
        assert pairs[0] == ("cfg64_o", "cfg64", None, None)
