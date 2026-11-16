"""The sources a project already describes, compiled as it describes them.

``verification.sources`` reads a ``.f`` list, an ipbb dep tree or Hog list
files; each file keeps its language, library and VHDL standard, so a mixed
VHDL-2008 / SystemVerilog design with a non-default library, a top
parameter, an include file, a define and a ROM simulates with no second
source list, and a change to any of them makes its result out of date.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from forge.core.cli.main import build_parser
from forge.integration.compile_list import from_filelist, from_hog
from forge.verification import auto

needs_xsim = pytest.mark.skipif(shutil.which("xelab") is None, reason="XSim not on PATH")

SCALE = """library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library algo_lib;
use algo_lib.gains.all;

entity scale is
  generic (GAIN : natural := 1);
  port (clk : in std_logic; d : in std_logic_vector(7 downto 0); q : out std_logic_vector(15 downto 0));
end entity;

architecture rtl of scale is
begin
  process (all)   -- VHDL-2008
  begin
    q <= std_logic_vector(resize(unsigned(d) * (GAIN + EXTRA), 16));
  end process;
end architecture;
"""

GAINS = """package gains is
  constant EXTRA : natural := 0;
end package;
"""

TOP = """`include "defs.svh"
module top #(parameter int OFFSET = 0) (
  input clk, input rst,
  input [7:0] x, input x_valid,
  output reg [15:0] y, output reg y_valid);
  logic [15:0] scaled;
  logic [15:0] rom [0:3];
  initial $readmemh("rom.dat", rom);
  scale #(.GAIN(`GAIN)) u_scale (.clk(clk), .d(x), .q(scaled));
  always @(posedge clk) begin
    y_valid <= x_valid & !rst;
    y <= scaled + rom[x[1:0]] + OFFSET + `BIAS;
  end
endmodule
"""


def _hog_repo(root: Path) -> Path:
    hdl = root / "hdl"
    (hdl / "vhdl").mkdir(parents=True)
    (hdl / "vhdl" / "scale.vhd").write_text(SCALE)
    (hdl / "vhdl" / "gains.vhd").write_text(GAINS)
    (hdl / "top.sv").write_text(TOP)
    (hdl / "defs.svh").write_text("`define GAIN 3\n")
    (hdl / "rom.dat").write_text("0000\n0010\n0020\n0030\n")
    (hdl / "ip.xci").write_text("<xci/>\n")
    lists = root / "Top" / "algo" / "list"
    lists.mkdir(parents=True)
    (lists / "algo_lib.src").write_text("# the algorithm, VHDL-2008 by default\nhdl/vhdl/*.vhd\n")
    (lists / "top.src").write_text("hdl/top.sv SystemVerilog top=top\nhdl/ip.xci locked\nhdl/tb_only.sv nosim\n")
    return root


def test_hog_lists_give_libraries_standards_and_compile_order(tmp_path):
    repo = _hog_repo(tmp_path)
    (repo / "Top" / "algo" / "list" / "old.src").write_text("hdl/legacy.vhd 93\n")
    (repo / "hdl" / "legacy.vhd").write_text("entity legacy is end;\n")

    described = from_hog(repo, "algo")

    by_name = {f.path.name: f for f in described.files}
    assert by_name["scale.vhd"].library == "algo_lib" and by_name["scale.vhd"].standard == "2008"
    assert by_name["legacy.vhd"].library == "old" and by_name["legacy.vhd"].standard == "93"
    assert by_name["top.sv"].language == "systemverilog" and by_name["top.sv"].library == "work"
    names = [f.path.name for f in described.files]
    assert names.index("gains.vhd") < names.index("scale.vhd")       # the package before its user
    assert "tb_only.sv" not in names and [Path(u).name for u in described.unresolved] == ["ip.xci"]


def test_a_nested_hog_list_takes_its_own_library_or_lib(tmp_path):
    repo = _hog_repo(tmp_path)
    lists = repo / "Top" / "algo" / "list"
    (lists / "algo_lib.src").write_text("Top/algo/list/sub/extra.src lib=helpers\nhdl/vhdl/*.vhd\n")
    (lists / "sub").mkdir()
    (lists / "sub" / "extra.src").write_text("hdl/helper.vhd\n")
    (repo / "hdl" / "helper.vhd").write_text("entity helper is end;\n")

    by_name = {f.path.name: f for f in from_hog(repo, "algo").files}

    assert by_name["helper.vhd"].library == "helpers" and by_name["scale.vhd"].library == "algo_lib"


