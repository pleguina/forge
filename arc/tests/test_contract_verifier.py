"""
Tests for ContractVerifier role-shape dispatch and validation.

Covers all three role shapes: scalar, prefix-array, and N-D template.
Uses synthetic ip_info and contract YAML written to temp files.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import List

import pytest
import yaml


_TOPGEN_ROOT = Path(__file__).resolve().parents[1]
if str(_TOPGEN_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOPGEN_ROOT))

from topgen.ip.contract_verifier import ContractVerifier, VerifyResult


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _write(tmp_path: Path, name: str, data: dict) -> Path:
    """Write a dict as YAML to a temp file and return the path."""
    p = tmp_path / name
    p.write_text(yaml.dump(data, default_flow_style=False))
    return p


def _ip_info_with_ports(ports: List[dict]) -> dict:
    """Build a minimal ip_info.yaml dict with one IP key 'test_ip'."""
    return {"test_ip": {"ports": ports}}


def _contract(roles: dict, **extra) -> dict:
    """Build a minimal contract dict."""
    iface = {
        "module_name": "test_module",
        "ip_info_key": "test_ip",
        "source_type": "hls",
        "roles": roles,
        **extra,
    }
    return {"ip_interface": iface}


def _verify(tmp_path: Path, ip_info: dict, contract: dict) -> VerifyResult:
    ip_path = _write(tmp_path, "ip_info.yaml", ip_info)
    ct_path = _write(tmp_path, "test.interface.yaml", contract)
    v = ContractVerifier(ip_path, ct_path)
    return v.verify()


# ─────────────────────────────────────────────────────────────────────────────
# Scalar role tests
# ─────────────────────────────────────────────────────────────────────────────

class TestScalarRole:
    def test_valid_scalar_role(self, tmp_path):
        ip = _ip_info_with_ports([
            {"name": "ap_clk", "direction": "IN", "width": 1, "type": "wire"},
            {"name": "ap_rst", "direction": "IN", "width": 1, "type": "wire"},
        ])
        ct = _contract({
            "clock_primary": {"raw_port": "ap_clk", "direction": "input", "width": 1},
            "reset_primary": {"raw_port": "ap_rst", "direction": "input", "width": 1},
        })
        result = _verify(tmp_path, ip, ct)
        assert result.passed
        assert len(result.errors) == 0

    def test_missing_raw_port_field(self, tmp_path):
        ip = _ip_info_with_ports([
            {"name": "ap_clk", "direction": "IN", "width": 1, "type": "wire"},
            {"name": "ap_rst", "direction": "IN", "width": 1, "type": "wire"},
        ])
        ct = _contract({
            "clock_primary": {"raw_port": "ap_clk", "direction": "input", "width": 1},
            "reset_primary": {"raw_port": "ap_rst", "direction": "input", "width": 1},
            "bad_role": {"direction": "input", "width": 32},  # missing raw_port
        })
        result = _verify(tmp_path, ip, ct)
        assert not result.passed
        assert any("missing 'raw_port'" in str(e) for e in result.errors)

    def test_wrong_direction(self, tmp_path):
        ip = _ip_info_with_ports([
            {"name": "ap_clk", "direction": "IN", "width": 1, "type": "wire"},
            {"name": "ap_rst", "direction": "IN", "width": 1, "type": "wire"},
            {"name": "data_out", "direction": "OUT", "width": 32, "type": "wire"},
        ])
        ct = _contract({
            "clock_primary": {"raw_port": "ap_clk", "direction": "input", "width": 1},
            "reset_primary": {"raw_port": "ap_rst", "direction": "input", "width": 1},
            "some_input": {"raw_port": "data_out", "direction": "input", "width": 32},
        })
        result = _verify(tmp_path, ip, ct)
        assert not result.passed
        assert any("direction" in str(e) for e in result.errors)


# ─────────────────────────────────────────────────────────────────────────────
# Prefix-array role tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPrefixArrayRole:
    def test_valid_array_with_flag(self, tmp_path):
        ip = _ip_info_with_ports([
            {"name": "ap_clk", "direction": "IN", "width": 1, "type": "wire"},
            {"name": "ap_rst", "direction": "IN", "width": 1, "type": "wire"},
            {"name": "in_data_0", "direction": "IN", "width": 44, "type": "wire"},
            {"name": "in_data_1", "direction": "IN", "width": 44, "type": "wire"},
            {"name": "in_data_2", "direction": "IN", "width": 44, "type": "wire"},
        ])
        ct = _contract({
            "clock_primary": {"raw_port": "ap_clk", "direction": "input", "width": 1},
            "reset_primary": {"raw_port": "ap_rst", "direction": "input", "width": 1},
            "input_array": {
                "array": True,
                "raw_port_prefix": "in_data_",
                "count": 3,
                "direction": "input",
                "width": 44,
            },
        })
        result = _verify(tmp_path, ip, ct)
        assert result.passed

    def test_valid_prefix_array_without_array_flag(self, tmp_path):
        """gen_nd prefix-array roles omit array: true — verifier must still route correctly."""
        ip = _ip_info_with_ports([
            {"name": "ap_clk", "direction": "IN", "width": 1, "type": "wire"},
            {"name": "ap_rst", "direction": "IN", "width": 1, "type": "wire"},
            {"name": "out_fired_0", "direction": "OUT", "width": 1, "type": "wire"},
            {"name": "out_fired_1", "direction": "OUT", "width": 1, "type": "wire"},
            {"name": "out_fired_2", "direction": "OUT", "width": 1, "type": "wire"},
        ])
        ct = _contract({
            "clock_primary": {"raw_port": "ap_clk", "direction": "input", "width": 1},
            "reset_primary": {"raw_port": "ap_rst", "direction": "input", "width": 1},
            "gen_nd_out_fired": {
                "raw_port_prefix": "out_fired_",
                "count": 3,
                "direction": "output",
                "width": 1,
                "wiring_kind": "test_fired",
            },
        })
        result = _verify(tmp_path, ip, ct)
        assert result.passed

    def test_array_count_too_high(self, tmp_path):
        ip = _ip_info_with_ports([
            {"name": "ap_clk", "direction": "IN", "width": 1, "type": "wire"},
            {"name": "ap_rst", "direction": "IN", "width": 1, "type": "wire"},
            {"name": "in_x_0", "direction": "IN", "width": 8, "type": "wire"},
        ])
        ct = _contract({
            "clock_primary": {"raw_port": "ap_clk", "direction": "input", "width": 1},
            "reset_primary": {"raw_port": "ap_rst", "direction": "input", "width": 1},
            "my_array": {
                "array": True,
                "raw_port_prefix": "in_x_",
                "count": 5,
                "direction": "input",
                "width": 8,
            },
        })
        result = _verify(tmp_path, ip, ct)
        assert not result.passed
        assert any("Expected 5 ports" in str(e) for e in result.errors)


# ─────────────────────────────────────────────────────────────────────────────
# N-D template role tests
# ─────────────────────────────────────────────────────────────────────────────

class TestNdTplRole:
    def test_valid_nd_2d_role(self, tmp_path):
        """A valid 2-D template role with matching ports in ip_info."""
        ports = [
            {"name": "ap_clk", "direction": "IN", "width": 1, "type": "wire"},
            {"name": "ap_rst", "direction": "IN", "width": 1, "type": "wire"},
        ]
        # Generate ports for a 3×4 grid: out_layer{0}_{1}
        for i in range(3):
            for j in range(4):
                ports.append({
                    "name": f"out_layer{i}_{j}",
                    "direction": "OUT",
                    "width": 40,
                    "type": "wire",
                })
        ip = _ip_info_with_ports(ports)
        ct = _contract({
            "clock_primary": {"raw_port": "ap_clk", "direction": "input", "width": 1},
            "reset_primary": {"raw_port": "ap_rst", "direction": "input", "width": 1},
            "gen_nd_out_layers": {
                "raw_port_tpl": "out_layer{0}_{1}",
                "dims": [3, 4],
                "direction": "output",
                "width": 40,
                "wiring_kind": "test_nd_array",
            },
        })
        result = _verify(tmp_path, ip, ct)
        assert result.passed
        assert len(result.errors) == 0

    def test_nd_missing_dims(self, tmp_path):
        ip = _ip_info_with_ports([
            {"name": "ap_clk", "direction": "IN", "width": 1, "type": "wire"},
            {"name": "ap_rst", "direction": "IN", "width": 1, "type": "wire"},
        ])
        ct = _contract({
            "clock_primary": {"raw_port": "ap_clk", "direction": "input", "width": 1},
            "reset_primary": {"raw_port": "ap_rst", "direction": "input", "width": 1},
            "gen_nd_bad": {
                "raw_port_tpl": "out_{0}_{1}",
                "direction": "output",
                "wiring_kind": "test",
            },
        })
        result = _verify(tmp_path, ip, ct)
        assert not result.passed
        assert any("missing 'dims'" in str(e) for e in result.errors)

    def test_nd_negative_dim(self, tmp_path):
        ip = _ip_info_with_ports([
            {"name": "ap_clk", "direction": "IN", "width": 1, "type": "wire"},
            {"name": "ap_rst", "direction": "IN", "width": 1, "type": "wire"},
        ])
        ct = _contract({
            "clock_primary": {"raw_port": "ap_clk", "direction": "input", "width": 1},
            "reset_primary": {"raw_port": "ap_rst", "direction": "input", "width": 1},
            "gen_nd_bad": {
                "raw_port_tpl": "out_{0}",
                "dims": [-1],
                "direction": "output",
                "wiring_kind": "test",
            },
        })
        result = _verify(tmp_path, ip, ct)
        assert not result.passed
        assert any("positive integer" in str(e) for e in result.errors)

    def test_nd_placeholder_mismatch(self, tmp_path):
        """Template has 2 dims but only 1 placeholder."""
        ip = _ip_info_with_ports([
            {"name": "ap_clk", "direction": "IN", "width": 1, "type": "wire"},
            {"name": "ap_rst", "direction": "IN", "width": 1, "type": "wire"},
        ])
        ct = _contract({
            "clock_primary": {"raw_port": "ap_clk", "direction": "input", "width": 1},
            "reset_primary": {"raw_port": "ap_rst", "direction": "input", "width": 1},
            "gen_nd_bad": {
                "raw_port_tpl": "out_{0}",  # missing {1}
                "dims": [3, 4],
                "direction": "output",
                "wiring_kind": "test",
            },
        })
        result = _verify(tmp_path, ip, ct)
        assert not result.passed
        assert any("missing placeholder" in str(e) for e in result.errors)

    def test_nd_no_matching_ports(self, tmp_path):
        """Template ports don't exist in ip_info at all."""
        ip = _ip_info_with_ports([
            {"name": "ap_clk", "direction": "IN", "width": 1, "type": "wire"},
            {"name": "ap_rst", "direction": "IN", "width": 1, "type": "wire"},
            {"name": "unrelated_port", "direction": "OUT", "width": 8, "type": "wire"},
        ])
        ct = _contract({
            "clock_primary": {"raw_port": "ap_clk", "direction": "input", "width": 1},
            "reset_primary": {"raw_port": "ap_rst", "direction": "input", "width": 1},
            "gen_nd_missing": {
                "raw_port_tpl": "nonexist_{0}_{1}",
                "dims": [2, 3],
                "direction": "output",
                "wiring_kind": "test",
            },
        })
        result = _verify(tmp_path, ip, ct)
        assert not result.passed
        assert any("No expanded ports found" in str(e) for e in result.errors)

    def test_nd_missing_wiring_kind_warns(self, tmp_path):
        """N-D role without wiring_kind should produce a warning, not error."""
        ports = [
            {"name": "ap_clk", "direction": "IN", "width": 1, "type": "wire"},
            {"name": "ap_rst", "direction": "IN", "width": 1, "type": "wire"},
            {"name": "out_0_0", "direction": "OUT", "width": 8, "type": "wire"},
            {"name": "out_1_1", "direction": "OUT", "width": 8, "type": "wire"},
        ]
        ip = _ip_info_with_ports(ports)
        ct = _contract({
            "clock_primary": {"raw_port": "ap_clk", "direction": "input", "width": 1},
            "reset_primary": {"raw_port": "ap_rst", "direction": "input", "width": 1},
            "gen_nd_no_sk": {
                "raw_port_tpl": "out_{0}_{1}",
                "dims": [2, 2],
                "direction": "output",
                # no wiring_kind
            },
        })
        result = _verify(tmp_path, ip, ct)
        assert result.passed  # warnings only, not errors
        assert any("wiring_kind" in str(w) for w in result.warnings)

    def test_nd_wrong_direction(self, tmp_path):
        """N-D ports exist but direction mismatches."""
        ports = [
            {"name": "ap_clk", "direction": "IN", "width": 1, "type": "wire"},
            {"name": "ap_rst", "direction": "IN", "width": 1, "type": "wire"},
            {"name": "x_0_0", "direction": "IN", "width": 8, "type": "wire"},
            {"name": "x_1_1", "direction": "IN", "width": 8, "type": "wire"},
        ]
        ip = _ip_info_with_ports(ports)
        ct = _contract({
            "clock_primary": {"raw_port": "ap_clk", "direction": "input", "width": 1},
            "reset_primary": {"raw_port": "ap_rst", "direction": "input", "width": 1},
            "gen_nd_dir": {
                "raw_port_tpl": "x_{0}_{1}",
                "dims": [2, 2],
                "direction": "output",
                "wiring_kind": "test",
            },
        })
        result = _verify(tmp_path, ip, ct)
        assert not result.passed
        assert any("direction" in str(e) for e in result.errors)

    def test_nd_wrong_width(self, tmp_path):
        """N-D ports exist but width mismatches."""
        ports = [
            {"name": "ap_clk", "direction": "IN", "width": 1, "type": "wire"},
            {"name": "ap_rst", "direction": "IN", "width": 1, "type": "wire"},
            {"name": "x_0_0", "direction": "OUT", "width": 16, "type": "wire"},
            {"name": "x_1_1", "direction": "OUT", "width": 16, "type": "wire"},
        ]
        ip = _ip_info_with_ports(ports)
        ct = _contract({
            "clock_primary": {"raw_port": "ap_clk", "direction": "input", "width": 1},
            "reset_primary": {"raw_port": "ap_rst", "direction": "input", "width": 1},
            "gen_nd_width": {
                "raw_port_tpl": "x_{0}_{1}",
                "dims": [2, 2],
                "direction": "output",
                "width": 32,  # mismatch: actual is 16
                "wiring_kind": "test",
            },
        })
        result = _verify(tmp_path, ip, ct)
        assert not result.passed
        assert any("width" in str(e) for e in result.errors)


