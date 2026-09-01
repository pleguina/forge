"""Interface contracts may omit the port facts their own source declares.

Measured across this repo's 27 contracts, `raw_port`/`direction`/`width`/
`active_level` are pure transcription of the module's port list: 308 of 324
roles carry nothing else. Omitting them lets the scanned source answer;
declaring them keeps them as an assertion, verified against the real ports.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from forge.contracts.contract_loader import (
    LoadedContract,
    _derive_role_fields,
    drain_contract_conflicts,
    load_contracts_for_design,
)

PORTS = {
    "ap_clk": {"direction": "IN", "width": 1},
    "ap_rst": {"direction": "IN", "width": 1},
    "ap_rst_n": {"direction": "IN", "width": 1},
    "data_in": {"direction": "IN", "width": 8},
    "data_out": {"direction": "OUT", "width": 16},
}


def _contract(roles: dict, **iface) -> LoadedContract:
    return LoadedContract(Path("t.interface.yaml"),
                          {"ip_interface": {"roles": roles, **iface}})


def test_a_role_declaring_nothing_resolves_entirely_from_the_port_list():
    c = _contract({"data_in": None, "data_out": None})

    assert c.needs_port_resolution
    assert c.resolve_against_ports(PORTS) == []

    assert c.get_role("data_in") == {
        "raw_port": "data_in", "direction": "input", "width": 8}
    assert c.get_role("data_out") == {
        "raw_port": "data_out", "direction": "output", "width": 16}
    assert not c.needs_port_resolution


def test_declared_fields_are_kept_and_verified_not_overwritten():
    c = _contract({"data_in": {"width": 8}, "data_out": {"width": 99}})

    conflicts = c.resolve_against_ports(PORTS)

    # The correct declaration survives untouched; the wrong one is reported
    # rather than silently winning over the real port.
    assert c.get_role("data_in")["width"] == 8
    assert len(conflicts) == 1
    assert "data_out" in conflicts[0] and "99" in conflicts[0]


def test_raw_port_defaults_to_the_role_name_but_a_rename_still_works():
    c = _contract({"data_in": None, "renamed": {"raw_port": "data_out"}})

    c.resolve_against_ports(PORTS)

    assert c.get_raw_port("data_in") == "data_in"
    assert c.get_raw_port("renamed") == "data_out"
    assert c.get_role("renamed")["width"] == 16


def test_reset_active_level_follows_the_port_naming_convention():
    high = _contract({"reset_primary": {"raw_port": "ap_rst"}})
    low = _contract({"reset_primary": {"raw_port": "ap_rst_n"}})

    high.resolve_against_ports(PORTS)
    low.resolve_against_ports(PORTS)

    assert high.get_role("reset_primary")["active_level"] == "high"
    assert low.get_role("reset_primary")["active_level"] == "low"


def test_array_roles_are_left_alone():
    """An array role's physical binding is a real authoring decision, not a
    transcription — expanding it needs count/dims no port list supplies."""
    spec = {"array": True, "raw_port_prefix": "in_", "count": 4,
            "direction": "input", "width": 1}
    c = _contract({"in_array": dict(spec)})

    assert not c.needs_port_resolution
    assert c.resolve_against_ports(PORTS) == []
    assert c.get_role("in_array") == spec


def test_resolution_is_idempotent():
    c = _contract({"data_in": None})
    c.resolve_against_ports(PORTS)
    once = dict(c.get_role("data_in"))

    assert c.resolve_against_ports(PORTS) == []
    assert c.get_role("data_in") == once


def test_unknown_port_is_left_for_the_verifier_not_guessed():
    c = _contract({"nonexistent": None})

    assert c.resolve_against_ports(PORTS) == []
    assert c.get_role("nonexistent") == {}


def test_derive_helper_normalises_direction_spelling():
    resolved, conflicts = _derive_role_fields("p", {}, {"p": {"direction": "OUT", "width": 4}})
    assert (resolved["direction"], resolved["width"]) == ("output", 4)
    assert conflicts == []


def test_slim_rtl_contract_resolves_against_its_real_source(tmp_path):
    """End-to-end through load_contracts_for_design: a contract that declares
    only role names resolves from the module's actual Verilog."""
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "m.v").write_text(
        "module m #(parameter W = 8) (\n"
        "    input  wire         ap_clk,\n"
        "    input  wire         ap_rst,\n"
        "    input  wire [W-1:0] data_in,\n"
        "    output wire [W-1:0] data_out\n"
        ");\nendmodule\n"
    )
    (tmp_path / "c.interface.yaml").write_text(yaml.safe_dump({
        "ip_interface": {
            "module_name": "m", "ip_info_key": "m", "source_type": "rtl",
            "roles": {"clock_primary": {"raw_port": "ap_clk"},
                      "reset_primary": {"raw_port": "ap_rst"},
                      "data_in": None, "data_out": None},
        }
    }))
    (tmp_path / "modules.yml").write_text(yaml.safe_dump({
        "modules": [{"name": "m", "kind": "rtl", "top": "m",
                     "src": ["rtl/m.v"], "interface_contract": "c.interface.yaml"}]
    }))

    contracts = load_contracts_for_design(tmp_path / "modules.yml")
    drain_contract_conflicts()

    c = contracts["m"]
    assert c.get_role("data_in") == {
        "raw_port": "data_in", "direction": "input", "width": 8}
    assert c.get_role("data_out") == {
        "raw_port": "data_out", "direction": "output", "width": 8}


def test_unresolvable_gap_is_reported_rather_than_silently_dropped(tmp_path):
    """An HLS module has no HDL to scan until it is built, so its contract
    must stay explicit. The old symptom was a role vanishing from
    contract-driven wiring and a much later 'role not found' error."""
    (tmp_path / "c.interface.yaml").write_text(yaml.safe_dump({
        "ip_interface": {
            "module_name": "h", "ip_info_key": "h", "source_type": "hls",
            "roles": {"data_in": None},
        }
    }))
    (tmp_path / "modules.yml").write_text(yaml.safe_dump({
        "modules": [{"name": "h", "kind": "hls", "top": "h",
                     "src": ["algo/h.cpp"], "interface_contract": "c.interface.yaml"}]
    }))

    load_contracts_for_design(tmp_path / "modules.yml")
    conflicts = drain_contract_conflicts()

    assert len(conflicts) == 1
    assert "data_in" in conflicts[0]
    assert "HLS" in conflicts[0]
    assert "Declare those fields explicitly" in conflicts[0]
