"""`forge dev hls run` stage stamps: a stage is skipped only when nothing it reads
has changed since its last successful run.

A fake `vitis_hls` stands in for the real tool: it leaves the outputs each
stage normally leaves, records every invocation, and fails csim when a
testbench contains ``return 1;`` (the injected-failure case that used to
pass by output presence alone).
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, NamedTuple

import pytest

from forge.core.cli.main import build_parser

FAKE_VITIS_HLS = textwrap.dedent(
    """\
    #!{python}
    import os, pathlib, re, shutil, sys

    tcl = sys.argv[sys.argv.index("-f") + 1]
    cwd = pathlib.Path.cwd()
    with open(os.environ["FAKE_HLS_CALLS"], "a") as calls:
        calls.write(f"{{cwd.name}} {{tcl}}\\n")

    project = (cwd / "project.tcl").read_text()
    tb_files = re.findall(r'"([^"]+)"', re.search(r"set tb_files\\s+\\{{(.*)\\}}", project).group(1))
    sol = cwd / "solution1"

    if tcl == "project.tcl":
        shutil.rmtree(sol, ignore_errors=True)
        sol.mkdir()
        (sol / "solution1.aps").write_text("")
    elif tcl == "csim.tcl":
        (sol / "csim").mkdir(exist_ok=True)
        if any("return 1;" in pathlib.Path(f).read_text() for f in tb_files):
            print("C-SIMULATION FAILED")
            sys.exit(1)
        print("C-SIMULATION PASS")
    elif tcl == "synth.tcl":
        (sol / "syn" / "report").mkdir(parents=True, exist_ok=True)
        (sol / "syn" / "report" / "adder_csynth.rpt").write_text("")
    elif tcl == "cosim.tcl":
        (sol / "sim").mkdir(exist_ok=True)
        print("C/RTL co-simulation PASS")
    elif tcl == "ip_export.tcl":
        out_dir = re.search(r'set out_dir "([^"]+)"', (cwd / "ip_export.tcl").read_text()).group(1)
        pathlib.Path(out_dir).mkdir(parents=True, exist_ok=True)
        (pathlib.Path(out_dir) / f"{{cwd.name}}.zip").write_text("ip")
    """
)

REGISTRY = textwrap.dedent(
    """\
    registry_version: '1'
    defaults:
      part: xcvu13p-fsga2577-1-e
      clock_period: 4.0
      vendor: Test
      version: '1.0'
    modules:
    - name: adder
      kind: hls
      top: adder
      src: [src/adder.cpp]
      includes: [include]
      build:
        stages: [csim, synth, cosim, export]
      verify:
        tb_src: [src/adder.cpp, tb/tb_adder.cpp]
        tb_args: csim data/manifest.txt
    """
)


class Result(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


class Workspace(NamedTuple):
    root: Path
    registry: Path
    build: Path
    ips: Path
    calls_log: Path
    vitis_hls: Path

    def calls(self) -> List[str]:
        if not self.calls_log.exists():
            return []
        return self.calls_log.read_text().splitlines()


def _cli(capsys: pytest.CaptureFixture[str], *args: str) -> Result:
    parser = build_parser()
    code = 0
    try:
        parsed = parser.parse_args(["hls", *args])
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return Result(code, captured.out, captured.err)


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> Workspace:
    root = tmp_path / "plugin"
    (root / "src").mkdir(parents=True)
    (root / "include").mkdir()
    (root / "tb").mkdir()
    (root / "data").mkdir()
    (root / "src/adder.cpp").write_text('#include "adder.h"\nint adder(int a, int b) { return a + b; }\n')
    (root / "src/adder.h").write_text("int adder(int a, int b);\n")
    (root / "include/common.h").write_text("#define WIDTH 8\n")
    (root / "tb/tb_adder.cpp").write_text("int main() { return 0; }\n")
    (root / "data/events.xml").write_text("<events/>\n")
    (root / "data/manifest.txt").write_text("# golden set\nevents.xml\n")
    registry = root / "modules.yml"
    registry.write_text(REGISTRY)

    vitis_hls = tmp_path / "bin/vitis_hls"
    vitis_hls.parent.mkdir()
    vitis_hls.write_text(FAKE_VITIS_HLS.format(python=sys.executable))
    vitis_hls.chmod(0o755)

    calls_log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_HLS_CALLS", str(calls_log))

    build = tmp_path / "build_hls"
    ips = tmp_path / "ip_packages"
    gen = _cli(
        capsys, "gen-tcl", "adder",
        "--hls-config", str(registry),
        "--output-dir", str(build),
        "--ip-packages-dir", str(ips),
    )
    assert gen.returncode == 0, gen.stderr
    return Workspace(root, registry, build, ips, calls_log, vitis_hls)


def _run(capsys: pytest.CaptureFixture[str], ws: Workspace, stages: str, *extra: str) -> Result:
    return _cli(
        capsys, "run",
        "--registry", str(ws.registry),
        "--modules", "adder",
        "--stages", stages,
        "--hls-build-root", str(ws.build),
        "--ip-packages-dir", str(ws.ips),
        "--vitis-hls", str(ws.vitis_hls),
        *extra,
    )


def test_unchanged_inputs_skip_the_stage(capsys, ws):
    assert _run(capsys, ws, "csim").returncode == 0
    second = _run(capsys, ws, "csim")

    assert second.returncode == 0
    assert "[SKIP] csim   up to date for adder" in second.stdout
    assert ws.calls() == ["adder project.tcl", "adder csim.tcl"]


def test_broken_testbench_fails_in_a_reused_build_root(capsys, ws):
    assert _run(capsys, ws, "csim").returncode == 0
    (ws.root / "tb/tb_adder.cpp").write_text("int main() { return 1; }\n")

    rerun = _run(capsys, ws, "csim")

    assert rerun.returncode == 1
    assert "[FAIL] adder" in rerun.stdout
    assert "[SKIP]" not in rerun.stdout


def test_failed_stage_leaves_no_stamp(capsys, ws):
    (ws.root / "tb/tb_adder.cpp").write_text("int main() { return 1; }\n")
    assert _run(capsys, ws, "csim").returncode == 1

    assert not (ws.build / "adder/.forge_stamps/csim.json").exists()
    assert _run(capsys, ws, "csim").returncode == 1


@pytest.mark.parametrize(
    "edited",
    [
        "src/adder.cpp",
        "src/adder.h",        # header next to a source file
        "include/common.h",   # header under an -I directory
        "data/manifest.txt",
        "data/events.xml",    # listed in the manifest, not named in the TCL
    ],
)
def test_any_input_change_reruns_the_stage(capsys, ws, edited):
    assert _run(capsys, ws, "csim").returncode == 0
    path = ws.root / edited
    path.write_text(path.read_text() + "\n")

    rerun = _run(capsys, ws, "csim")

    assert rerun.returncode == 0
    assert "[SKIP]" not in rerun.stdout
    assert ws.calls().count("adder csim.tcl") == 2


def test_outputs_without_a_stamp_are_not_complete(capsys, ws):
    assert _run(capsys, ws, "csim").returncode == 0
    for stamp in (ws.build / "adder/.forge_stamps").iterdir():
        stamp.unlink()

    rerun = _run(capsys, ws, "csim")

    assert "[SKIP]" not in rerun.stdout
    assert ws.calls().count("adder csim.tcl") == 2


def test_changed_project_script_reinitializes_the_project(capsys, ws):
    assert _run(capsys, ws, "csim,synth").returncode == 0
    ws.registry.write_text(REGISTRY.replace("clock_period: 4.0", "clock_period: 3.0"))
    assert _cli(
        capsys, "gen-tcl", "adder",
        "--hls-config", str(ws.registry),
        "--output-dir", str(ws.build),
        "--ip-packages-dir", str(ws.ips),
    ).returncode == 0

    rerun = _run(capsys, ws, "csim,synth")

    assert rerun.returncode == 0
    assert ws.calls().count("adder project.tcl") == 2
    assert ws.calls().count("adder synth.tcl") == 2


def test_cosim_reruns_a_stale_synth_first(capsys, ws):
    assert _run(capsys, ws, "synth,cosim").returncode == 0
    (ws.root / "src/adder.cpp").write_text("int adder(int a, int b) { return a - b; }\n")

    rerun = _run(capsys, ws, "cosim")

    assert rerun.returncode == 0
    assert ws.calls()[-2:] == ["adder synth.tcl", "adder cosim.tcl"]


def test_force_reinitializes_once_and_reruns_every_stage(capsys, ws):
    assert _run(capsys, ws, "csim,synth,export").returncode == 0
    first = len(ws.calls())

    forced = _run(capsys, ws, "csim,synth,export", "--force")

    assert forced.returncode == 0
    assert ws.calls()[first:] == [
        "adder project.tcl",
        "adder csim.tcl",
        "adder synth.tcl",
        "adder ip_export.tcl",
    ]


def test_summary_json_and_junit_report_each_stage(capsys, ws, tmp_path):
    assert _run(capsys, ws, "csim").returncode == 0
    (ws.root / "tb/tb_adder.cpp").write_text("int main() { return 1; }\n")
    summary = tmp_path / "reports/hls.json"
    junit = tmp_path / "reports/hls.xml"

    result = _run(
        capsys, ws, "synth,csim",
        "--summary-json", str(summary), "--junit-xml", str(junit),
    )

    assert result.returncode == 1
    data = json.loads(summary.read_text())
    assert data["ok"] is False
    assert data["stages"] == ["synth", "csim"]
    assert [(r["stage"], r["status"]) for r in data["results"]] == [
        ("synth", "ok"), ("csim", "failed"),
    ]
    assert data["results"][1]["log"].endswith("logs/csim.log")

    suite = ET.parse(junit).getroot()
    assert suite.get("tests") == "2"
    assert suite.get("failures") == "1"
    failed = [tc.get("name") for tc in suite.iter("testcase") if tc.find("failure") is not None]
    assert failed == ["csim:adder"]


def test_junit_marks_up_to_date_stages_as_skipped(capsys, ws, tmp_path):
    assert _run(capsys, ws, "csim").returncode == 0
    junit = tmp_path / "hls.xml"

    assert _run(capsys, ws, "csim", "--junit-xml", str(junit)).returncode == 0

    suite = ET.parse(junit).getroot()
    assert suite.get("skipped") == "1"
    assert suite.find("testcase/skipped") is not None


def test_build_outputs_are_not_hashed(ws):
    from forge.hls.stage_stamp import stage_inputs

    inputs = stage_inputs(ws.build / "adder", "csim", (ws.build, ws.ips))

    assert all(ws.build not in p.parents for p in inputs if p.name not in {"project.tcl", "csim.tcl"})
    assert (ws.root / "data/events.xml").resolve() in inputs
    assert (ws.root / "include/common.h").resolve() in inputs


def test_run_generates_missing_tcl_from_the_registry(capsys, ws, tmp_path):
    expected = {p.name: p.read_text() for p in (ws.build / "adder").glob("*.tcl")}
    fresh = tmp_path / "fresh_build"
    result = _cli(
        capsys, "run",
        "--registry", str(ws.registry), "--modules", "adder", "--stages", "csim",
        "--hls-build-root", str(fresh), "--ip-packages-dir", str(ws.ips),
        "--vitis-hls", str(ws.vitis_hls),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Generating TCL for adder" in result.stdout
    generated = {p.name: p.read_text() for p in (fresh / "adder").glob("*.tcl")}
    assert generated.keys() == expected.keys()
    assert generated["csim.tcl"] == expected["csim.tcl"].replace(str(ws.build), str(fresh))


def test_run_without_registry_names_the_missing_tcl(capsys, ws, tmp_path):
    fresh = tmp_path / "fresh_build"
    result = _cli(
        capsys, "run",
        "--modules", "adder", "--stages", "csim",
        "--hls-build-root", str(fresh), "--ip-packages-dir", str(ws.ips),
        "--vitis-hls", str(ws.vitis_hls),
    )

    assert result.returncode != 0
    log = (fresh / "adder/logs/project.log").read_text()
    assert "Pass --registry to generate it" in log
