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


class TestReservedRoles:
    """clock_secondary/reset_secondary are declared in canonical_roles.yaml
    but have no clock-domain/CDC model behind them yet (audit gap #4: dead,
    misleading stub). They must be explicitly surfaced as reserved, not
    silently accepted as if multi-clock-domain support existed."""

    def test_clock_secondary_is_flagged_reserved(self, tmp_path):
        ip = _ip_info_with_ports([
            {"name": "clk2", "direction": "IN", "width": 1, "type": "wire"},
        ])
        ct = _contract(
            {"clock_secondary": {"raw_port": "clk2", "direction": "input", "width": 1}},
            clock_free=True, reset_free=True,
        )
        result = _verify(tmp_path, ip, ct)
        assert result.passed  # reserved is a warning, not an error
        assert any(
            "clock_secondary" in i.role and "RESERVED" in i.message
            for i in result.issues
        )

    def test_reset_secondary_is_flagged_reserved(self, tmp_path):
        ip = _ip_info_with_ports([
            {"name": "rst2", "direction": "IN", "width": 1, "type": "wire"},
        ])
        ct = _contract(
            {"reset_secondary": {"raw_port": "rst2", "direction": "input", "width": 1}},
            clock_free=True, reset_free=True,
        )
        result = _verify(tmp_path, ip, ct)
        assert result.passed
        assert any(
            "reset_secondary" in i.role and "RESERVED" in i.message
            for i in result.issues
        )

    def test_non_reserved_role_is_not_flagged(self, tmp_path):
        ip = _ip_info_with_ports([
            {"name": "din", "direction": "IN", "width": 8, "type": "wire"},
        ])
        ct = _contract(
            {"clock_primary": {"raw_port": "din", "direction": "input", "width": 8}},
            reset_free=True,
        )
        result = _verify(tmp_path, ip, ct)
        assert not any("RESERVED" in i.message for i in result.issues)


class TestProtocolSemantics:
    """protocol: is optional metadata on a role. Absent is valid (no
    existing contract needs to declare it); a declared value must be one
    of the known built-ins."""

    def test_no_protocol_declared_is_valid(self, tmp_path):
        ip = _ip_info_with_ports([
            {"name": "din", "direction": "IN", "width": 8, "type": "wire"},
        ])
        ct = _contract(
            {"some_input": {"raw_port": "din", "direction": "input", "width": 8}},
            combinatorial=True,
        )
        result = _verify(tmp_path, ip, ct)
        assert result.passed
        assert not any("protocol" in i.message for i in result.issues)

    def test_each_known_protocol_value_is_valid(self, tmp_path):
        ip = _ip_info_with_ports([
            {"name": "din", "direction": "IN", "width": 8, "type": "wire"},
        ])
        for value in ("combinational", "valid-only", "ready-valid", "fixed-frame"):
            ct = _contract(
                {"some_input": {
                    "raw_port": "din", "direction": "input", "width": 8,
                    "protocol": value,
                }},
                combinatorial=True,
            )
            result = _verify(tmp_path, ip, ct)
            assert result.passed, f"protocol={value!r} should be valid"

    def test_unknown_protocol_value_is_a_structured_error(self, tmp_path):
        ip = _ip_info_with_ports([
            {"name": "din", "direction": "IN", "width": 8, "type": "wire"},
        ])
        ct = _contract(
            {"some_input": {
                "raw_port": "din", "direction": "input", "width": 8,
                "protocol": "totally-made-up",
            }},
            combinatorial=True,
        )
        result = _verify(tmp_path, ip, ct)
        assert not result.passed
        assert any(
            "some_input" in i.role and "unknown protocol" in i.message
            for i in result.issues
        )


