"""
Integration test for `forge topgen validate`'s CDC
wiring: `verify_cdc` used to be reachable only from `gen-top --strict`;
this test drives the real `forge topgen validate` CLI entry point
against a small two-module, two-clock-domain synthetic design (built
from scratch in tmp_path, mirroring test_cdc_generation.py's real-file
manifest-test convention) and confirms an undeclared clock-domain
crossing now surfaces as a warning there too, not just at generation
time.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge.core.cli.main import build_parser


def _run_topgen(capsys: pytest.CaptureFixture[str], *args: str):
    parser = build_parser()
    code = 0
    try:
        parsed = parser.parse_args(["topgen", *args])
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _write_two_clock_design(tmp_path: Path) -> Path:
    (tmp_path / "src.v").write_text(
        "module src_top(input clk_a, input rst, output dout); endmodule\n"
    )
    (tmp_path / "dst.v").write_text(
        "module dst_top(input clk_b, input rst, input din); endmodule\n"
    )
    (tmp_path / "src.interface.yaml").write_text(
        "ip_interface:\n"
        "  module_name: src_top\n"
        "  ip_info_key: src\n"
        "  source_type: rtl\n"
        "  roles:\n"
        "    clock_primary: {raw_port: clk_a, direction: input, width: 1}\n"
        "    reset_primary: {raw_port: rst, direction: input, width: 1}\n"
        "    dout: {raw_port: dout, direction: output, width: 1}\n"
    )
    (tmp_path / "dst.interface.yaml").write_text(
        "ip_interface:\n"
        "  module_name: dst_top\n"
        "  ip_info_key: dst\n"
        "  source_type: rtl\n"
        "  roles:\n"
        "    clock_primary: {raw_port: clk_b, direction: input, width: 1}\n"
        "    reset_primary: {raw_port: rst, direction: input, width: 1}\n"
        "    din: {raw_port: din, direction: input, width: 1}\n"
    )
    (tmp_path / "modules.yml").write_text(
        "modules:\n"
        "  - name: src\n"
        "    kind: rtl\n"
        "    top: src_top\n"
        "    src: [src.v]\n"
        "    interface_contract: src.interface.yaml\n"
        "  - name: dst\n"
        "    kind: rtl\n"
        "    top: dst_top\n"
        "    src: [dst.v]\n"
        "    interface_contract: dst.interface.yaml\n"
    )
    design_path = tmp_path / "design.yml"
    design_path.write_text(
        "part: xcvu13p\n"
        "clock_period: 4.0\n"
        "registry: modules.yml\n"
        "modules:\n"
        "  - name: src\n"
        "    ref: src\n"
        "    instances: 1\n"
        "  - name: dst\n"
        "    ref: dst\n"
        "    instances: 1\n"
        "connections:\n"
        "  - from: src\n"
        "    to: dst\n"
        "    port_map: [[dout, din]]\n"
    )
    return design_path


def test_validate_warns_on_undeclared_cdc_crossing(tmp_path, capsys):
    design_path = _write_two_clock_design(tmp_path)
    code, out, _err = _run_topgen(capsys, "validate", str(design_path), "--json")

    assert code == 0  # non-strict: a warning, not a failure
    payload = json.loads(out)
    assert payload["status"] == "warn"
    assert any(d.get("code") == "ATG023" for d in payload["diagnostics"])


def test_validate_strict_fails_on_undeclared_cdc_crossing(tmp_path, capsys):
    design_path = _write_two_clock_design(tmp_path)
    code, _out, _err = _run_topgen(capsys, "validate", str(design_path), "--strict")

    assert code == 1


def test_validate_clean_on_declared_cdc_crossing(tmp_path, capsys):
    design_path = _write_two_clock_design(tmp_path)
    text = design_path.read_text().replace(
        "    port_map: [[dout, din]]\n",
        "    port_map: [[dout, din]]\n"
        "    cdc: {kind: level_sync}\n",
    )
    design_path.write_text(text)

    code, out, _err = _run_topgen(capsys, "validate", str(design_path), "--json")

    assert code == 0
    payload = json.loads(out)
    assert not any(d.get("code") == "ATG023" for d in payload["diagnostics"])
