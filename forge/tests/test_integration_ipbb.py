"""A --mode vhdl build as an ipbb component, and its dep tree compiled in XSim."""

from __future__ import annotations

import hashlib
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

    srcs = _srcs(out / "firmware/cfg/algo_top.dep")
    assert srcs[:2] == ["forge/top/algo_top.vhd", "forge/top/algo_top_core.v"]
    assert {"forge/decimator/decimator.v", "forge/packer/packer.v", "forge/shaper/shaper.v"} <= set(srcs)
    record = json.loads((out / "firmware/hdl/forge/.forge_component.json").read_text())
    assert sorted(record["files"]) == sorted(s[len("forge/"):] for s in srcs)
    for rel, info in record["files"].items():
        assert info["sha256"] == hashlib.sha256((out / "firmware/hdl/forge" / rel).read_bytes()).hexdigest()


def _manifest(tmp_path: Path, modules: dict, files: List[Path], include_dirs=()) -> Path:
    path = tmp_path / "build_manifest.json"
    path.write_text(json.dumps({
        "algorithm_top": str(files[0]), "verilog_files": [str(f) for f in files], "modules": modules,
        "include_dirs": [str(d) for d in include_dirs],
    }))
    return path


def _two_modules(tmp_path: Path, *, include: str = "") -> Path:
    files = [tmp_path / "algo_top.v"]
    files[0].write_text("module algo_top; endmodule\n")
    modules = {}
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        f = tmp_path / name / "fifo.v"
        f.write_text(f"{include}module fifo_{name}; endmodule\n")
        files.append(f)
        modules[name] = {"top": f"fifo_{name}", "verilog_files": [str(f)]}
    return _manifest(tmp_path, modules, files, include_dirs=[tmp_path / "inc"])


def test_a_module_without_rtl_is_an_error(tmp_path: Path) -> None:
    top = tmp_path / "algo_top.v"
    top.write_text("module algo_top; endmodule\n")
    manifest = _manifest(tmp_path, {"dec": {"top": "hit_decoder", "verilog_files": []}}, [top])

    with pytest.raises(ValueError, match="no RTL for hit_decoder"):
        write_component(manifest, tmp_path / "pkg")


def test_files_with_one_name_keep_their_own_module_directory(tmp_path: Path) -> None:
    dep = write_component(_two_modules(tmp_path), tmp_path / "pkg")
    assert set(_srcs(dep)) == {"forge/top/algo_top.v", "forge/a/fifo.v", "forge/b/fifo.v"}
    hdl = tmp_path / "pkg/firmware/hdl/forge"
    assert "fifo_a" in (hdl / "a/fifo.v").read_text() and "fifo_b" in (hdl / "b/fifo.v").read_text()


def test_included_headers_travel_with_their_source(tmp_path: Path) -> None:
    (tmp_path / "inc").mkdir()
    (tmp_path / "inc" / "pkg.vh").write_text("`define W 8\n")
    write_component(_two_modules(tmp_path, include='`include "pkg.vh"\n'), tmp_path / "pkg")
    hdl = tmp_path / "pkg/firmware/hdl/forge"
    assert (hdl / "a/pkg.vh").read_text() == "`define W 8\n" and (hdl / "b/pkg.vh").is_file()
    record = json.loads((hdl / ".forge_component.json").read_text())
    assert record["files"]["a/pkg.vh"]["kind"] == "header"


def test_headers_included_by_headers_travel_at_their_include_paths(tmp_path: Path) -> None:
    (tmp_path / "inc" / "sub").mkdir(parents=True)
    (tmp_path / "inc" / "outer.vh").write_text('`include "sub/nested.vh"\n')
    (tmp_path / "inc" / "sub" / "nested.vh").write_text('`include "../inner.vh"\n')
    (tmp_path / "inc" / "inner.vh").write_text("`define W 8\n")
    write_component(_two_modules(tmp_path, include='`include "outer.vh"\n'), tmp_path / "pkg")
    hdl = tmp_path / "pkg/firmware/hdl/forge"
    assert (hdl / "a/outer.vh").is_file()
    assert (hdl / "a/sub/nested.vh").is_file()
    assert (hdl / "a/inner.vh").read_text() == "`define W 8\n"
    record = json.loads((hdl / ".forge_component.json").read_text())
    assert {"a/outer.vh", "a/sub/nested.vh", "a/inner.vh"} <= set(record["files"])


