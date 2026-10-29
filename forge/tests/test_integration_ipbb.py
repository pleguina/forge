"""A --mode vhdl build as an ipbb component, and its dep tree compiled in XSim."""

from __future__ import annotations

import json
import shutil
import sys
import types
from pathlib import Path
from typing import List

import pytest

from forge.core.cli.main import build_parser
from forge.integration.ipbb import write_component, xsim_compile_script
from forge.project.adopt import plan_adoption

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "foreign" / "simple_pipeline"


def _cli(capsys: pytest.CaptureFixture[str], *args: str) -> int:
    parsed = build_parser().parse_args(list(args))
    code = 0
    try:
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    return code


@pytest.fixture
def vhdl_build(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    project = tmp_path / "simple_pipeline"
    shutil.copytree(FIXTURE, project)
    plan_adoption(project).write()
    _cli(
        capsys, "build", str(project / ".forge/project/design.yml"),
        "--contracts-from", str(project / ".forge/project/modules.yml"),
        "--consumer-root", str(project), "--mode", "vhdl",
        "--output", str(project / "out" / "algo_top.vhd"),
    )
    return project / "out"


def _srcs(dep: Path) -> List[str]:
    return [line.split()[1] for line in dep.read_text().splitlines() if line.startswith("src ")]


def test_the_manifest_lists_the_vhdl_entity(vhdl_build: Path) -> None:
    manifest = json.loads((vhdl_build / "build_manifest.json").read_text())

    assert manifest["vhdl_files"] == [str((vhdl_build / "algo_top.vhd").resolve())]
    assert Path(manifest["algorithm_top"]).name == "algo_top_core.v"


def test_component_lists_the_entity_first_for_ipbb(
    vhdl_build: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "pkg" / "algo"
    _cli(capsys, "dev", "framework", "ipbb-component",
         "--manifest", str(vhdl_build / "build_manifest.json"), "--out", str(out))

    dep = out / "firmware/cfg/algo_top.dep"
    manifest = json.loads((vhdl_build / "build_manifest.json").read_text())
    top, *modules = [Path(p).name for p in manifest["verilog_files"]]
    expected = ["algo_top.vhd", top, *reversed(modules)]
    assert _srcs(dep) == expected
    assert {"decimator.v", "packer.v", "shaper.v"} <= set(expected)
    assert sorted(p.name for p in (out / "firmware/hdl").iterdir()) == sorted(expected)


def _manifest(tmp_path: Path, modules: dict, files: List[Path]) -> Path:
    path = tmp_path / "build_manifest.json"
    path.write_text(json.dumps({
        "algorithm_top": str(files[0]), "verilog_files": [str(f) for f in files], "modules": modules,
    }))
    return path


def test_a_module_without_rtl_is_an_error(tmp_path: Path) -> None:
    top = tmp_path / "algo_top.v"
    top.write_text("module algo_top; endmodule\n")
    manifest = _manifest(tmp_path, {"dec": {"top": "hit_decoder", "verilog_files": []}}, [top])

    with pytest.raises(ValueError, match="no RTL for hit_decoder"):
        write_component(manifest, tmp_path / "pkg")


def test_two_sources_with_one_name_are_an_error(tmp_path: Path) -> None:
    files = []
    for sub in ("a", "b"):
        (tmp_path / sub).mkdir()
        files.append(tmp_path / sub / "fifo.v")
        files[-1].write_text("module fifo; endmodule\n")
    manifest = _manifest(tmp_path, {}, files)

    with pytest.raises(ValueError, match="fifo.v"):
        write_component(manifest, tmp_path / "pkg")


class _Src:
    def __init__(self, filepath: str, lib: str = "", vhdl2008: bool = False, sim: bool = True) -> None:
        self.filepath, self.lib, self.vhdl2008, self.useInSim = filepath, lib, vhdl2008, sim


def test_compile_calls_follow_ipbb_order_library_and_standard(monkeypatch: pytest.MonkeyPatch) -> None:
    sources = [
        _Src("/s/pkg.vhd"), _Src("/s/core.v"), _Src("/s/glue.v"), _Src("/s/top.vhd"),
        _Src("/s/strings.vhdl", lib="extras"), _Src("/s/tb.vhd", vhdl2008=True),
        _Src("/s/impl_only.xdc"), _Src("/s/synth_only.vhd", sim=False),
    ]

    class Parser:
        def __init__(self, toolset: str, pathmaker: object) -> None:
            self.commands = {"src": sources}
            self.unresolved: list = []

        def parse(self, package: str, component: str, depfile: str) -> None:
            assert (package, component, depfile) == ("forge", "emp", "top.dep")

    stub = types.ModuleType("ipbb.depparser")
    stub.DepFileParser = Parser  # type: ignore[attr-defined]
    stub.Pathmaker = lambda *a: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ipbb", types.ModuleType("ipbb"))
    monkeypatch.setitem(sys.modules, "ipbb.depparser", stub)

    assert xsim_compile_script(Path("/w/src"), "forge:emp", "top.dep").splitlines() == [
        "set -e",
        "xvhdl -work xil_defaultlib /s/pkg.vhd",
        "xvlog -work xil_defaultlib /s/core.v /s/glue.v",
        "xvhdl -work xil_defaultlib /s/top.vhd",
        "xvhdl -work extras /s/strings.vhdl",
        "xvhdl --2008 -work xil_defaultlib /s/tb.vhd",
    ]


def test_ipbb_resolves_the_component_from_a_work_area(
    vhdl_build: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("ipbb.depparser")
    src = tmp_path / "work" / "src"
    write_component(vhdl_build / "build_manifest.json", src / "gen" / "algo")
    user = src / "user" / "payload" / "firmware"
    (user / "cfg").mkdir(parents=True)
    (user / "hdl").mkdir()
    (user / "hdl" / "payload.vhd").write_text("entity payload is end;\n")
    (user / "cfg" / "top.dep").write_text("include -c gen:algo algo_top.dep\nsrc payload.vhd\n")

    monkeypatch.chdir(tmp_path / "work")
    script = xsim_compile_script(Path("src"), "user:payload", "top.dep")

    order = [line.rsplit("/", 1)[-1] for line in script.split()]
    assert order.index("decimator.v") < order.index("algo_top_core.v") < order.index("algo_top.vhd")
    assert order.index("algo_top.vhd") > order.index("payload.vhd")
    assert "xvlog -work xil_defaultlib" in script
    assert all(Path(f).is_absolute() for f in script.split() if f.endswith((".v", ".vhd")))
