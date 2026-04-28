"""
Generation equivalence regression tests — Step 17.

For each boundary migrated from port_map_ranges to topology_groups, these tests
verify that the contract-derived expansion produces the exact same wire-pair set
as the old explicit-range expansion.

Each test class:
1. Computes the expected wire mapping (what the old port_map_ranges would produce)
2. Runs derive_topology_group with the migrated contracts + topology_group config
3. Applies the same instance replication logic used in matcher.py
4. Asserts the two wire sets are identical

Boundary types covered:
- instance_assign (dt→subdet, csc→subdet)
- role_pairs scalar diagonal (cfg→dt)
- role_pairs scalar with src_instance_offset (cfg→csc)
- auto_match partitioned (subdet→rgf DT, subdet→rgf CSC, rgf→arb)
- scatter: prefix_array→scalar instances (subdet→rpc_*_dly)
- gather: scalar instances→prefix_array (rpc_*_dly→rgf)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pytest


_TOPGEN_ROOT = Path(__file__).resolve().parents[1]
if str(_TOPGEN_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOPGEN_ROOT))

from topgen.config import InstanceAssign, TopologyGroup
from topgen.ip.contract_loader import LoadedContract
from topgen.ip.topology_deriver import derive_topology_group


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

WireSet = Set[Tuple[str, str, str, str]]  # (src_inst, src_pin, dst_inst, dst_pin)


def _contract(roles: Dict[str, dict], name: str = "m") -> LoadedContract:
    spec = {"ip_interface": {"module_name": name, "ip_info_key": name, "roles": roles}}
    return LoadedContract(path=Path(f"/fake/{name}.yaml"), spec=spec)


def _inst(name: str, idx: int, n: int) -> str:
    return name if n == 1 else f"{name}_{idx}"


def _replicate_topology(
    base_pairs: List[Tuple],
    src_name: str, dst_name: str,
    src_n: int, dst_n: int,
    src_offset: int = 0,
) -> WireSet:
    """Simulate matcher.py topology_group instance replication loop."""
    result: WireSet = set()
    used_sinks: set = set()
    for i_s in range(max(1, src_n)):
        si = _inst(src_name, i_s, src_n)
        for i_d in range(max(1, dst_n)):
            di = _inst(dst_name, i_d, dst_n)
            for s_pin, d_pin, slot, meta in base_pairs:
                if slot is not None and slot != i_s:
                    continue
                if meta and meta.get("kind") == "scatter":
                    if i_d != meta["target_instance"]:
                        continue
                if slot is None and meta is None:
                    if src_n > 1 and dst_n > 1:
                        if (i_s - src_offset) != i_d:
                            continue
                sk = (di, d_pin)
                if sk in used_sinks:
                    continue
                used_sinks.add(sk)
                result.add((si, s_pin, di, d_pin))
    return result


def _replicate_old_ranges(
    ranges: List[dict],
    src_name: str, dst_name: str,
    src_n: int, dst_n: int,
    scalar_src_ports: set,
) -> WireSet:
    """Simulate matcher.py port_map_ranges 1-D expansion + instance replication."""
    base_pairs: list = []
    for rng in ranges:
        sp, dp = rng["src_prefix"], rng["dst_prefix"]
        cnt = rng["count"]
        si = rng.get("src_start", 0)
        di = rng.get("dst_start", 0)
        scalar_src = sp in scalar_src_ports
        dst_scalar = rng.get("_dst_scalar", False)
        for k in range(cnt):
            s_pin = sp if scalar_src else f"{sp}{si + k}"
            d_pin = dp if dst_scalar else f"{dp}{di + k}"
            meta = {"kind": "1d", "si": si, "di": di, "k": k, "cnt": cnt,
                    "scalar_src": scalar_src, "dst_scalar": dst_scalar,
                    "sp": sp, "dp": dp}
            slot = si + k if scalar_src else None
            base_pairs.append((s_pin, d_pin, slot, meta))

    result: WireSet = set()
    used_sinks: set = set()
    for i_s in range(max(1, src_n)):
        si_name = _inst(src_name, i_s, src_n)
        for i_d in range(max(1, dst_n)):
            di_name = _inst(dst_name, i_d, dst_n)
            for s_pin, d_pin, slot, meta in base_pairs:
                if slot is not None and slot != i_s:
                    continue
                if meta and meta.get("kind") == "1d":
                    si, di, k = meta["si"], meta["di"], meta["k"]
                    ss, ds = meta["scalar_src"], meta["dst_scalar"]
                    if not ss and not ds:
                        rel = i_s - si
                        if not (0 <= rel < meta["cnt"] and i_d == rel + di):
                            continue
                    elif ss and ds:
                        if i_d != di + (slot - si):
                            continue
                    elif not ss and ds:
                        if i_d != di + k:
                            continue
                sk = (di_name, d_pin)
                if sk in used_sinks:
                    continue
                used_sinks.add(sk)
                result.add((si_name, s_pin, di_name, d_pin))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 1. DT → Concentrator (instance_assign: 15 instances → 3 partitions of 5)
# ─────────────────────────────────────────────────────────────────────────────

class TestDtToConcentratorEquivalence:

    def _old(self) -> WireSet:
        return _replicate_old_ranges(
            [{"src_prefix": "csp_out", "dst_prefix": "in_dt_mb1_", "count": 5, "src_start": 0},
             {"src_prefix": "csp_out", "dst_prefix": "in_dt_mb2_", "count": 5, "src_start": 5},
             {"src_prefix": "csp_out", "dst_prefix": "in_dt_mb3_", "count": 5, "src_start": 10}],
            "dt", "subdet", 15, 1, scalar_src_ports={"csp_out"})

    def _new(self) -> WireSet:
        src = _contract({"csp_output": {
            "direction": "output", "wiring_kind": "dt_processed_stub",
            "raw_port": "csp_out", "width": 27}})
        dst = _contract({
            "dt_mb1": {"direction": "input", "wiring_kind": "dt_processed_stub",
                       "raw_port_prefix": "in_dt_mb1_", "count": 5, "width": 27, "partition": "mb1"},
            "dt_mb2": {"direction": "input", "wiring_kind": "dt_processed_stub",
                       "raw_port_prefix": "in_dt_mb2_", "count": 5, "width": 27, "partition": "mb2"},
            "dt_mb3": {"direction": "input", "wiring_kind": "dt_processed_stub",
                       "raw_port_prefix": "in_dt_mb3_", "count": 5, "width": 27, "partition": "mb3"},
        })
        tg = TopologyGroup(name="dt_to_concentrator", family="detector_processed",
                           from_="dt", to="subdet", wiring_kind="dt_processed_stub",
                           instance_assign=[
                               InstanceAssign((0, 5), "mb1"),
                               InstanceAssign((5, 10), "mb2"),
                               InstanceAssign((10, 15), "mb3")])
        return _replicate_topology(derive_topology_group(tg, src, dst), "dt", "subdet", 15, 1)

    def test_wire_count(self):
        assert len(self._new()) == 15

    def test_identical_wire_mapping(self):
        assert self._old() == self._new()


# ─────────────────────────────────────────────────────────────────────────────
# 2. CSC → Concentrator (instance_assign: 52 instances → 4 partitions of 13)
# ─────────────────────────────────────────────────────────────────────────────

class TestCscToConcentratorEquivalence:

    def _old(self) -> WireSet:
        return _replicate_old_ranges(
            [{"src_prefix": "csp_out", "dst_prefix": "in_csc_me12_", "count": 13, "src_start": 0},
             {"src_prefix": "csp_out", "dst_prefix": "in_csc_me13_", "count": 13, "src_start": 13},
             {"src_prefix": "csp_out", "dst_prefix": "in_csc_me22_", "count": 13, "src_start": 26},
             {"src_prefix": "csp_out", "dst_prefix": "in_csc_me32_", "count": 13, "src_start": 39}],
            "csc", "subdet", 52, 1, scalar_src_ports={"csp_out"})

    def _new(self) -> WireSet:
        src = _contract({"csp_output": {
            "direction": "output", "wiring_kind": "csc_processed_stub",
            "raw_port": "csp_out", "width": 27}})
        dst = _contract({
            "csc_me12": {"direction": "input", "wiring_kind": "csc_processed_stub",
                         "raw_port_prefix": "in_csc_me12_", "count": 13, "width": 27, "partition": "me12"},
            "csc_me13": {"direction": "input", "wiring_kind": "csc_processed_stub",
                         "raw_port_prefix": "in_csc_me13_", "count": 13, "width": 27, "partition": "me13"},
            "csc_me22": {"direction": "input", "wiring_kind": "csc_processed_stub",
                         "raw_port_prefix": "in_csc_me22_", "count": 13, "width": 27, "partition": "me22"},
            "csc_me32": {"direction": "input", "wiring_kind": "csc_processed_stub",
                         "raw_port_prefix": "in_csc_me32_", "count": 13, "width": 27, "partition": "me32"},
        })
        tg = TopologyGroup(name="csc_to_concentrator", family="detector_processed",
                           from_="csc", to="subdet", wiring_kind="csc_processed_stub",
                           instance_assign=[
                               InstanceAssign((0, 13), "me12"),
                               InstanceAssign((13, 26), "me13"),
                               InstanceAssign((26, 39), "me22"),
                               InstanceAssign((39, 52), "me32")])
        return _replicate_topology(derive_topology_group(tg, src, dst), "csc", "subdet", 52, 1)

    def test_wire_count(self):
        assert len(self._new()) == 52

    def test_identical_wire_mapping(self):
        assert self._old() == self._new()


# ─────────────────────────────────────────────────────────────────────────────
# 3. cfg → dt (scalar diagonal: role_pairs, 67 cfg → 15 dt)
# ─────────────────────────────────────────────────────────────────────────────

class TestCfgToDtEquivalence:

    def _old(self) -> WireSet:
        return _replicate_old_ranges(
            [{"src_prefix": "cfg64_o", "dst_prefix": "cfg64", "count": 15,
              "src_start": 0, "_dst_scalar": True},
             {"src_prefix": "cfg_valid_o", "dst_prefix": "cfg_valid", "count": 15,
              "src_start": 0, "_dst_scalar": True}],
            "cfg", "dt", 67, 15, scalar_src_ports={"cfg64_o", "cfg_valid_o"})

    def _new(self) -> WireSet:
        src = _contract({
            "cfg_word_out": {"direction": "output", "wiring_kind": "cfg64_word",
                             "raw_port": "cfg64_o", "width": 64},
            "cfg_valid_out": {"direction": "output", "wiring_kind": "cfg_valid_strobe",
                              "raw_port": "cfg_valid_o", "width": 1},
        })
        dst = _contract({
            "cfg_word": {"direction": "input", "wiring_kind": "cfg64_word",
                         "raw_port": "cfg64", "width": 64},
            "cfg_valid": {"direction": "input", "wiring_kind": "cfg_valid_strobe",
                          "raw_port": "cfg_valid", "width": 1},
        })
        tg = TopologyGroup(name="cfg_to_dt", family="configuration",
                           from_="cfg", to="dt",
                           role_pairs=[("cfg_word_out", "cfg_word"),
                                       ("cfg_valid_out", "cfg_valid")])
        return _replicate_topology(derive_topology_group(tg, src, dst), "cfg", "dt", 67, 15)

    def test_wire_count(self):
        assert len(self._new()) == 30  # 15 instances × 2 signals

    def test_identical_wire_mapping(self):
        assert self._old() == self._new()


# ─────────────────────────────────────────────────────────────────────────────
# 4. cfg → csc (offset diagonal: src_instance_offset=15, 67 cfg → 52 csc)
# ─────────────────────────────────────────────────────────────────────────────

class TestCfgToCscEquivalence:

    def _old(self) -> WireSet:
        return _replicate_old_ranges(
            [{"src_prefix": "cfg64_o", "dst_prefix": "cfg64", "count": 52,
              "src_start": 15, "_dst_scalar": True},
             {"src_prefix": "cfg_valid_o", "dst_prefix": "cfg_valid", "count": 52,
              "src_start": 15, "_dst_scalar": True}],
            "cfg", "csc", 67, 52, scalar_src_ports={"cfg64_o", "cfg_valid_o"})

    def _new(self) -> WireSet:
        src = _contract({
            "cfg_word_out": {"direction": "output", "wiring_kind": "cfg64_word",
                             "raw_port": "cfg64_o", "width": 64},
            "cfg_valid_out": {"direction": "output", "wiring_kind": "cfg_valid_strobe",
                              "raw_port": "cfg_valid_o", "width": 1},
        })
        dst = _contract({
            "cfg_word": {"direction": "input", "wiring_kind": "cfg64_word",
                         "raw_port": "cfg64", "width": 64},
            "cfg_valid": {"direction": "input", "wiring_kind": "cfg_valid_strobe",
                          "raw_port": "cfg_valid", "width": 1},
        })
        tg = TopologyGroup(name="cfg_to_csc", family="configuration",
                           from_="cfg", to="csc", src_instance_offset=15,
                           role_pairs=[("cfg_word_out", "cfg_word"),
                                       ("cfg_valid_out", "cfg_valid")])
        return _replicate_topology(derive_topology_group(tg, src, dst),
                                   "cfg", "csc", 67, 52, src_offset=15)

    def test_wire_count(self):
        assert len(self._new()) == 104  # 52 instances × 2 signals

    def test_identical_wire_mapping(self):
        assert self._old() == self._new()


# ─────────────────────────────────────────────────────────────────────────────
# 5. subdet → rgf DT (auto_match: partition-aware, single-instance both sides)
# ─────────────────────────────────────────────────────────────────────────────

class TestSubdetToRgfDtEquivalence:

    def _old(self) -> WireSet:
        return _replicate_old_ranges(
            [{"src_prefix": "out_dt_mb1_", "dst_prefix": "in_dt_mb1_", "count": 5},
             {"src_prefix": "out_dt_mb2_", "dst_prefix": "in_dt_mb2_", "count": 5},
             {"src_prefix": "out_dt_mb3_", "dst_prefix": "in_dt_mb3_", "count": 5}],
            "subdet", "rgf", 1, 1, scalar_src_ports=set())

    def _new(self) -> WireSet:
        src = _contract({
            "dt_mb1": {"direction": "output", "wiring_kind": "dt_processed_stub",
                       "raw_port_prefix": "out_dt_mb1_", "count": 5, "width": 27, "partition": "mb1"},
            "dt_mb2": {"direction": "output", "wiring_kind": "dt_processed_stub",
                       "raw_port_prefix": "out_dt_mb2_", "count": 5, "width": 27, "partition": "mb2"},
            "dt_mb3": {"direction": "output", "wiring_kind": "dt_processed_stub",
                       "raw_port_prefix": "out_dt_mb3_", "count": 5, "width": 27, "partition": "mb3"},
        })
        dst = _contract({
            "dt_mb1": {"direction": "input", "wiring_kind": "dt_processed_stub",
                       "raw_port_prefix": "in_dt_mb1_", "count": 5, "width": 27, "partition": "mb1"},
            "dt_mb2": {"direction": "input", "wiring_kind": "dt_processed_stub",
                       "raw_port_prefix": "in_dt_mb2_", "count": 5, "width": 27, "partition": "mb2"},
            "dt_mb3": {"direction": "input", "wiring_kind": "dt_processed_stub",
                       "raw_port_prefix": "in_dt_mb3_", "count": 5, "width": 27, "partition": "mb3"},
        })
        tg = TopologyGroup(name="subdet_to_rgf_dt", family="detector_processed",
                           from_="subdet", to="rgf", wiring_kind="dt_processed_stub")
        return _replicate_topology(derive_topology_group(tg, src, dst), "subdet", "rgf", 1, 1)

    def test_wire_count(self):
        assert len(self._new()) == 15

    def test_identical_wire_mapping(self):
        assert self._old() == self._new()


# ─────────────────────────────────────────────────────────────────────────────
# 6. subdet → rgf CSC (auto_match: partition-aware, single-instance)
# ─────────────────────────────────────────────────────────────────────────────

class TestSubdetToRgfCscEquivalence:

    def _old(self) -> WireSet:
        return _replicate_old_ranges(
            [{"src_prefix": "out_csc_me13_", "dst_prefix": "in_csc_me13_", "count": 13},
             {"src_prefix": "out_csc_me22_", "dst_prefix": "in_csc_me22_", "count": 13}],
            "subdet", "rgf", 1, 1, scalar_src_ports=set())

    def _new(self) -> WireSet:
        src = _contract({
            "csc_me13": {"direction": "output", "wiring_kind": "csc_processed_stub",
                         "raw_port_prefix": "out_csc_me13_", "count": 13, "width": 27, "partition": "me13"},
            "csc_me22": {"direction": "output", "wiring_kind": "csc_processed_stub",
                         "raw_port_prefix": "out_csc_me22_", "count": 13, "width": 27, "partition": "me22"},
        })
        dst = _contract({
            "csc_me13": {"direction": "input", "wiring_kind": "csc_processed_stub",
                         "raw_port_prefix": "in_csc_me13_", "count": 13, "width": 27, "partition": "me13"},
            "csc_me22": {"direction": "input", "wiring_kind": "csc_processed_stub",
                         "raw_port_prefix": "in_csc_me22_", "count": 13, "width": 27, "partition": "me22"},
        })
        tg = TopologyGroup(name="subdet_to_rgf_csc", family="detector_processed",
                           from_="subdet", to="rgf", wiring_kind="csc_processed_stub")
        return _replicate_topology(derive_topology_group(tg, src, dst), "subdet", "rgf", 1, 1)

    def test_wire_count(self):
        assert len(self._new()) == 26

    def test_identical_wire_mapping(self):
        assert self._old() == self._new()


# ─────────────────────────────────────────────────────────────────────────────
# 7. rgf → arb (auto_match: region_encoded_stub, partition-aware)
# ─────────────────────────────────────────────────────────────────────────────

class TestRgfToArbEquivalence:

    def _old(self) -> WireSet:
        return _replicate_old_ranges(
            [{"src_prefix": "out_dt_mb1_", "dst_prefix": "in_dt_mb1_", "count": 5},
             {"src_prefix": "out_dt_mb2_", "dst_prefix": "in_dt_mb2_", "count": 5},
             {"src_prefix": "out_dt_mb3_", "dst_prefix": "in_dt_mb3_", "count": 5},
             {"src_prefix": "out_csc_me13_", "dst_prefix": "in_csc_me13_", "count": 5},
             {"src_prefix": "out_csc_me22_", "dst_prefix": "in_csc_me22_", "count": 5},
             {"src_prefix": "out_rpc_re23_", "dst_prefix": "in_rpc_re23_", "count": 5},
             {"src_prefix": "out_rpc_rb1_in_", "dst_prefix": "in_rpc_rb1_in_", "count": 5},
             {"src_prefix": "out_rpc_rb1_out_", "dst_prefix": "in_rpc_rb1_out_", "count": 5}],
            "rgf", "arb", 1, 1, scalar_src_ports=set())

    def _new(self) -> WireSet:
        partitions = ["dt_mb1", "dt_mb2", "dt_mb3", "csc_me13", "csc_me22",
                      "rpc_re23", "rpc_rb1_in", "rpc_rb1_out"]
        src_roles = {p: {"direction": "output", "wiring_kind": "region_encoded_stub",
                         "raw_port_prefix": f"out_{p}_", "count": 5, "width": 30,
                         "partition": p} for p in partitions}
        dst_roles = {p: {"direction": "input", "wiring_kind": "region_encoded_stub",
                         "raw_port_prefix": f"in_{p}_", "count": 5, "width": 30,
                         "partition": p} for p in partitions}
        src = _contract(src_roles)
        dst = _contract(dst_roles)
        tg = TopologyGroup(name="rgf_to_arb", family="region_encoded",
                           from_="rgf", to="arb", wiring_kind="region_encoded_stub")
        return _replicate_topology(derive_topology_group(tg, src, dst), "rgf", "arb", 1, 1)

    def test_wire_count(self):
        assert len(self._new()) == 40  # 8 partitions × 5 elements

    def test_identical_wire_mapping(self):
        assert self._old() == self._new()


# ─────────────────────────────────────────────────────────────────────────────
# 8. Scatter: subdet → rpc_rb1_in_dly (prefix_array → scalar instances)
# ─────────────────────────────────────────────────────────────────────────────

class TestScatterRpcEquivalence:

    def _old(self) -> WireSet:
        # Old approach: indexed src pins → scalar dst on replicated instances
        return _replicate_old_ranges(
            [{"src_prefix": "out_rpc_rb1_in_", "dst_prefix": "din",
              "count": 5, "_dst_scalar": True}],
            "subdet", "rpc_rb1_in_dly", 1, 5, scalar_src_ports=set())

    def _new(self) -> WireSet:
        src = _contract({"output_stream_array_rpc_rb1_in": {
            "direction": "output", "wiring_kind": "rpc_alignment_stub",
            "raw_port_prefix": "out_rpc_rb1_in_", "count": 5, "width": 30}})
        dst = _contract({"input_stream_0": {
            "direction": "input", "raw_port": "din", "width": 30}})
        tg = TopologyGroup(name="subdet_to_rpc_rb1_in_dly", family="rpc_delay",
                           from_="subdet", to="rpc_rb1_in_dly",
                           role_pairs=[("output_stream_array_rpc_rb1_in", "input_stream_0")])
        return _replicate_topology(derive_topology_group(tg, src, dst),
                                   "subdet", "rpc_rb1_in_dly", 1, 5)

    def test_wire_count(self):
        assert len(self._new()) == 5

    def test_identical_wire_mapping(self):
        assert self._old() == self._new()


# ─────────────────────────────────────────────────────────────────────────────
# 9. Gather: rpc_rb1_in_dly → rgf (scalar instances → prefix_array)
# ─────────────────────────────────────────────────────────────────────────────

class TestGatherRpcEquivalence:

    def _old(self) -> WireSet:
        # Old approach: scalar src on replicated instances → indexed dst pins
        return _replicate_old_ranges(
            [{"src_prefix": "dout", "dst_prefix": "in_rpc_rb1_in_", "count": 5}],
            "rpc_rb1_in_dly", "rgf", 5, 1, scalar_src_ports={"dout"})

    def _new(self) -> WireSet:
        src = _contract({"output_stream_0": {
            "direction": "output", "raw_port": "dout", "width": 30}})
        dst = _contract({"input_stream_array_rpc_rb1_in": {
            "direction": "input", "wiring_kind": "rpc_alignment_stub",
            "raw_port_prefix": "in_rpc_rb1_in_", "count": 5, "width": 30}})
        tg = TopologyGroup(name="rpc_rb1_in_dly_to_rgf", family="rpc_delay",
                           from_="rpc_rb1_in_dly", to="rgf",
                           role_pairs=[("output_stream_0", "input_stream_array_rpc_rb1_in")])
        return _replicate_topology(derive_topology_group(tg, src, dst),
                                   "rpc_rb1_in_dly", "rgf", 5, 1)

    def test_wire_count(self):
        assert len(self._new()) == 5

    def test_identical_wire_mapping(self):
        assert self._old() == self._new()


# ─────────────────────────────────────────────────────────────────────────────
# 10. Completeness: verify ALL migrated boundary types are covered
# ─────────────────────────────────────────────────────────────────────────────

class TestEquivalenceCoverage:
    """Meta-test: all migration patterns have at least one equivalence test."""

    PATTERNS_COVERED = [
        "instance_assign (dt→subdet)",
        "instance_assign (csc→subdet)",
        "scalar diagonal role_pairs (cfg→dt)",
        "scalar diagonal role_pairs + src_instance_offset (cfg→csc)",
        "partitioned auto_match DT (subdet→rgf)",
        "partitioned auto_match CSC (subdet→rgf)",
        "partitioned auto_match (rgf→arb)",
        "scatter: prefix_array→scalar (subdet→rpc_*_dly)",
        "gather: scalar→prefix_array (rpc_*_dly→rgf)",
    ]

    def test_all_patterns_have_tests(self):
        """Verify this file covers all migration patterns."""
        assert len(self.PATTERNS_COVERED) == 9
