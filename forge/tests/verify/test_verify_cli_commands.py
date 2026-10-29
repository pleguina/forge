"""End-to-end coverage for `forge dev verify` read-only CLI commands.

verify/__main__.py is a ~720-line, ~15%-covered CLI surface, only
exercised indirectly through integration scripts. These tests call
forge.verification.__main__.main() in-process (subprocess-based CLI tests, like
tests/verify/test_cli_error_handling.py, run in a child interpreter that
--cov=forge in the parent pytest process can't see, so they never counted
towards coverage) against a prepared copy of plugins/passthrough_demo, a
tiny FORGE consumer.

Scoped to commands that are read-only or write only to a scratch
--plugins-root (doctor, release-check, init-plugin --dry-run, and
argument-validation error paths); `run` is covered by test_verify_run_xsim.py.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
from pathlib import Path

import pytest

import forge.verification.__main__ as cli

REPO_ROOT = Path(__file__).resolve().parents[3]


def _run_verify(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *args: str):
    monkeypatch.setattr(sys, "argv", ["forge-verify", *args])
    code = 0
    try:
        cli.main()
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def prepared_plugin(tmp_path: Path, prepare_plugin_flows) -> Path:
    """design.verification.yml of a gen-top'd copy of passthrough_demo with
    its flow files and stimulus written."""
    from forge.core.cli.main import build_parser

    plugin = tmp_path / "plugins/passthrough_demo"
    shutil.copytree(
        REPO_ROOT / "plugins/passthrough_demo", plugin, ignore=shutil.ignore_patterns("__pycache__"),
    )
    parsed = build_parser().parse_args([
        "topgen", "gen-top", str(plugin / "forge/designs/design.yml"),
        "--mode", "verilog", "--consumer-root", str(tmp_path),
        "--output", str(tmp_path / "gen-top/design_passthrough_demo/algo_top.v"),
        "--build-dir", str(tmp_path / "build/passthrough_demo"),
    ])
    with contextlib.redirect_stdout(io.StringIO()):
        parsed.func(parsed)
    design = plugin / "forge/verify/design.verification.yml"
    prepare_plugin_flows(design, tmp_path)
    return design


@pytest.fixture
def stale_design_verification(prepared_plugin: Path) -> Path:
    """A prepared copy whose design.verification.yml is newer than the flow
    files generated from it."""
    plugin = prepared_plugin.parents[2]
    newest = max(p.stat().st_mtime for p in plugin.rglob("*") if p.is_file())
    os.utime(prepared_plugin, (newest + 60, newest + 60))
    return prepared_plugin


def _root(design: Path) -> str:
    return str(design.parents[4])


def test_doctor_passes_on_real_plugin(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], prepared_plugin: Path) -> None:
    code, out, _err = _run_verify(
        monkeypatch, capsys, "doctor", str(prepared_plugin), "--consumer-root", _root(prepared_plugin),
    )

    assert code == 0
    assert "PASS" in out


def test_doctor_json_emits_diagnostic_report(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stale_design_verification: Path) -> None:
    import json

    code, out, _err = _run_verify(monkeypatch, capsys, "doctor", str(stale_design_verification), "--json")

    assert code == 0
    report = json.loads(out)
    # The stale fixture has warnings (stale port_map.yaml) and 0 errors — this now actually exercises the "warn" branch, closing a
    # latent bug where DiagnosticReport.to_dict() only ever emitted
    # "pass"/"fail", never "warn", even with real warnings present.
    assert report["status"] == "warn"
    assert report["schema_version"]
    assert report["metrics"]["counts"]["errors"] == 0
    assert report["metrics"]["counts"]["warnings"] > 0
    assert report["next_actions"], "warnings should surface at least one next_action"


def test_doctor_json_strict_promotes_warn_to_fail_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stale_design_verification: Path
) -> None:
    import json

    code, out, _err = _run_verify(
        monkeypatch, capsys, "doctor", str(stale_design_verification), "--json", "--strict",
    )

    # --strict promotes a warning-only report's exit code to 1, without
    # changing the reported status string.
    assert code == 1
    report = json.loads(out)
    assert report["status"] == "warn"


def test_doctor_missing_design_is_guided(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    missing = tmp_path / "missing.design.verification.yml"

    code, _out, err = _run_verify(monkeypatch, capsys, "doctor", str(missing))

    assert code == 1
    assert "design.verification.yml not found" in err
    assert "Traceback" not in err


def test_release_check_passes_on_real_plugin(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], prepared_plugin: Path) -> None:
    code, out, _err = _run_verify(
        monkeypatch, capsys, "release-check", str(prepared_plugin), "--consumer-root", _root(prepared_plugin),
    )

    assert code == 0
    assert "PASS" in out


def test_release_check_strict_fails_on_stale_warning(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stale_design_verification: Path) -> None:
    code, _out, err = _run_verify(
        monkeypatch, capsys, "release-check", str(stale_design_verification), "--strict",
    )

    # RC-10 reports the stale artifacts as warnings; --strict promotes any
    # warning to a failure.
    assert code == 1
    assert "[RC-10]" in err and "is stale" in err


def test_init_plugin_dry_run_lists_without_writing(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    plugins_root = tmp_path / "plugins"

    code, out, _err = _run_verify(
        monkeypatch, capsys, "init-plugin", "my_test_plugin",
        "--plugins-root", str(plugins_root), "--dry-run",
    )

    assert code == 0
    assert "Would create plugin skeleton" in out
    assert not plugins_root.exists()


def test_init_plugin_writes_expected_skeleton(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    plugins_root = tmp_path / "plugins"

    code, _out, _err = _run_verify(
        monkeypatch, capsys, "init-plugin", "my_test_plugin",
        "--plugins-root", str(plugins_root),
    )

    assert code == 0
    created = plugins_root / "my_test_plugin"
    assert (created / "forge/verify/design.verification.yml").exists()
    assert (created / "forge/verify/tools/bootstrap.py").exists()


def test_preflight_missing_consumer_root_is_guided(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], prepared_plugin: Path) -> None:
    flow = prepared_plugin.parent / "passthrough_xsim/verify.flow.yml"

    code, _out, err = _run_verify(
        monkeypatch, capsys, "preflight", str(flow), "--plugin", "passthrough_demo",
    )

    assert code == 1
    assert "consumer_root is required" in err
    assert "Traceback" not in err


def test_generate_missing_design_is_guided(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    missing = tmp_path / "missing.design.verification.yml"

    code, _out, err = _run_verify(monkeypatch, capsys, "generate", str(missing))

    assert code == 1
    assert "design file not found" in err.lower()


@pytest.mark.parametrize(
    "subcmd", ["doctor", "release-check", "preflight", "run", "generate", "prepare"]
)
def test_missing_positional_arg_is_argparse_error(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], subcmd: str) -> None:
    monkeypatch.setattr(sys, "argv", ["forge-verify", subcmd])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2
    assert "usage:" in capsys.readouterr().err.lower()
