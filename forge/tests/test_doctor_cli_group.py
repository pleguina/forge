"""Coverage for `forge doctor` (core/cli/groups/doctor.py), the top-level
environment/toolchain health check.

Check results depend on the sandbox's actual state, so assertions target
structure (keys/status values present) rather than pinning specific
pass/fail outcomes for every optional tool — except where this sandbox's
state is known and stable for the duration of a test run (Vivado present,
matplotlib/numpy/networkx absent — confirmed via `python -m forge.core.cli.main
doctor` during development).
"""

from __future__ import annotations

import json

import pytest

from forge.core.cli.main import build_parser


def _run_doctor(capsys: pytest.CaptureFixture[str], *args: str):
    parser = build_parser()
    code = 0
    try:
        parsed = parser.parse_args(["doctor", *args])
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_doctor_text_mode_runs_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _err = _run_doctor(capsys)

    assert code == 0
    assert "forge doctor" in out
    assert "forge_version" in out
    assert "tool:xvlog" in out


def test_doctor_json_mode_has_expected_structure(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _err = _run_doctor(capsys, "--json")

    assert code == 0
    payload = json.loads(out)
    assert "checks" in payload and "ok" in payload
    for key in (
        "forge_version", "python_version",
        "tool:xvlog", "tool:xelab", "tool:xsim", "tool:ghdl", "tool:verilator",
        "python:pyverilog", "python:matplotlib", "python:numpy", "python:networkx",
        "resource:hls_templates", "resource:canonical_roles",
        "resource:normalized_signal_families", "resource:docs_root",
    ):
        assert key in payload["checks"], f"missing check {key}"
        assert payload["checks"][key]["status"] in (
            "ok", "missing (optional)", "missing (required)",
        )


def test_doctor_reports_real_resource_paths(capsys: pytest.CaptureFixture[str]) -> None:
    _code, out, _err = _run_doctor(capsys, "--json")
    payload = json.loads(out)

    # These are real, installed package resources — always present
    # regardless of cwd or optional-dependency state.
    assert payload["checks"]["resource:hls_templates"]["status"] == "ok"
    assert payload["checks"]["resource:canonical_roles"]["status"] == "ok"


def test_doctor_never_fails_without_strict(capsys: pytest.CaptureFixture[str]) -> None:
    # Every check today is an optional extra, so even with several missing
    # (matplotlib/numpy/networkx/ghdl in this sandbox), plain `forge doctor`
    # must still exit 0.
    code, _out, _err = _run_doctor(capsys)

    assert code == 0