def test_a_filelist_keeps_its_order_and_reports_what_it_cannot_compile(tmp_path):
    (tmp_path / "a.vhd").write_text("")
    (tmp_path / "b.sv").write_text("")
    (tmp_path / "c.xci").write_text("")
    (tmp_path / "top.f").write_text("a.vhd\nb.sv\nc.xci\n+define+X=1\n")

    described = from_filelist(tmp_path / "top.f")

    assert [(f.path.name, f.language) for f in described.files] == [("a.vhd", "vhdl"), ("b.sv", "systemverilog")]
    assert described.defines == {"X": "1"} and [Path(u).name for u in described.unresolved] == ["c.xci"]


def _project(root: Path) -> None:
    _hog_repo(root)
    (root / "input.json").write_text(json.dumps({"x": [1, 2, 3]}))
    # 3x + rom[x & 3] + OFFSET 5 + BIAS 100
    (root / "expected.json").write_text(json.dumps({"y": [3 + 0x10 + 105, 6 + 0x20 + 105, 9 + 0x30 + 105]}))
    (root / "forge.yml").write_text(yaml.safe_dump({
        "project": "mixed", "top": "top", "clock": {"port": "clk"}, "reset": {"port": "rst", "active": "high"},
        "verification": {
            "target": "existing", "backends": ["xsim"],
            "sources": {"provider": "hog", "project": "algo"},
            "defines": {"BIAS": "100"}, "parameters": {"OFFSET": 5},
            "input": "input.json", "expected": "expected.json"}}))


def _plan(root: Path):
    from forge.project.config import ForgeConfig

    config = ForgeConfig.load(root)
    project = SimpleNamespace(root=root, config=config)
    return auto.plan(project, spec=dict(config.verification_spec)).levels[0]


def test_each_compile_input_of_a_described_project_is_in_its_identity(tmp_path):
    _project(tmp_path)
    level = _plan(tmp_path)
    assert level.compile_units[str(tmp_path / "hdl/vhdl/scale.vhd")] == {
        "language": "vhdl", "library": "algo_lib", "standard": "2008"}
    before = auto.plan_parts(level)

    (tmp_path / "Top/algo/list/algo_lib.src").write_text("hdl/vhdl/gains.vhd\nhdl/vhdl/scale.vhd 93\n")
    assert auto.plan_parts(_plan(tmp_path))["compile"] != before["compile"]

    config = yaml.safe_load((tmp_path / "forge.yml").read_text())
    config["verification"]["parameters"] = {"OFFSET": 6}
    (tmp_path / "forge.yml").write_text(yaml.safe_dump(config))
    (tmp_path / "Top/algo/list/algo_lib.src").write_text("hdl/vhdl/*.vhd\n")
    after = auto.plan_parts(_plan(tmp_path))
    assert after["compile"] != before["compile"] and after["run"] == before["run"]


def test_the_xsim_commands_keep_each_files_library_and_standard(tmp_path):
    _project(tmp_path)
    level = _plan(tmp_path)

    cmds = auto._compile_commands("xsim", level, tmp_path / "tb.sv", tmp_path)

    vhdl = [c for c in cmds if c[0] == "xvhdl"]
    assert vhdl and all(c[1:4] == ["--2008", "-work", "algo_lib"] for c in vhdl)
    assert "-L" in cmds[-1] and cmds[-1][cmds[-1].index("-L") + 1] == "algo_lib"


@needs_xsim
def test_a_mixed_language_project_simulates_from_its_hog_lists(tmp_path, capsys, monkeypatch):
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(["test", "--json"])
    try:
        args.func(args)
    except SystemExit:
        pass
    capsys.readouterr()
    evidence = json.loads(next((tmp_path / ".forge/reports/test/auto").glob("*.evidence.json")).read_text())

    assert evidence["status"] == "VERIFIED", evidence.get("reason") or evidence["interfaces"]
    assert [Path(f["path"]).name for f in evidence["memory_files"]] == ["rom.dat"]

    from forge.project.verification_state import latest_results
    (tmp_path / "hdl/rom.dat").write_text("0000\n0011\n0020\n0030\n")
    (record,) = latest_results(tmp_path / ".forge/reports")
    assert "the memory file rom.dat" in record.stale
