"""`forge contract infer` — the interface-contract authoring command.

Previously reachable only as `forge topgen migrate --kind infer-contract`:
a verb meaning "convert an old project", for what is actually the most
common authoring task in a FORGE project. These are the same invariants
that command was held to, plus the registry-scanning route that removes
its ip_info.yaml prerequisite.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import pytest
import yaml

from forge.core.cli.main import build_parser


class Result(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> Result:
    parser = build_parser()
    code = 0
    try:
        parsed = parser.parse_args(["contract", *args])
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return Result(code, captured.out, captured.err)


def _ip_info(tmp_path: Path, ports: list) -> Path:
    p = tmp_path / "ip_info.yaml"
    p.write_text(yaml.dump({"m1": {"ports": ports}}))
    return p


CLK_RST = [
    {"name": "ap_clk", "direction": "IN", "width": 1},
    {"name": "ap_rst", "direction": "IN", "width": 1},
]


def test_dry_run_never_writes(capsys, tmp_path):
    out = tmp_path / "m1.interface.yaml"

    result = _run(capsys, "infer", "m1", "--ip-info", str(_ip_info(tmp_path, CLK_RST)),
                  "--output", str(out), "--dry-run")

    assert result.returncode == 0
    assert "clock_primary" in result.stdout
    assert not out.exists()


def test_real_run_writes_valid_yaml(capsys, tmp_path):
    out = tmp_path / "m1.interface.yaml"

    result = _run(capsys, "infer", "m1", "--ip-info", str(_ip_info(tmp_path, CLK_RST)),
                  "--output", str(out))

    assert result.returncode == 0
    assert yaml.safe_load(out.read_text())["ip_interface"]["module_name"] == "m1"


def test_refuses_to_overwrite_existing_output(capsys, tmp_path):
    out = tmp_path / "m1.interface.yaml"
    out.write_text("already here\n")

    result = _run(capsys, "infer", "m1", "--ip-info", str(_ip_info(tmp_path, [])),
                  "--output", str(out))

    assert result.returncode == 1
    assert "already exists" in result.stderr
    assert out.read_text() == "already here\n"


def test_unknown_module_names_the_available_ones(capsys, tmp_path):
    result = _run(capsys, "infer", "nope", "--ip-info", str(_ip_info(tmp_path, [])),
                  "--output", str(tmp_path / "o.yaml"))

    # A bad argument value is a finding about the user's input (exit 1),
    # not a FORGE crash — see docs/development/cli_exit_codes.md.
    assert result.returncode == 1
    assert "not found" in result.stderr
    assert "Available: m1" in result.stderr


def _rtl_project(tmp_path: Path) -> Path:
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "m.v").write_text(
        "module m #(parameter W = 8) (\n"
        "    input  wire         ap_clk,\n"
        "    input  wire         ap_rst,\n"
        "    input  wire [W-1:0] data_in,\n"
        "    output wire [W-1:0] a, b\n"
        ");\nendmodule\n"
    )
    reg = tmp_path / "modules.yml"
    reg.write_text(yaml.safe_dump({
        "modules": [{"name": "m", "kind": "rtl", "top": "m", "src": ["rtl/m.v"]}]
    }))
    return reg


def test_infers_from_the_registry_with_no_ip_info_prerequisite(capsys, tmp_path):
    """The old command required the caller to produce an ip_info.yaml first.
    For RTL the source is right there, so scan it."""
    out = tmp_path / "m.interface.yaml"

    result = _run(capsys, "infer", "m", "--contracts-from", str(_rtl_project(tmp_path)),
                  "--output", str(out))

    assert result.returncode == 0, result.stderr
    roles = yaml.safe_load(out.read_text())["ip_interface"]["roles"]
    assert set(roles) == {"clock_primary", "reset_primary", "data_in", "a", "b"}
    # RTL roles carry no transcribed facts — contract_loader derives them.
    assert roles["data_in"] is None


def test_unscannable_module_says_why(capsys, tmp_path):
    reg = tmp_path / "modules.yml"
    reg.write_text(yaml.safe_dump({
        "modules": [{"name": "h", "kind": "hls", "top": "h", "src": ["algo/h.cpp"]}]
    }))

    result = _run(capsys, "infer", "h", "--contracts-from", str(reg),
                  "--output", str(tmp_path / "h.yaml"))

    assert result.returncode == 1
    assert "no HDL until its IP is built" in result.stderr


def test_unknown_module_in_registry_names_the_available_ones(capsys, tmp_path):
    result = _run(capsys, "infer", "nope", "--contracts-from", str(_rtl_project(tmp_path)),
                  "--output", str(tmp_path / "o.yaml"))

    assert result.returncode == 1
    assert "Available: m" in result.stderr


def test_predict_drafts_a_contract_for_an_unbuilt_hls_module(capsys, tmp_path):
    """`--predict` reads the C++ instead of scanning HDL, so an HLS module's
    contract can be drafted before its IP exists."""
    repo = Path(__file__).resolve().parents[2]
    out = tmp_path / "hd.interface.yaml"

    result = _run(capsys, "infer", "hit_decoder",
                  "--contracts-from", str(repo / "plugins/trigger_demo/forge/modules.yml"),
                  "--predict", "--output", str(out))

    assert result.returncode == 0, result.stderr
    spec = yaml.safe_load(out.read_text())["ip_interface"]
    assert spec["source_type"] == "hls"
    assert spec["normalization_status"] == "draft"
    roles = spec["roles"]
    assert {"clock_primary", "reset_primary", "raw_hit", "decoded_hit"} <= set(roles)
    # HLS roles carry the port facts, since there is no HDL to derive from.
    assert roles["raw_hit"]["width"] == 32
    assert roles["decoded_hit"]["direction"] == "output"


def test_predict_reports_what_it_could_not_resolve(capsys, tmp_path):
    """A prediction is a draft to confirm against the built IP, so anything
    unresolved is surfaced rather than silently omitted."""
    (tmp_path / "m.cpp").write_text(
        "struct big_t { int a; int b; };\n"
        "big_t m(mystery_t x) {\n"
        "#pragma HLS INTERFACE ap_ctrl_none port=return\n"
        "#pragma HLS INTERFACE ap_none port=x\n"
        "}\n")
    (tmp_path / "modules.yml").write_text(yaml.safe_dump({
        "modules": [{"name": "m", "kind": "hls", "top": "m", "src": ["m.cpp"]}]}))

    result = _run(capsys, "infer", "m", "--contracts-from", str(tmp_path / "modules.yml"),
                  "--predict")

    assert result.returncode == 0, result.stderr
    assert "confirm against the built IP" in result.stdout
    assert "could not resolve the width" in result.stdout
