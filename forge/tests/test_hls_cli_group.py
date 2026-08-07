"""End-to-end coverage for `forge hls` (core/cli/groups/hls.py, ~12%).

`gen-tcl` is pure Jinja templating (no Vitis HLS invocation), so it's
exercised for real against plugins/trigger_demo's HLS module registry.
`run` actually shells out to `vitis_hls`, which is a much heavier/slower
tier (real synthesis) — out of scope here; only its argument-validation
and error paths (which fail before any subprocess is spawned) are covered.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import pytest

from forge.core.cli.main import build_parser

REPO_ROOT = Path(__file__).resolve().parents[2]
TRIGGER_DEMO_MODULES_YML = REPO_ROOT / "plugins/trigger_demo/forge/modules.yml"


class Result(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def _run_hls(capsys: pytest.CaptureFixture[str], *args: str) -> Result:
    parser = build_parser()
    code = 0
    try:
        parsed = parser.parse_args(["hls", *args])
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return Result(code, captured.out, captured.err)


def test_gen_tcl_writes_all_stage_scripts_for_one_module(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    result = _run_hls(
        capsys, "gen-tcl", "hit_decoder",
        "--hls-config", str(TRIGGER_DEMO_MODULES_YML),
        "--output-dir", str(tmp_path / "build_hls"),
    )

    assert result.returncode == 0
    assert "Generating TCL for hit_decoder" in result.stdout
    module_dir = tmp_path / "build_hls/hit_decoder"
    for stage in ("project.tcl", "csim.tcl", "synth.tcl", "cosim.tcl", "ip_export.tcl", "clean.tcl"):
        assert (module_dir / stage).exists(), f"missing {stage}"


def test_gen_tcl_all_modules(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    result = _run_hls(
        capsys, "gen-tcl",
        "--hls-config", str(TRIGGER_DEMO_MODULES_YML),
        "--output-dir", str(tmp_path / "build_hls"),
    )

    assert result.returncode == 0
    # trigger_demo's registry declares 4 HLS modules
    generated = {p.name for p in (tmp_path / "build_hls").iterdir()}
    assert generated == {"hit_decoder", "hit_collector", "trigger_logic", "trigger_output"}


def test_gen_tcl_missing_config_is_guided(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    result = _run_hls(
        capsys, "gen-tcl",
        "--hls-config", str(tmp_path / "missing.yml"),
        "--output-dir", str(tmp_path / "build_hls"),
    )

    assert result.returncode == 1
    assert "HLS config file not found" in result.stderr


def test_gen_tcl_unknown_module_fails(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    result = _run_hls(
        capsys, "gen-tcl", "does_not_exist",
        "--hls-config", str(TRIGGER_DEMO_MODULES_YML),
        "--output-dir", str(tmp_path / "build_hls"),
    )

    assert result.returncode == 1


def test_run_missing_registry_file_is_guided(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    result = _run_hls(capsys, "run", "--registry", str(tmp_path / "missing.yml"))

    assert result.returncode == 1
    assert "registry file not found" in result.stderr


def test_run_all_modules_requires_registry(capsys: pytest.CaptureFixture[str]) -> None:
    result = _run_hls(capsys, "run")

    assert result.returncode == 1
    assert "--registry is required" in result.stderr


def test_run_invalid_stage_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    result = _run_hls(
        capsys, "run",
        "--registry", str(TRIGGER_DEMO_MODULES_YML),
        "--modules", "hit_decoder",
        "--stages", "bogus",
    )

    assert result.returncode == 1
    assert "invalid stage(s): bogus" in result.stderr
