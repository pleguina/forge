"""
Tests for MatchReport.connection_evidence — per-pin-pair wiring-method
attribution (release-plan §3.5 "matching evidence"). Also covers
rejected_matches (semantic candidate rejections that never produced a
wire, so there's no ResolvedConnection to attach rejected_fanin-style
evidence to) and gather_scatter_evidence — later slices of the same
effort.
"""

from __future__ import annotations

from pathlib import Path

from topgen.config import Connection, DesignConfig, Module
from topgen.ip.contract_loader import LoadedContract
from topgen.ip.matcher import RejectedMatch, _derive_from_contracts, auto_match_ports


def _make_contract(roles, module_name="test"):
    """Build a LoadedContract from a dict of {role_name: role_spec} — same
    minimal-spec convention as test_topology_deriver.py's helper."""
    spec = {
        "ip_interface": {
            "module_name": module_name,
            "ip_info_key": module_name,
            "roles": roles,
        }
    }
    return LoadedContract(path=Path(f"/fake/{module_name}.interface.yaml"), spec=spec)


def _scalar_role(direction, wiring_kind, raw_port, kind="scalar", **extra):
    d = {"direction": direction, "wiring_kind": wiring_kind, "width": 32}
    if kind == "scalar":
        d["raw_port"] = raw_port
    d.update(extra)
    return d


def _prefix_role(direction, wiring_kind, prefix, count):
    return {
        "direction": direction, "wiring_kind": wiring_kind,
        "raw_port_prefix": prefix, "count": count, "width": 32,
    }


def _ip_info_entry(ports):
    return {"ports": ports}


def _port(name, direction, width=8):
    return {"name": name, "direction": direction, "width": width, "type": "wire"}


def test_port_map_connection_recorded_as_port_map():
    src = Module(name="src", top="src_top", src=[])
    dst = Module(name="dst", top="dst_top", src=[])
    cfg = DesignConfig(
        part="xcvu13p", clock_period=4.0,
        modules=[src, dst],
        connections=[Connection(from_="src", to="dst", port_map=[("dout", "din")])],
    )
    ip_info = {
        "src": _ip_info_entry([_port("dout", "OUT")]),
        "dst": _ip_info_entry([_port("din", "IN")]),
    }

    _, _, report = auto_match_ports(cfg, ip_info)

    assert report.connection_evidence[("src", "dout", "dst", "din")] == "port_map"
    assert report.wiring_method_counts["port_map"] == 1


def test_auto_match_connection_recorded_as_auto_match():
    src = Module(name="src", top="src_top", src=[])
    dst = Module(name="dst", top="dst_top", src=[])
    cfg = DesignConfig(
        part="xcvu13p", clock_period=4.0,
        modules=[src, dst],
        connections=[Connection(from_="src", to="dst")],  # no port_map/ranges
    )
    ip_info = {
        "src": _ip_info_entry([_port("shared_bus", "OUT")]),
        "dst": _ip_info_entry([_port("shared_bus", "IN")]),
    }

    _, _, report = auto_match_ports(cfg, ip_info)

    assert report.connection_evidence[("src", "shared_bus", "dst", "shared_bus")] == "auto_match"
    assert report.wiring_method_counts["auto_match"] == 1


def test_connection_evidence_matches_wiring_method_counts_totals():
    """The per-pin evidence dict must be consistent with the existing
    aggregate counts — same classification, just retained per-pin instead
    of only tallied."""
    src = Module(name="src", top="src_top", src=[])
    dst = Module(name="dst", top="dst_top", src=[])
    cfg = DesignConfig(
        part="xcvu13p", clock_period=4.0,
        modules=[src, dst],
        connections=[
            Connection(from_="src", to="dst", port_map=[("a_out", "a_in")]),
        ],
    )
    ip_info = {
        "src": _ip_info_entry([_port("a_out", "OUT")]),
        "dst": _ip_info_entry([_port("a_in", "IN")]),
    }

    _, _, report = auto_match_ports(cfg, ip_info)

    port_map_pins = [
        k for k, method in report.connection_evidence.items() if method == "port_map"
    ]
    assert len(port_map_pins) == report.wiring_method_counts["port_map"]


# ─────────────────────────────────────────────────────────────────────────
# RejectedMatch — semantic rejections in _derive_from_contracts (unit-level)
# ─────────────────────────────────────────────────────────────────────────