# ─────────────────────────────────────────────────────────────────────────────
# Regression: existing shapes must not break
# ─────────────────────────────────────────────────────────────────────────────

class TestNoRegression:
    def test_clock_free_module(self, tmp_path):
        """clock_free module should not require clock_primary."""
        ip = _ip_info_with_ports([
            {"name": "din", "direction": "IN", "width": 8, "type": "wire"},
        ])
        ct = _contract(
            {"some_input": {"raw_port": "din", "direction": "input", "width": 8}},
            clock_free=True,
            reset_free=True,
        )
        result = _verify(tmp_path, ip, ct)
        assert result.passed

    def test_combinatorial_module(self, tmp_path):
        """combinatorial module should not require clock or reset."""
        ip = _ip_info_with_ports([
            {"name": "din", "direction": "IN", "width": 8, "type": "wire"},
        ])
        ct = _contract(
            {"some_input": {"raw_port": "din", "direction": "input", "width": 8}},
            combinatorial=True,
        )
        result = _verify(tmp_path, ip, ct)
        assert result.passed


# ─────────────────────────────────────────────────────────────────────────────
# Topology group verification tests
# ─────────────────────────────────────────────────────────────────────────────

from unittest.mock import MagicMock
from topgen.config import TopologyGroup, InstanceAssign, Module
from topgen.ip.contract_loader import LoadedContract
from topgen.ip.contract_verifier import verify_topology_groups


