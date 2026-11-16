"""allowed_unconnected patterns name an instance or a whole module."""

from __future__ import annotations

from forge.contracts.config import Module
from forge.generation.artifacts import filter_unaccounted


def _modules():
    return [Module(name="csc", top="csc", src=[], instances=3), Module(name="gb", top="gb", src=[], instances=1)]


def test_a_module_name_covers_every_instance():
    ports = [(f"csc_{i}", "dbg_s2_r", 10) for i in range(3)]
    assert filter_unaccounted(ports, ["csc.dbg_s2_r"], _modules()) == []


def test_an_instance_pattern_still_matches_only_that_instance():
    ports = [(f"csc_{i}", "dbg_s2_r", 10) for i in range(3)]
    assert filter_unaccounted(ports, ["csc_1.dbg_s2_r"], _modules()) == [ports[0], ports[2]]


def test_other_ports_of_the_module_stay_unaccounted():
    ports = [("csc_0", "dbg_s2_r", 10), ("csc_0", "csp_out", 39)]
    assert filter_unaccounted(ports, ["csc.dbg_*"], _modules()) == [("csc_0", "csp_out", 39)]


def test_without_modules_only_instance_names_match():
    ports = [("csc_0", "dbg_s2_r", 10)]
    assert filter_unaccounted(ports, ["csc.dbg_s2_r"]) == ports
