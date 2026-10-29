"""Register maps and their exports (forge.integration.registers)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from forge.integration.registers import FakeBus, RegisterMap, to_blobfish, to_ipbus_xml, to_markdown

_MAP = {"registers": [
    {"name": "control", "kind": "config", "width": 64, "reset": 0x5, "port": "cfg_control",
     "clock_domain": "logic_clk",
     "fields": [{"name": "enable", "bits": 0}, {"name": "mode", "bits": [3, 1]},
                {"name": "threshold", "bits": [47, 32], "description": "hits"}]},
    {"name": "lock", "kind": "status", "width": 32, "net": "lock_status",
     "fields": [{"name": "locked", "bits": 0}, {"name": "latency", "bits": [31, 16]}]},
    {"name": "drops", "kind": "counter", "width": 32, "net": "drop_count", "clear_on_read": True},
]}


def _map(**change):
    return RegisterMap.from_dict({"registers": [dict(_MAP["registers"][0], **change)]})


def test_ipbus_table_has_one_node_per_word_and_masked_fields():
    root = ET.fromstring(to_ipbus_xml(RegisterMap.from_dict(_MAP)))
    nodes = {n.get("id"): n for n in root}
    assert list(nodes) == ["control_w0", "control_w1", "lock", "drops"]
    assert [nodes[n].get("address") for n in nodes] == ["0x00000000", "0x00000001", "0x00000002", "0x00000003"]
    assert nodes["control_w0"].get("permission") == "rw" and nodes["lock"].get("permission") == "r"
    assert {f.get("id"): f.get("mask") for f in nodes["control_w0"]} == {"enable": "0x00000001", "mode": "0x0000000e"}
    assert {f.get("id"): f.get("mask") for f in nodes["control_w1"]} == {"threshold": "0x0000ffff"}
    assert {f.get("id"): f.get("mask") for f in nodes["lock"]} == {"locked": "0x00000001", "latency": "0xffff0000"}


def test_blobfish_services_place_config_and_readback_words():
    out = to_blobfish(RegisterMap.from_dict(_MAP), slr=1)
    assert out["control_policies"] == {
        "config_words": {"cfg_control": {"register": "slr1_config_registers_in", "word": 0}},
        "status_words": {"slr1_readback_registers_out": [
            {"net": "lock_status", "width": 32}, {"zero": 32}, {"net": "drop_count", "width": 32}, {"zero": 32}]},
    }
    assert [w["word"] for w in out["words"]] == [0, 0, 1]


def test_markdown_lists_access_reset_and_fields():
    md = to_markdown(RegisterMap.from_dict(_MAP))
    assert "| `control` | 0-1 | config | rw | 64 | 0x5 | logic_clk | enable [0], mode [3:1], threshold [47:32] |" in md
    assert "| `drops` | 3 | counter | r, clears on read | 32 | 0x0 | - | - |" in md


def test_the_fake_bus_keeps_the_maps_semantics():
    regs = RegisterMap.from_dict(_MAP)
    bus = FakeBus(regs)
    assert bus.read(0) == 0x5
    bus.write(1, 0x1234)
    assert bus.values["control"] == 0x0000123400000005
    with pytest.raises(PermissionError, match="lock is a status register"):
        bus.write(2, 1)
    bus.hardware_sets("drops", 7)
    assert bus.read(3) == 7 and bus.read(3) == 0
    bus.hardware_sets("lock", 0x00290001)
    root = ET.fromstring(to_ipbus_xml(regs))
    latency = int(next(f for n in root if n.get("id") == "lock" for f in n if f.get("id") == "latency").get("mask"), 16)
    assert (bus.read(2) & latency) >> 16 == 0x29


@pytest.mark.parametrize("change, message", [
    ({"kind": "register"}, "kind must be one of"),
    ({"fields": [{"name": "a", "bits": [3, 0]}, {"name": "b", "bits": [4, 2]}]}, "fields a and b overlap"),
    ({"fields": [{"name": "a", "bits": 64}]}, "outside a 64-bit register"),
    ({"reset": 1 << 64}, "does not fit 64 bits"),
    ({"clear_on_read": True}, "only a counter clears on read"),
    ({"net": "x"}, "a config register drives a port"),
])
def test_bad_registers_are_refused(change, message):
    with pytest.raises(ValueError, match=message):
        _map(**change)


def test_a_status_register_driving_a_port_is_refused():
    with pytest.raises(ValueError, match="reads a net, it drives no port"):
        RegisterMap.from_dict({"registers": [{"name": "s", "kind": "status", "port": "p"}]})


def test_a_duplicate_register_is_refused():
    with pytest.raises(ValueError, match="declared twice: control"):
        RegisterMap.from_dict({"registers": [_MAP["registers"][0]] * 2})


@pytest.mark.parametrize("change, export, message", [
    ({"fields": [{"name": "w", "bits": [40, 20]}]}, to_ipbus_xml, "crosses a 32-bit word"),
    ({"atomic": True}, to_ipbus_xml, "atomic and 64 bits"),
    ({"width": 96}, lambda m: to_blobfish(m, slr=0), "one register is one 64-bit word"),
    ({"port": ""}, lambda m: to_blobfish(m, slr=0), "names no algorithm port"),
])
def test_what_a_bus_cannot_carry_is_refused(change, export, message):
    with pytest.raises(ValueError, match=message):
        export(_map(**change))


def test_the_command_writes_each_export(tmp_path, capsys):
    import yaml
    from forge.core.cli.main import build_parser

    regs = tmp_path / "regs.yml"
    regs.write_text(yaml.safe_dump(_MAP))
    args = build_parser().parse_args(["dev", "framework", "registers", "--map", str(regs),
                                      "--ipbus", str(tmp_path / "t.xml"), "--blobfish", str(tmp_path / "b.yml"),
                                      "--slr", "0", "--doc", str(tmp_path / "r.md")])
    args.func(args)
    assert ET.parse(tmp_path / "t.xml").getroot().get("id") == "payload"
    assert "config_words" in yaml.safe_load((tmp_path / "b.yml").read_text())["control_policies"]
    assert "| `lock` |" in (tmp_path / "r.md").read_text()
