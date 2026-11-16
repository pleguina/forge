"""`forge init` in a repository with firmware: one forge.yml, nothing else."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from forge.core.cli.main import build_parser

TOP = """module payload_top(input clk, input rst_n, input [7:0] x, input x_valid,
                   output reg [7:0] y, output reg y_valid);
  always @(posedge clk) begin y <= x + 8'd1; y_valid <= x_valid & rst_n; end
endmodule
"""


def _init(capsys, root: Path, *argv: str):
    args = build_parser().parse_args(["init", "--consumer-root", str(root), "--json", *argv])
    code = 0
    try:
        args.func(args)
    except SystemExit as exc:
        code = int(exc.code or 0)
    return code, json.loads(capsys.readouterr().out)


def _files(root: Path):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def test_init_writes_only_forge_yml_from_what_it_finds(tmp_path, capsys):
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "payload_top.v").write_text(TOP)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "expected.json").write_text("{}")
    before = _files(tmp_path)

    code, doc = _init(capsys, tmp_path, "--expected", "data/expected.json")

    assert code == 0, doc
    assert _files(tmp_path) == sorted([*before, "forge.yml"])
    config = yaml.safe_load((tmp_path / "forge.yml").read_text())
    assert config["top"] == "payload_top"
    assert config["clock"]["port"] == "clk" and config["reset"] == {"port": "rst_n", "active": "low"}
    assert config["verification"]["target"] == "existing"
    assert config["verification"]["expected"] == "data/expected.json"


def test_init_reads_a_hog_project_and_asks_for_what_is_missing(tmp_path, capsys):
    (tmp_path / "hdl").mkdir()
    (tmp_path / "hdl" / "payload_top.v").write_text(TOP)
    (tmp_path / "Top" / "algo" / "list").mkdir(parents=True)
    (tmp_path / "Top" / "algo" / "list" / "top.src").write_text("hdl/payload_top.v\n")

    code, doc = _init(capsys, tmp_path)

    config = yaml.safe_load((tmp_path / "forge.yml").read_text())
    assert config["verification"]["sources"] == {"provider": "hog", "project": "algo"}
    assert doc["status"] == "warn" and any("NOT_VERIFIED_NO_ORACLE" in m for m in doc["metrics"]["missing"])


def test_init_does_not_replace_a_forge_yml(tmp_path, capsys):
    (tmp_path / "forge.yml").write_text("project: mine\n")
    code, doc = _init(capsys, tmp_path)
    assert code != 0 and (tmp_path / "forge.yml").read_text() == "project: mine\n"


def test_a_plugin_name_without_example_is_pointed_at_it(tmp_path, capsys):
    code, doc = _init(capsys, tmp_path, "my_plugin")
    assert code != 0 and "forge init --example my_plugin" in doc["diagnostics"][0]["message"]
    assert not (tmp_path / "forge.yml").exists()
