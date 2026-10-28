"""``variant_of:`` — a design written as the changes it makes to another."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from forge.contracts.config import DesignConfig
from forge.contracts.design_variant import design_sources, load_design_document

BASE = {
    "part": "xcvu13p",
    "clock_period": 4.0,
    "modules": [
        {"name": "src", "top": "src_top", "src": ["src.v"]},
        {"name": "dly", "top": "dly_top", "src": ["dly.v"]},
        {"name": "dst", "top": "dst_top", "src": ["dst.v"], "external_out_ports": ["mon"]},
        {"name": "cfg", "top": "cfg_top", "src": ["cfg.v"]},
    ],
    "connections": [
        {"from": "src", "to": "dly", "port_map": [["dout", "din"]]},
        {"from": "dly", "to": "dst", "port_map": [["dout", "din"]]},
        {"from": "src", "to": ["dst", "cfg"], "port_map": [["side", "side"]]},
    ],
    "topology_groups": [
        {"name": "cfg_to_dst", "family": "configuration", "from": "cfg", "to": "dst"},
        {"name": "cfg_to_dly", "family": "configuration", "from": "cfg", "to": "dly"},
    ],
    "allowed_unconnected": {"open_outputs": ["dst.mon"]},
}


def _write(path: Path, document: dict) -> Path:
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    return path


@pytest.fixture
def base(tmp_path: Path) -> Path:
    for name in ("src", "dly", "dst", "cfg"):
        (tmp_path / f"{name}.v").write_text(f"module {name}_top(); endmodule\n")
    return _write(tmp_path / "design.yml", BASE)


def test_a_variant_applies_its_changes_to_the_base(base: Path) -> None:
    variant = _write(base.parent / "design.sim.yml", {
        "variant_of": "design.yml",
        "remove": {"modules": ["dly"], "topology_groups": ["cfg_to_dst"]},
        "modules": {"dst": {"external_out_ports": None, "external_in_ports": ["din"]}},
        "allowed_unconnected": {},
    })

    design = load_design_document(variant)

    assert [m["name"] for m in design["modules"]] == ["src", "dst", "cfg"]
    dst = design["modules"][1]
    assert "external_out_ports" not in dst and dst["external_in_ports"] == ["din"]
    # Everything touching the removed module goes with it.
    assert design["connections"] == [BASE["connections"][2]]
    assert design["topology_groups"] == []
    assert design["allowed_unconnected"] == {}
    assert "variant_of" not in design
    assert load_design_document(base) == BASE


def test_add_appends_to_the_base_lists_and_merges_its_mappings(base: Path) -> None:
    variant = _write(base.parent / "design.sim.yml", {
        "variant_of": "design.yml",
        "add": {
            "allowed_unconnected": {"open_outputs": ["src.dbg"], "tied_inputs": ["dst.cfg"]},
            "modules": [{"name": "probe", "top": "probe_top", "src": ["probe.v"]}],
        },
    })

    design = load_design_document(variant)

    assert design["allowed_unconnected"] == {
        "open_outputs": ["dst.mon", "src.dbg"], "tied_inputs": ["dst.cfg"],
    }
    assert design["modules"][-1]["name"] == "probe"


def test_add_cannot_change_a_value(base: Path) -> None:
    variant = _write(base.parent / "design.sim.yml", {
        "variant_of": "design.yml", "add": {"clock_period": 2.0},
    })

    with pytest.raises(ValueError, match="clock_period is not a list or mapping"):
        load_design_document(variant)


def test_a_fan_out_connection_loses_only_the_destination_named(base: Path) -> None:
    variant = _write(base.parent / "design.sim.yml", {
        "variant_of": "design.yml",
        "remove": {"connections": [{"from": "src", "to": "cfg"}]},
    })

    connections = load_design_document(variant)["connections"]

    assert connections[2]["to"] == ["dst"]


def test_design_config_loads_a_variant(base: Path) -> None:
    variant = _write(base.parent / "design.sim.yml", {
        "variant_of": "design.yml", "remove": {"modules": ["dly"]},
    })

    cfg = DesignConfig.load(variant)

    assert [m.name for m in cfg.modules] == ["src", "dst", "cfg"]


@pytest.mark.parametrize("change, message", [
    ({"remove": {"modules": ["gone"]}}, "no module named gone"),
    ({"remove": {"topology_groups": ["gone"]}}, "no topology group named gone"),
    ({"remove": {"connections": [{"from": "dst", "to": "src"}]}}, "no connection dst -> src"),
    ({"remove": {"ports": ["x"]}}, "not ports"),
    ({"modules": {"gone": {"top": "x"}}}, "'gone' is not a module"),
])
def test_a_name_the_base_does_not_have_is_an_error(base: Path, change: dict, message: str) -> None:
    variant = _write(base.parent / "design.sim.yml", {"variant_of": "design.yml", **change})

    with pytest.raises(ValueError, match=message):
        load_design_document(variant)


def test_the_base_must_sit_in_the_same_directory(base: Path) -> None:
    (base.parent / "sim").mkdir()
    variant = _write(base.parent / "sim" / "design.sim.yml", {"variant_of": "../design.yml"})

    with pytest.raises(ValueError, match="same directory"):
        load_design_document(variant)


def test_a_loop_is_an_error(tmp_path: Path) -> None:
    _write(tmp_path / "a.yml", {"variant_of": "b.yml"})
    _write(tmp_path / "b.yml", {"variant_of": "a.yml"})

    with pytest.raises(ValueError, match="loops back"):
        load_design_document(tmp_path / "a.yml")


def test_a_variant_goes_stale_with_its_base(base: Path) -> None:
    from forge.core.stale_detection import check_top_gen_staleness

    variant = _write(base.parent / "design.sim.yml", {"variant_of": "design.yml"})
    out = base.parent / "out"
    out.mkdir()
    manifest = out / "build_manifest.json"
    manifest.write_text("{}")
    old = manifest.stat().st_mtime - 100
    os.utime(variant, (old, old))
    os.utime(base, (old + 200, old + 200))

    assert design_sources(variant) == [variant.resolve(), base.resolve()]
    report = check_top_gen_staleness(out, design_yml=variant)
    assert any(s.source == base.resolve() for s in report.stale)