def _mock_design(modules, topology_groups):
    """Build a minimal design config mock."""
    cfg = MagicMock()
    cfg.modules = modules
    cfg.topology_groups = topology_groups
    return cfg


def _mod(name, *, instances=1, ip_info_key=None):
    """Build a minimal Module."""
    return Module(
        name=name,
        top=name,
        instances=instances,
        ip_info_key=ip_info_key or name,
        src=[],
    )


def _loaded_contract(roles):
    """Build a LoadedContract from roles dict."""
    spec = {"ip_interface": {"module_name": "test", "ip_info_key": "test", "roles": roles}}
    return LoadedContract(path=Path("/fake/test.yaml"), spec=spec)


class TestTopologyGroupVerification:

    def test_valid_instance_assign(self):
        """Valid instance_assign with matching partitions passes."""
        src = _loaded_contract({
            "out": {"raw_port": "csp_out", "direction": "output", "width": 27,
                    "wiring_kind": "stub"},
        })
        dst = _loaded_contract({
            "in_a": {"array": True, "raw_port_prefix": "in_a_", "count": 5,
                     "direction": "input", "width": 27, "wiring_kind": "stub",
                     "partition": "grp_a"},
            "in_b": {"array": True, "raw_port_prefix": "in_b_", "count": 5,
                     "direction": "input", "width": 27, "wiring_kind": "stub",
                     "partition": "grp_b"},
        })
        tg = TopologyGroup(
            name="test_ia", family="test", from_="src", to="dst",
            wiring_kind="stub",
            instance_assign=[
                InstanceAssign(instances=(0, 5), partition="grp_a"),
                InstanceAssign(instances=(5, 10), partition="grp_b"),
            ],
        )
        cfg = _mock_design(
            [_mod("src", instances=10), _mod("dst")],
            [tg],
        )
        issues = verify_topology_groups(cfg, {"src": src, "dst": dst})
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) == 0

    def test_missing_module_errors(self):
        """Non-existent module in topology_group is an error."""
        tg = TopologyGroup(
            name="bad", family="f", from_="nonexistent", to="also_missing",
        )
        cfg = _mock_design([], [tg])
        issues = verify_topology_groups(cfg, {})
        errors = [i for i in issues if i.severity == "error"]
        assert any("not found" in e.message for e in errors)

    def test_overlapping_instance_ranges(self):
        """Overlapping instance_assign ranges produce an error."""
        src = _loaded_contract({
            "out": {"raw_port": "o", "direction": "output", "width": 1,
                    "wiring_kind": "w"},
        })
        dst = _loaded_contract({
            "in_a": {"array": True, "raw_port_prefix": "a_", "count": 5,
                     "direction": "input", "width": 1, "wiring_kind": "w",
                     "partition": "a"},
        })
        tg = TopologyGroup(
            name="overlap", family="f", from_="s", to="d",
            wiring_kind="w",
            instance_assign=[
                InstanceAssign(instances=(0, 6), partition="a"),
                InstanceAssign(instances=(4, 10), partition="a"),  # overlaps
            ],
        )
        cfg = _mock_design([_mod("s", instances=10), _mod("d")], [tg])
        issues = verify_topology_groups(cfg, {"s": src, "d": dst})
        assert any("overlaps" in i.message for i in issues)

    def test_bad_partition_label(self):
        """Partition label not in consumer contract is an error."""
        src = _loaded_contract({
            "out": {"raw_port": "o", "direction": "output", "width": 1,
                    "wiring_kind": "w"},
        })
        dst = _loaded_contract({
            "in_x": {"array": True, "raw_port_prefix": "x_", "count": 5,
                     "direction": "input", "width": 1, "wiring_kind": "w",
                     "partition": "x"},
        })
        tg = TopologyGroup(
            name="bad_part", family="f", from_="s", to="d",
            wiring_kind="w",
            instance_assign=[InstanceAssign(instances=(0, 5), partition="nonexistent")],
        )
        cfg = _mock_design([_mod("s", instances=5), _mod("d")], [tg])
        issues = verify_topology_groups(cfg, {"s": src, "d": dst})
        assert any("nonexistent" in i.message and "not found" in i.message for i in issues)

    def test_invalid_role_pair_name(self):
        """role_pairs referencing non-existent roles produce errors."""
        src = _loaded_contract({
            "real_out": {"raw_port": "o", "direction": "output", "width": 1},
        })
        dst = _loaded_contract({
            "real_in": {"raw_port": "i", "direction": "input", "width": 1},
        })
        tg = TopologyGroup(
            name="bad_rp", family="f", from_="s", to="d",
            role_pairs=[("fake_out", "real_in"), ("real_out", "fake_in")],
        )
        cfg = _mock_design([_mod("s"), _mod("d")], [tg])
        issues = verify_topology_groups(cfg, {"s": src, "d": dst})
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) == 2  # one for each bad role name

    def test_auto_match_missing_wiring_kind(self):
        """auto_match with wiring_kind not in contracts is an error."""
        src = _loaded_contract({
            "out": {"raw_port": "o", "direction": "output", "width": 1,
                    "wiring_kind": "other"},
        })
        dst = _loaded_contract({
            "in": {"raw_port": "i", "direction": "input", "width": 1,
                   "wiring_kind": "other"},
        })
        tg = TopologyGroup(
            name="missing_wk", family="f", from_="s", to="d",
            wiring_kind="nonexistent",
        )
        cfg = _mock_design([_mod("s"), _mod("d")], [tg])
        issues = verify_topology_groups(cfg, {"s": src, "d": dst})
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) == 2  # not in src, not in dst