def test_a_missing_header_at_any_depth_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "inc").mkdir()
    (tmp_path / "inc" / "outer.vh").write_text('`include "gone.vh"\n')
    with pytest.raises(ValueError, match='`include "gone.vh"'):
        write_component(_two_modules(tmp_path, include='`include "outer.vh"\n'), tmp_path / "pkg")


def test_a_header_that_cannot_be_found_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match='`include "missing.vh"'):
        write_component(_two_modules(tmp_path, include='`include "missing.vh"\n'), tmp_path / "pkg")


def test_a_second_export_replaces_only_what_forge_wrote(tmp_path: Path) -> None:
    manifest = _two_modules(tmp_path)
    pkg = tmp_path / "pkg"
    dep = write_component(manifest, pkg)
    first = {p.relative_to(pkg): p.read_bytes() for p in pkg.rglob("*") if p.is_file()}
    (pkg / "firmware/hdl/user_glue.vhd").write_text("-- written by hand\n")
    (pkg / "firmware/cfg/top.dep").write_text("include algo_top.dep\n")

    assert write_component(manifest, pkg) == dep
    second = {p.relative_to(pkg): p.read_bytes() for p in pkg.rglob("*") if p.is_file()}
    assert second.pop(Path("firmware/hdl/user_glue.vhd")) == b"-- written by hand\n"
    assert second.pop(Path("firmware/cfg/top.dep")) == b"include algo_top.dep\n"
    assert second == first
    assert not [p for p in (pkg / "firmware/hdl").iterdir() if p.name.startswith(".forge-")]


@pytest.mark.parametrize("foreign", ["firmware/hdl/forge/mine.v", "firmware/cfg/algo_top.dep"])
def test_a_directory_or_dep_file_forge_did_not_write_is_refused(tmp_path: Path, foreign: str) -> None:
    pkg = tmp_path / "pkg"
    (pkg / foreign).parent.mkdir(parents=True)
    (pkg / foreign).write_text("kept\n")
    with pytest.raises(ValueError, match="not written by forge"):
        write_component(_two_modules(tmp_path), pkg)
    assert (pkg / foreign).read_text() == "kept\n"


def test_a_source_ipbb_cannot_take_is_refused(tmp_path: Path) -> None:
    top = tmp_path / "algo_top.v"
    top.write_text("module algo_top; endmodule\n")
    ip = tmp_path / "fifo.xci"
    ip.write_text("{}")
    with pytest.raises(ValueError, match="not .xci"):
        write_component(_manifest(tmp_path, {}, [top, ip]), tmp_path / "pkg")


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


def test_a_vhdl_file_compiles_after_the_packages_it_uses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = {
        "choice.vhd": "use work.speed_pkg.all;\npackage choice_pkg is\nend package;\n",
        "other.v": "module other; endmodule\n",
        "speed.vhd": "-- package speed_pkg is not this\npackage speed_pkg is\nend package;\n",
        "top.vhd": "use work.choice_pkg.all;\nentity top is end;\narchitecture a of top is begin\n"
                   "u : entity work.leaf; end;\n",
        "leaf.vhd": "entity leaf is end;\n",
    }
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    sources = [_Src(str(tmp_path / name)) for name in files]

    class Parser:
        def __init__(self, toolset: str, pathmaker: object) -> None:
            self.commands = {"src": sources}
            self.unresolved: list = []

        def parse(self, *args: str) -> None:
            pass

    stub = types.ModuleType("ipbb.depparser")
    stub.DepFileParser = Parser  # type: ignore[attr-defined]
    stub.Pathmaker = lambda *a: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ipbb", types.ModuleType("ipbb"))
    monkeypatch.setitem(sys.modules, "ipbb.depparser", stub)

    order = [Path(f).name for f in xsim_compile_script(tmp_path, "p:c", "top.dep").split() if "." in Path(f).name]
    assert order.index("speed.vhd") < order.index("choice.vhd") < order.index("top.vhd")
    assert order.index("leaf.vhd") < order.index("top.vhd")
    assert order.index("other.v") < order.index("speed.vhd")


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
