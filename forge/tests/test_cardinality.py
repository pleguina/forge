"""
Tests for declarative cardinality (release-plan §2.4):
``forge.topgen.ip.cardinality.parse_cardinality`` (per-role structural
parsing/sugar resolution) and ``verify_cardinality`` (design-level
enforcement against what ``auto_match_ports`` actually wired).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.topgen.config import Connection, DesignConfig, Module
from forge.topgen.ip.cardinality import (
    Bound,
    CardinalityError,
    parse_cardinality,
    verify_cardinality,
)
from forge.topgen.ip.contract_loader import LoadedContract
from forge.topgen.ip.matcher import auto_match_ports


def _synthetic_contract(module_name: str, ip_info_key: str, roles: dict) -> LoadedContract:
    spec = {
        "ip_interface": {
            "module_name": module_name,
            "ip_info_key": ip_info_key,
            "source_type": "hls",
            "roles": roles,
        }
    }
    return LoadedContract(path=Path(f"/fake/{module_name}.interface.yaml"), spec=spec)


# ─────────────────────────────────────────────────────────────────────────────
# parse_cardinality — structural parsing and sugar resolution
# ─────────────────────────────────────────────────────────────────────────────

class TestParseCardinality:
    def test_no_block_returns_none(self):
        assert parse_cardinality({"direction": "input"}, direction="input") is None

    def test_explicit_producers_bound_on_input_role(self):
        resolved = parse_cardinality(
            {"direction": "input", "cardinality": {"producers": {"min": 1, "max": 1}}},
            direction="input",
        )
        assert resolved.producers == Bound(min=1, max=1)
        assert resolved.consumers is None

    def test_explicit_consumers_bound_on_output_role(self):
        resolved = parse_cardinality(
            {"direction": "output", "cardinality": {"consumers": {"min": 1, "max": "many"}}},
            direction="output",
        )
        assert resolved.consumers == Bound(min=1, max="many")
        assert resolved.producers is None

    def test_fanout_forbidden_is_sugar_for_consumers_max_1(self):
        resolved = parse_cardinality(
            {"direction": "output", "cardinality": {"fanout": "forbidden"}},
            direction="output",
        )
        assert resolved.consumers == Bound(min=0, max=1)

    def test_fanout_allowed_is_sugar_for_consumers_max_many(self):
        resolved = parse_cardinality(
            {"direction": "output", "cardinality": {"fanout": "allowed"}},
            direction="output",
        )
        assert resolved.consumers == Bound(min=0, max="many")

    def test_completeness_required_on_input_role_forces_min_1(self):
        resolved = parse_cardinality(
            {"direction": "input", "cardinality": {"completeness": "required"}},
            direction="input",
        )
        assert resolved.producers == Bound(min=1, max="many")

    def test_completeness_optional_on_output_role_allows_min_0(self):
        resolved = parse_cardinality(
            {"direction": "output", "cardinality": {"consumers": {"min": 1}, "completeness": "optional"}},
            direction="output",
        )
        assert resolved.consumers.min == 0

    def test_unknown_key_is_an_error(self):
        with pytest.raises(CardinalityError, match="unknown cardinality key"):
            parse_cardinality(
                {"direction": "input", "cardinality": {"bogus": 1}},
                direction="input",
            )

    def test_max_less_than_min_is_an_error(self):
        with pytest.raises(CardinalityError, match="less than"):
            parse_cardinality(
                {"direction": "input", "cardinality": {"producers": {"min": 2, "max": 1}}},
                direction="input",
            )

    def test_consumers_on_input_role_is_an_error(self):
        with pytest.raises(CardinalityError, match="did you mean 'producers'"):
            parse_cardinality(
                {"direction": "input", "cardinality": {"consumers": {"max": 1}}},
                direction="input",
            )

    def test_producers_on_output_role_is_an_error(self):
        with pytest.raises(CardinalityError, match="did you mean 'consumers'"):
            parse_cardinality(
                {"direction": "output", "cardinality": {"producers": {"max": 1}}},
                direction="output",
            )

    def test_fanout_on_input_role_is_an_error(self):
        with pytest.raises(CardinalityError, match="fanout"):
            parse_cardinality(
                {"direction": "input", "cardinality": {"fanout": "forbidden"}},
                direction="input",
            )


# ─────────────────────────────────────────────────────────────────────────────
# Bound.satisfied
# ─────────────────────────────────────────────────────────────────────────────

class TestBound:
    def test_satisfied_within_range(self):
        assert Bound(min=1, max=1).satisfied(1)

    def test_unsatisfied_below_min(self):
        assert not Bound(min=1, max=1).satisfied(0)

    def test_unsatisfied_above_max(self):
        assert not Bound(min=1, max=1).satisfied(2)

    def test_many_max_has_no_upper_bound(self):
        assert Bound(min=0, max="many").satisfied(50)


# ─────────────────────────────────────────────────────────────────────────────
# verify_cardinality — design-level enforcement
# ─────────────────────────────────────────────────────────────────────────────

def _port(name, direction, width=8):
    return {"name": name, "direction": direction, "width": width, "type": "wire"}


class TestVerifyCardinality:
    def test_required_producer_missing_is_an_error(self):
        """dst declares producers.min=1 (required) but no connection wires it."""
        dst = Module(name="dst", top="dst_top", src=[])
        cfg = DesignConfig(part="xcvu13p", clock_period=4.0, modules=[dst], connections=[])
        ip_info = {"dst": {"ports": [_port("din", "IN")]}}
        contracts = {
            "dst": _synthetic_contract("dst", "dst", {
                "din_role": {
                    "raw_port": "din", "direction": "input", "width": 8,
                    "cardinality": {"producers": {"min": 1, "max": 1}},
                },
            }),
        }
        _, _, report = auto_match_ports(cfg, ip_info, contracts=contracts)
        issues = verify_cardinality(cfg, contracts, report)
        assert any("0 producer" in i.message for i in issues)
        assert all(i.severity == "error" for i in issues)

    def test_satisfied_single_producer_is_not_an_error(self):
        src = Module(name="src", top="src_top", src=[])
        dst = Module(name="dst", top="dst_top", src=[])
        cfg = DesignConfig(
            part="xcvu13p", clock_period=4.0,
            modules=[src, dst],
            connections=[Connection(from_="src", to="dst", port_map=[("dout", "din")])],
        )
        ip_info = {
            "src": {"ports": [_port("dout", "OUT")]},
            "dst": {"ports": [_port("din", "IN")]},
        }
        contracts = {
            "dst": _synthetic_contract("dst", "dst", {
                "din_role": {
                    "raw_port": "din", "direction": "input", "width": 8,
                    "cardinality": {"producers": {"min": 1, "max": 1}},
                },
            }),
        }
        _, _, report = auto_match_ports(cfg, ip_info, contracts=contracts)
        issues = verify_cardinality(cfg, contracts, report)
        assert issues == []

    def test_forbidden_fanout_exceeded_is_an_error(self):
        """A source declares fanout: forbidden (max 1 consumer) but drives
        two sinks — this is fully observable from connection_evidence
        alone (fan-out is never blocked by the matcher today)."""
        src = Module(name="src", top="src_top", src=[])
        dst_a = Module(name="dst_a", top="dst_top", src=[])
        dst_b = Module(name="dst_b", top="dst_top", src=[])
        cfg = DesignConfig(
            part="xcvu13p", clock_period=4.0,
            modules=[src, dst_a, dst_b],
            connections=[
                Connection(from_="src", to="dst_a", port_map=[("dout", "din")]),
                Connection(from_="src", to="dst_b", port_map=[("dout", "din")]),
            ],
        )
        ip_info = {
            "src": {"ports": [_port("dout", "OUT")]},
            "dst_a": {"ports": [_port("din", "IN")]},
            "dst_b": {"ports": [_port("din", "IN")]},
        }
        contracts = {
            "src": _synthetic_contract("src", "src", {
                "dout_role": {
                    "raw_port": "dout", "direction": "output", "width": 8,
                    "cardinality": {"fanout": "forbidden"},
                },
            }),
        }
        _, _, report = auto_match_ports(cfg, ip_info, contracts=contracts)
        issues = verify_cardinality(cfg, contracts, report)
        assert any("2 consumer" in i.message for i in issues)

    def test_rejected_fanin_counts_toward_producer_cardinality(self):
        """Two sources both try to drive the same sink pin — the matcher's
        first-driver-wins guard silently wires only the first and records
        the second in rejected_fanin. verify_cardinality must count both as
        producer attempts against a max:1 bound."""
        src_a = Module(name="src_a", top="src_top", src=[])
        src_b = Module(name="src_b", top="src_top", src=[])
        dst = Module(name="dst", top="dst_top", src=[])
        cfg = DesignConfig(
            part="xcvu13p", clock_period=4.0,
            modules=[src_a, src_b, dst],
            connections=[
                Connection(from_="src_a", to="dst", port_map=[("dout", "din")]),
                Connection(from_="src_b", to="dst", port_map=[("dout", "din")]),
            ],
        )
        ip_info = {
            "src_a": {"ports": [_port("dout", "OUT")]},
            "src_b": {"ports": [_port("dout", "OUT")]},
            "dst": {"ports": [_port("din", "IN")]},
        }
        contracts = {
            "dst": _synthetic_contract("dst", "dst", {
                "din_role": {
                    "raw_port": "din", "direction": "input", "width": 8,
                    "cardinality": {"producers": {"min": 1, "max": 1}},
                },
            }),
        }
        _, _, report = auto_match_ports(cfg, ip_info, contracts=contracts)
        assert report.rejected_fanin[("dst", "din")] == [("src_b", "dout")]

        issues = verify_cardinality(cfg, contracts, report)
        assert any("2 producer" in i.message for i in issues)