class TestInterfaceMembers:
    """interface: / member: grouping. Optional — a
    role that omits them keeps today's 1:1 role-per-interface behavior."""

    def test_grouped_roles_with_known_members_are_valid(self, tmp_path):
        ip = _ip_info_with_ports([
            {"name": "raw_hit", "direction": "IN", "width": 32, "type": "wire"},
            {"name": "raw_valid", "direction": "IN", "width": 1, "type": "wire"},
        ])
        ct = _contract({
            "raw_hit": {
                "raw_port": "raw_hit", "direction": "input", "width": 32,
                "interface": "raw_detector_hit", "member": "data",
            },
            "raw_valid": {
                "raw_port": "raw_valid", "direction": "input", "width": 1,
                "interface": "raw_detector_hit", "member": "valid",
            },
        }, combinatorial=True)
        result = _verify(tmp_path, ip, ct)
        assert result.passed

    def test_unknown_member_value_is_a_structured_error(self, tmp_path):
        ip = _ip_info_with_ports([
            {"name": "din", "direction": "IN", "width": 8, "type": "wire"},
        ])
        ct = _contract({
            "some_input": {
                "raw_port": "din", "direction": "input", "width": 8,
                "interface": "grp", "member": "totally-made-up",
            },
        }, combinatorial=True)
        result = _verify(tmp_path, ip, ct)
        assert not result.passed
        assert any(
            "some_input" in i.role and "unknown member" in i.message
            for i in result.issues
        )

    def test_member_without_interface_warns(self, tmp_path):
        ip = _ip_info_with_ports([
            {"name": "din", "direction": "IN", "width": 8, "type": "wire"},
        ])
        ct = _contract({
            "some_input": {
                "raw_port": "din", "direction": "input", "width": 8,
                "member": "data",
            },
        }, combinatorial=True)
        result = _verify(tmp_path, ip, ct)
        assert result.passed  # warning only, not an error
        assert any(
            "some_input" in i.role and "no grouping effect" in i.message
            for i in result.issues
        )

    def test_duplicate_member_in_same_group_is_an_error(self, tmp_path):
        ip = _ip_info_with_ports([
            {"name": "valid_a", "direction": "IN", "width": 1, "type": "wire"},
            {"name": "valid_b", "direction": "IN", "width": 1, "type": "wire"},
        ])
        ct = _contract({
            "valid_a": {
                "raw_port": "valid_a", "direction": "input", "width": 1,
                "interface": "grp", "member": "valid",
            },
            "valid_b": {
                "raw_port": "valid_b", "direction": "input", "width": 1,
                "interface": "grp", "member": "valid",
            },
        }, combinatorial=True)
        result = _verify(tmp_path, ip, ct)
        assert not result.passed
        assert any("more than once" in i.message for i in result.issues)


class TestSchemaVersioning:
    """schema_version: on *.interface.yaml. Absence is
    valid (fully backward compatible); a declared value is checked against
    INTERFACE_CONTRACT_SCHEMA_VERSION."""

    def test_no_schema_version_is_valid(self, tmp_path):
        ip = _ip_info_with_ports([{"name": "din", "direction": "IN", "width": 8, "type": "wire"}])
        ct = _contract({"some_input": {"raw_port": "din", "direction": "input", "width": 8}}, combinatorial=True)
        result = _verify(tmp_path, ip, ct)
        assert result.passed
        assert not any("schema_version" in i.message for i in result.issues)

    def test_matching_schema_version_is_valid(self, tmp_path):
        ip = _ip_info_with_ports([{"name": "din", "direction": "IN", "width": 8, "type": "wire"}])
        ct = _contract(
            {"some_input": {"raw_port": "din", "direction": "input", "width": 8}},
            combinatorial=True, schema_version="1.0",
        )
        result = _verify(tmp_path, ip, ct)
        assert result.passed

    def test_newer_minor_is_a_warning_only(self, tmp_path):
        ip = _ip_info_with_ports([{"name": "din", "direction": "IN", "width": 8, "type": "wire"}])
        ct = _contract(
            {"some_input": {"raw_port": "din", "direction": "input", "width": 8}},
            combinatorial=True, schema_version="1.99",
        )
        result = _verify(tmp_path, ip, ct)
        assert result.passed  # warning only
        assert any(i.severity == "warning" and "newer" in i.message for i in result.issues)

    def test_different_major_is_a_structured_error(self, tmp_path):
        ip = _ip_info_with_ports([{"name": "din", "direction": "IN", "width": 8, "type": "wire"}])
        ct = _contract(
            {"some_input": {"raw_port": "din", "direction": "input", "width": 8}},
            combinatorial=True, schema_version="2.0",
        )
        result = _verify(tmp_path, ip, ct)
        assert not result.passed
        assert any(i.severity == "error" and "incompatible" in i.message for i in result.issues)


