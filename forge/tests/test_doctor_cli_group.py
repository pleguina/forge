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
    """The bespoke `{"checks", "ok"}` dict is
    now a `CommandEnvelope` — `metrics["checks"]` carries the same
    per-check `{status, detail}` data, reshaped via the same
    `from_diagnostic_report` bridge every other envelope-adopting command
    uses (not a third bespoke JSON shape)."""
    code, out, _err = _run_doctor(capsys, "--json")

    assert code == 0
    payload = json.loads(out)
    assert payload["status"] in ("pass", "warn", "fail")
    assert payload["schema_version"]
    checks = payload["metrics"]["checks"]
    for key in (
        "forge_version", "python_version",
        "tool:xvlog", "tool:xelab", "tool:xsim", "tool:ghdl", "tool:verilator",
        "python:pyverilog", "python:matplotlib", "python:numpy", "python:networkx",
        "resource:hls_templates", "resource:canonical_roles",
        "resource:normalized_signal_families", "resource:docs_root",
    ):
        assert key in checks, f"missing check {key}"
        assert checks[key]["status"] in (
            "ok", "missing (optional)", "missing (required)",
        )


def test_doctor_reports_real_resource_paths(capsys: pytest.CaptureFixture[str]) -> None:
    _code, out, _err = _run_doctor(capsys, "--json")
    checks = json.loads(out)["metrics"]["checks"]

    # These are real, installed package resources — always present
    # regardless of cwd or optional-dependency state.
    assert checks["resource:hls_templates"]["status"] == "ok"
    assert checks["resource:canonical_roles"]["status"] == "ok"


def test_doctor_strict_fails_on_missing_optional_extra(capsys: pytest.CaptureFixture[str]) -> None:
    """Unlike the pre-envelope `--strict`
    (dead code, since no check was ever `required=True`), `--strict` now
    promotes any missing check — including optional extras — to a
    failure, via the same envelope `--strict` semantics every other
    command uses. This sandbox's matplotlib/numpy/networkx are confirmed
    absent (see this module's docstring), so `--strict` must fail here."""
    code, _out, _err = _run_doctor(capsys, "--json", "--strict")

    assert code == 1


def test_doctor_never_fails_without_strict(capsys: pytest.CaptureFixture[str]) -> None:
    # Every check today is an optional extra, so even with several missing
    # (matplotlib/numpy/networkx/ghdl in this sandbox), plain `forge doctor`
    # must still exit 0.
    code, _out, _err = _run_doctor(capsys)

    assert code == 0


def test_doctor_and_verify_doctor_agree_on_tool_availability(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The de-duplication proof: `forge
    doctor` and `forge verify doctor` both check xvlog/xelab/xsim
    presence via the same shared `forge.core.toolchain_versions.tool_present`
    helper now — they must report identically for the same environment,
    where before each independently called `shutil.which` (a duplication
    that could silently drift if one's check ever diverged from the
    other's)."""
    import json as _json
    import sys as _sys

    import forge.verify.__main__ as verify_cli

    _code, doctor_out, _err = _run_doctor(capsys, "--json")
    doctor_checks = _json.loads(doctor_out)["metrics"]["checks"]

    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    design_verification_yml = (
        repo_root / "plugins/passthrough_demo/forge/verify/design.verification.yml"
    )
    old_argv = _sys.argv
    _sys.argv = ["forge-verify", "doctor", str(design_verification_yml), "--json"]
    try:
        verify_code = 0
        try:
            verify_cli.main()
        except SystemExit as exc:
            verify_code = 0 if exc.code is None else int(exc.code)
        verify_out = capsys.readouterr().out
    finally:
        _sys.argv = old_argv
    assert verify_code in (0, 1)
    verify_diagnostics = _json.loads(verify_out)["diagnostics"]

    for tool in ("xvlog", "xelab", "xsim"):
        doctor_present = doctor_checks[f"tool:{tool}"]["status"] == "ok"
        verify_present = any(
            f"Tool on PATH: {tool}" in d["message"] for d in verify_diagnostics
        )
        assert doctor_present == verify_present, (
            f"{tool}: forge doctor says present={doctor_present}, "
            f"forge verify doctor says present={verify_present}"
        )
