"""Active-low module resets on the active-high global ``ap_rst``.

The foreign ``simple_pipeline`` fixture resets all three blocks with
``rst_n``. Wiring that pin straight to ``ap_rst`` elaborates cleanly and
holds the pipeline in reset exactly when the top level releases it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from forge.contracts.domains import reset_is_active_low
from forge.core.cli.main import build_parser
from forge.project.adopt import plan_adoption

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "foreign" / "simple_pipeline"
MODULES = ("decimator", "packer", "shaper")


def _build(capsys: pytest.CaptureFixture[str], project: Path, mode: str, output: Path) -> str:
    parser = build_parser()
    parsed = parser.parse_args([
        "build", str(project / ".forge/project/design.yml"),
        "--contracts-from", str(project / ".forge/project/modules.yml"),
        "--consumer-root", str(project),
        "--mode", mode, "--output", str(output),
    ])
    code = 0
    try:
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    return output.read_text()


@pytest.fixture
def adopted(tmp_path: Path) -> Path:
    destination = tmp_path / "simple_pipeline"
    shutil.copytree(FIXTURE, destination)
    plan_adoption(destination).write()
    return destination


def test_verilog_drives_an_active_low_reset_with_ap_rst_inverted(
    adopted: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    top = _build(capsys, adopted, "verilog", adopted / "out" / "algo_top.v")

    assert top.count(".rst_n(~ap_rst)") == len(MODULES)
    assert ".rst_n(ap_rst)" not in top


def test_the_vhdl_entity_wraps_the_verilog_top_and_its_inverted_reset(
    adopted: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    entity = _build(capsys, adopted, "vhdl", adopted / "out" / "algo_top.vhd")
    core = (adopted / "out" / "algo_top_core.v").read_text()

    assert "entity algo_top is" in entity
    assert "  core : algo_top_core" in entity
    assert "ap_rst : in std_logic" in entity
    assert core.count(".rst_n(~ap_rst)") == len(MODULES)


def test_block_design_drives_an_active_low_reset_through_one_inverter(
    adopted: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    tcl = _build(capsys, adopted, "bd", adopted / "out" / "top_bd.tcl")

    assert tcl.count("util_vector_logic:2.0 ap_rst_inv") == 1
    assert "connect_bd_net [get_bd_ports ap_rst] [get_bd_pins ap_rst_inv/Op1]" in tcl
    for module in MODULES:
        assert f"[get_bd_pins ap_rst_inv/Res] [get_bd_pins {module}/rst_n]" in tcl
        assert f"[get_bd_ports ap_rst] [get_bd_pins {module}/rst_n]" not in tcl


# rst_n is each module's reset_primary role; presetn is not one of the
# names that role is inferred from, so it keeps a role of its own.
@pytest.mark.parametrize("fixture, port", [
    ("simple_pipeline", "rst_n"), ("peripheral_subsystem", "presetn"),
])
def test_forge_yml_states_the_level_when_the_name_misleads(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], fixture: str, port: str,
) -> None:
    project = tmp_path / fixture
    shutil.copytree(FIXTURE.parent / fixture, project)
    plan_adoption(project).write()
    low = _build(capsys, project, "verilog", project / "out" / "algo_top.v")

    forge_yml = project / "forge.yml"
    text = forge_yml.read_text()
    assert "active: low" in text
    forge_yml.write_text(text.replace("active: low", "active: high"))
    plan_adoption(project).write()
    high = _build(capsys, project, "verilog", project / "out" / "algo_top.v")

    assert f".{port}(~ap_rst)" in low
    assert f".{port}(ap_rst)" in high
    assert "~ap_rst" not in high


@pytest.mark.parametrize("name, low", [
    ("rst", False), ("ap_rst", False), ("reset", False),
    ("rst_n", True), ("ap_rst_n", True), ("aresetn", True), ("presetn", True),
    ("rst_b", True), ("n_rst", True),
])
def test_the_name_decides_without_a_contract(name: str, low: bool) -> None:
    assert reset_is_active_low(name) is low