class TestDeclarativeCardinality:
    """cardinality: block structural validation.
    Design-level enforcement (does the wired design satisfy the declared
    bound) is covered separately in test_cardinality.py — ContractVerifier
    only validates one contract's cardinality: block in isolation."""

    def test_valid_producers_bound_is_valid(self, tmp_path):
        ip = _ip_info_with_ports([
            {"name": "din", "direction": "IN", "width": 8, "type": "wire"},
        ])
        ct = _contract({
            "some_input": {
                "raw_port": "din", "direction": "input", "width": 8,
                "cardinality": {"producers": {"min": 1, "max": 1}},
            },
        }, combinatorial=True)
        result = _verify(tmp_path, ip, ct)
        assert result.passed

    def test_valid_fanout_sugar_is_valid(self, tmp_path):
        ip = _ip_info_with_ports([
            {"name": "dout", "direction": "OUT", "width": 8, "type": "wire"},
        ])
        ct = _contract({
            "some_output": {
                "raw_port": "dout", "direction": "output", "width": 8,
                "cardinality": {"fanout": "forbidden"},
            },
        }, combinatorial=True)
        result = _verify(tmp_path, ip, ct)
        assert result.passed

    def test_unknown_cardinality_key_is_a_structured_error(self, tmp_path):
        ip = _ip_info_with_ports([
            {"name": "din", "direction": "IN", "width": 8, "type": "wire"},
        ])
        ct = _contract({
            "some_input": {
                "raw_port": "din", "direction": "input", "width": 8,
                "cardinality": {"bogus": 1},
            },
        }, combinatorial=True)
        result = _verify(tmp_path, ip, ct)
        assert not result.passed
        assert any(
            "some_input" in i.role and "invalid cardinality" in i.message
            for i in result.issues
        )

    def test_consumers_on_input_role_is_a_structured_error(self, tmp_path):
        ip = _ip_info_with_ports([
            {"name": "din", "direction": "IN", "width": 8, "type": "wire"},
        ])
        ct = _contract({
            "some_input": {
                "raw_port": "din", "direction": "input", "width": 8,
                "cardinality": {"consumers": {"max": 1}},
            },
        }, combinatorial=True)
        result = _verify(tmp_path, ip, ct)
        assert not result.passed
        assert any(
            "some_input" in i.role and "invalid cardinality" in i.message
            for i in result.issues
        )


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

    def test_valid_structured_coordinates(self):
        """Structured coordinates:` mappings validate the same way legacy
        partition strings do (audit gap #2 — additive, not a replacement)."""
        src = _loaded_contract({
            "out": {"raw_port": "o", "direction": "output", "width": 1,
                    "wiring_kind": "w"},
        })
        dst = _loaded_contract({
            "in_a": {"raw_port_prefix": "a_", "count": 5,
                     "direction": "input", "width": 1, "wiring_kind": "w",
                     "coordinates": {"sector": 1, "station": 1}},
            "in_b": {"raw_port_prefix": "b_", "count": 5,
                     "direction": "input", "width": 1, "wiring_kind": "w",
                     "coordinates": {"sector": 2, "station": 1}},
        })
        tg = TopologyGroup(
            name="coord_ok", family="f", from_="s", to="d",
            wiring_kind="w",
            instance_assign=[
                InstanceAssign(instances=(0, 5), coordinates={"sector": 1, "station": 1}),
                InstanceAssign(instances=(5, 10), coordinates={"sector": 2, "station": 1}),
            ],
        )
        cfg = _mock_design([_mod("s", instances=10), _mod("d")], [tg])
        issues = verify_topology_groups(cfg, {"s": src, "d": dst})
        assert not any(i.severity == "error" for i in issues)

    def test_unresolvable_structured_coordinate_is_an_error(self):
        """A structured coordinate that matches no consumer role errors,
        same as an unresolvable legacy partition label."""
        src = _loaded_contract({
            "out": {"raw_port": "o", "direction": "output", "width": 1,
                    "wiring_kind": "w"},
        })
        dst = _loaded_contract({
            "in_x": {"raw_port_prefix": "x_", "count": 5,
                     "direction": "input", "width": 1, "wiring_kind": "w",
                     "coordinates": {"sector": 1}},
        })
        tg = TopologyGroup(
            name="bad_coord", family="f", from_="s", to="d",
            wiring_kind="w",
            instance_assign=[InstanceAssign(instances=(0, 5), coordinates={"sector": 9})],
        )
        cfg = _mock_design([_mod("s", instances=5), _mod("d")], [tg])
        issues = verify_topology_groups(cfg, {"s": src, "d": dst})
        assert any("sector=9" in i.message and "not found" in i.message for i in issues)

    def test_duplicate_coordinate_across_consumer_roles_is_an_error(self):
        """Two consumer roles declaring the same coordinate (structured or
        legacy) is flagged, generalizing duplicate-partition detection to
        the verify path (not just topology_deriver's derivation path)."""
        src = _loaded_contract({
            "out": {"raw_port": "o", "direction": "output", "width": 1,
                    "wiring_kind": "w"},
        })
        dst = _loaded_contract({
            "in_a": {"raw_port_prefix": "a_", "count": 5,
                     "direction": "input", "width": 1, "wiring_kind": "w",
                     "coordinates": {"sector": 1}},
            "in_b": {"raw_port_prefix": "b_", "count": 5,
                     "direction": "input", "width": 1, "wiring_kind": "w",
                     "coordinates": {"sector": 1}},
        })
        tg = TopologyGroup(
            name="dup_coord", family="f", from_="s", to="d",
            wiring_kind="w",
            instance_assign=[InstanceAssign(instances=(0, 5), coordinates={"sector": 1})],
        )
        cfg = _mock_design([_mod("s", instances=5), _mod("d")], [tg])
        issues = verify_topology_groups(cfg, {"s": src, "d": dst})
        assert any("duplicate coordinate" in i.message and "sector=1" in i.message for i in issues)

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
