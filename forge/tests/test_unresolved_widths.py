"""A port width the source scan cannot evaluate is never guessed.

Adoption asks for it, `forge check` reports it as an error, `forge build`
refuses to generate, and a width declared under ``modules.<name>.widths`` in
forge.yml settles it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from forge.core.cli.main import build_parser
from forge.project.adopt import plan_adoption
from forge.project.discovery import ProjectDiscovery
from forge.project.status import FAIL, evaluate

_LOOKUP = """module lookup #(
    parameter DEPTH = 16
) (
    input  wire                     clk,
    input  wire                     rst_n,
    input  wire [{addr}]            lookup_addr,
    input  wire                     lookup_addr_valid,
    output reg  [7:0]               lookup_data,
    output reg                      lookup_data_valid
);
    always @(posedge clk) begin
        lookup_data       <= lookup_addr;
        lookup_data_valid <= lookup_addr_valid;
    end
endmodule
"""


def _project(tmp_path: Path, addr: str) -> Path:
    root = tmp_path / "lookup_project"
    (root / "rtl").mkdir(parents=True)
    (root / "rtl" / "lookup.v").write_text(_LOOKUP.format(addr=addr))
    plan_adoption(root).write()
    return root


def _build(root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.chdir(root)
    args = build_parser().parse_args(["build", "--json"])
    try:
        args.func(args)
        code = 0
    except SystemExit as exc:
        code = int(exc.code or 0)
    return code, json.loads(capsys.readouterr().out)


def _declare(root: Path, widths: dict) -> None:
    config = yaml.safe_load((root / "forge.yml").read_text())
    config["modules"] = {"lookup": {"widths": widths}}
    (root / "forge.yml").write_text(yaml.safe_dump(config, sort_keys=False))


def test_a_clog2_address_is_generated_at_its_real_width(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, "$clog2(DEPTH)-1:0")

    code, _ = _build(root, monkeypatch, capsys)

    assert code == 0
    assert "input [3:0] lookup_lookup_addr," in (root / ".forge/generated/algo_top.v").read_text()


def test_a_macro_width_is_asked_about_and_blocks_the_build(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, "`ADDR_W-1:0")

    result = ProjectDiscovery().scan(root)
    unit = next(u for u in result.units if u.name == "lookup")
    assert unit.unresolved_widths == {"lookup_addr": "[`ADDR_W-1:0]"}
    assert "lookup_addr" not in unit.ports
    assert any(a.id == "unsupported:lookup:widths" for a in result.unsupported)

    status = evaluate(root)
    section = next(s for s in status.sections if s.name == "Supported constructs")
    assert section.state == FAIL and "ATG051" in section.codes

    code, envelope = _build(root, monkeypatch, capsys)
    assert code != 0 and envelope["status"] == "fail"
    assert "lookup_addr" in envelope["diagnostics"][0]["message"]
    assert not (root / ".forge/generated/algo_top.v").exists()


def test_a_declared_width_settles_it(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, "`ADDR_W-1:0")
    _declare(root, {"lookup_addr": 4})

    code, _ = _build(root, monkeypatch, capsys)

    assert code == 0
    assert "input [3:0] lookup_lookup_addr," in (root / ".forge/generated/algo_top.v").read_text()
    registry = yaml.safe_load((root / ".forge/project/modules.yml").read_text())
    assert registry["modules"][0]["port_widths"] == {"lookup_addr": 4}


def test_a_declaration_contradicting_the_source_is_refused(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, "`ADDR_W-1:0")
    _declare(root, {"lookup_addr": 4, "lookup_data": 9})

    code, envelope = _build(root, monkeypatch, capsys)

    assert code != 0
    assert "contradicts the source, which gives 8" in envelope["diagnostics"][0]["message"]


def test_widths_must_be_positive_integers(tmp_path):
    root = _project(tmp_path, "7:0")
    _declare(root, {"lookup_addr": "four"})

    from forge.project.config import ForgeConfig
    with pytest.raises(ValueError, match="must be a positive integer"):
        ForgeConfig.load(root)


def test_generation_scans_with_the_instance_parameters(tmp_path):
    from forge.contracts.config import Module
    from forge.contracts.parser import _scan_hdl_fallback

    src = tmp_path / "w.v"
    src.write_text("module w #(parameter W = 8) (input clk, input [W-1:0] d);\nendmodule\n")
    mod = Module(name="w", top="w", src=[str(src)], kind="rtl", parameters={"W": 24})

    ports = {p["name"]: p["width"] for p in _scan_hdl_fallback(mod, tmp_path)["ports"]}

    assert ports == {"clk": 1, "d": 24}


def test_a_packed_array_port_is_generated_at_its_full_width(tmp_path, monkeypatch, capsys):
    root = tmp_path / "packed"
    (root / "rtl").mkdir(parents=True)
    (root / "rtl" / "pair.sv").write_text(
        "module pair (input logic clk, input logic rst_n,\n"
        "  input logic [1:0][7:0] pair_in, input logic pair_in_valid,\n"
        "  output logic [15:0] pair_out, output logic pair_out_valid);\n"
        "endmodule\n")
    plan_adoption(root).write()

    code, _ = _build(root, monkeypatch, capsys)

    assert code == 0
    assert "input [15:0] pair_pair_in," in (root / ".forge/generated/algo_top.v").read_text()
