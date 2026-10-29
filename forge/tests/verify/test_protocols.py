"""Interfaces found from a port list (forge.verification.protocols)."""

from __future__ import annotations

import pytest

from forge.verification.protocols import InterfaceError, Port, infer


def _p(*specs):
    return [Port(n, d, w) for n, d, w in specs]


def _summary(ifaces):
    return [(i.name, i.direction, i.protocol, [p.name for p in i.ports()]) for i in ifaces]


def test_valid_ready_valid_axis_and_scalar_are_found():
    ports = _p(("clk", "in", 1), ("rst", "in", 1),
               ("a", "in", 16), ("a_valid", "in", 1),
               ("b", "in", 8), ("b_valid", "in", 1), ("b_ready", "out", 1),
               ("s_tdata", "out", 32), ("s_tvalid", "out", 1), ("s_tready", "in", 1),
               ("mode", "in", 2), ("count", "out", 10))
    assert _summary(infer(ports, exclude=["clk", "rst"])) == [
        ("a", "in", "valid", ["a", "a_valid"]),
        ("b", "in", "ready_valid", ["b", "b_valid", "b_ready"]),
        ("s", "out", "ready_valid", ["s_tdata", "s_tvalid", "s_tready"]),
        ("mode", "in", "scalar", ["mode"]),
        ("count", "out", "scalar", ["count"]),
    ]


def test_a_ready_in_the_same_direction_as_the_data_is_not_its_handshake():
    with pytest.raises(InterfaceError, match="ports in no interface: x_ready"):
        infer(_p(("x", "in", 4), ("x_valid", "in", 1), ("x_ready", "in", 1)))


def test_emp_link_word_fields_form_one_interface():
    ports = _p(("l0_data", "in", 64), ("l0_valid", "in", 1), ("l0_start", "in", 1), ("l0_last", "in", 1),
               ("l0_start_of_orbit", "in", 1), ("q_data", "out", 64), ("q_valid", "out", 1),
               ("q_start", "out", 1), ("q_last", "out", 1))
    ifaces = infer(ports)
    assert [(i.name, i.protocol) for i in ifaces] == [("l0", "emp_lword"), ("q", "emp_lword")]
    assert set(ifaces[0].fields) == {"valid", "start", "last", "start_of_orbit"}
    assert ifaces[0].version == "emp_lword@1"


def test_declared_interfaces_take_their_ports_first():
    ports = _p(("lo", "in", 8), ("hi", "in", 8), ("go", "in", 1), ("res", "out", 8))
    ifaces = infer(ports, declared={"word": {"protocol": "valid", "data": ["lo", "hi"], "valid": "go"}})
    assert _summary(ifaces) == [("word", "in", "valid", ["lo", "hi", "go"]), ("res", "out", "scalar", ["res"])]
    assert ifaces[0].width == 16


@pytest.mark.parametrize("declared, message", [
    ({"w": {"protocol": "ready_valid", "data": ["lo"], "valid": "go"}}, "needs its ready port"),
    ({"w": {"protocol": "fifo", "data": ["lo"]}}, "protocol 'fifo'"),
    ({"w": {"data": ["lo", "res"], "valid": "go"}}, "both directions"),
    ({"w": {"data": ["nope"], "valid": "go"}}, "no port 'nope'"),
    ({"w": {"data": ["lo"], "valid": "go"}, "v": {"data": ["hi"], "valid": "go"}}, "in two interfaces"),
])
def test_declarations_that_do_not_fit_are_refused(declared, message):
    with pytest.raises(InterfaceError, match=message):
        infer(_p(("lo", "in", 8), ("hi", "in", 8), ("go", "in", 1), ("res", "out", 8)), declared=declared)