def test_derive_from_contracts_no_dst_role_is_rejected():
    src = _make_contract({"a": _scalar_role("output", "wk_a", "a_out")}, "src")
    dst = _make_contract({"b": _scalar_role("output", "wk_b", "b_out")}, "dst")  # no wk_a on dst

    pairs, rejections = _derive_from_contracts(src, dst)

    assert pairs == []
    assert len(rejections) == 1
    r = rejections[0]
    assert r.scope == "contract_role"
    assert r.src_ref == "wk_a"
    assert r.dst_ref is None
    assert "no destination role" in r.reason


def test_derive_from_contracts_ambiguous_match_is_rejected():
    src = _make_contract({
        "a1": _scalar_role("output", "shared", "a1_out"),
        "a2": _scalar_role("output", "shared", "a2_out"),
    }, "src")
    dst = _make_contract({"b": _scalar_role("input", "shared", "b_in")}, "dst")

    pairs, rejections = _derive_from_contracts(src, dst)

    assert pairs == []
    assert len(rejections) == 1
    r = rejections[0]
    assert r.scope == "contract_role"
    assert r.src_ref == "shared" and r.dst_ref == "shared"
    assert "ambiguous" in r.reason


def test_derive_from_contracts_role_kind_mismatch_is_rejected():
    src = _make_contract({"a": _scalar_role("output", "wk", "a_out")}, "src")
    dst = _make_contract({"b": _prefix_role("input", "wk", "b_in_", 4)}, "dst")

    pairs, rejections = _derive_from_contracts(src, dst)

    assert pairs == []
    assert len(rejections) == 1
    r = rejections[0]
    assert r.scope == "contract_role"
    assert "kind mismatch" in r.reason
    assert "scalar" in r.reason and "prefix_array" in r.reason


def test_derive_from_contracts_unambiguous_match_produces_pairs_no_rejection():
    src = _make_contract({"a": _prefix_role("output", "wk", "a_out_", 3)}, "src")
    dst = _make_contract({"b": _prefix_role("input", "wk", "b_in_", 3)}, "dst")

    pairs, rejections = _derive_from_contracts(src, dst)

    assert len(pairs) == 3
    assert rejections == []


def test_auto_match_ports_records_module_pair_on_rejected_matches():
    """Integration: the caller in auto_match_ports fills in module_pair
    (the _derive_from_contracts unit itself has no module-name context)."""
    src = Module(name="src", top="src_top", src=[])
    dst = Module(name="dst", top="dst_top", src=[])
    cfg = DesignConfig(
        part="xcvu13p", clock_period=4.0,
        modules=[src, dst],
        connections=[Connection(from_="src", to="dst", contract_wiring=True)],
    )
    ip_info = {
        "src": _ip_info_entry([_port("a_out", "OUT")]),
        "dst": _ip_info_entry([_port("b_in", "IN")]),
    }
    contracts = {
        "src": _make_contract({"a": _scalar_role("output", "wk_a", "a_out")}, "src"),
        "dst": _make_contract({"b": _scalar_role("output", "wk_b", "b_out")}, "dst"),
    }

    _, _, report = auto_match_ports(cfg, ip_info, contracts=contracts)

    assert len(report.rejected_matches) == 1
    assert report.rejected_matches[0].module_pair == ("src", "dst")


def test_bulk_auto_match_no_shape_match_is_rejected():
    """A source group with no destination group of matching
    count/width/type gets a RejectedMatch (scope='auto_match_group'),
    not a silent nothing-happens outcome."""
    src = Module(name="src", top="src_top", src=[])
    dst = Module(name="dst", top="dst_top", src=[])
    cfg = DesignConfig(
        part="xcvu13p", clock_period=4.0,
        modules=[src, dst],
        connections=[Connection(from_="src", to="dst")],  # no port_map/ranges -> bulk auto-match
    )
    ip_info = {
        "src": _ip_info_entry([_port("wide_out", "OUT", width=32)]),
        "dst": _ip_info_entry([_port("narrow_in", "IN", width=8)]),  # width mismatch -> no shape match
    }

    _, _, report = auto_match_ports(cfg, ip_info)

    auto_match_rejections = [
        r for r in report.rejected_matches if r.scope == "auto_match_group"
    ]
    assert len(auto_match_rejections) == 1
    r = auto_match_rejections[0]
    assert r.module_pair == ("src", "dst")
    assert r.src_ref == "wide_out"
    assert "no destination group matched shape" in r.reason
