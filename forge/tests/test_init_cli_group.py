"""Real end-to-end coverage for `forge init`
— the command that unifies the topgen-side and verify-side scaffolders
and chains validate -> build --apply -> test -> report, all with zero
manual edits.

The acceptance bar is literal: run `forge
init <plugin_id>`, touch nothing by hand, and confirm a JUnit XML with a
passing test and a report bundle both exist at the end. This file does
exactly that, against the real xsim toolchain confirmed present in this
environment (skipped automatically when it isn't).
"""

from __future__ import annotations

import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import NamedTuple

import pytest

from forge.core.cli.main import build_parser

XSIM_AVAILABLE = all(shutil.which(tool) for tool in ("xvlog", "xelab", "xsim"))
skip_without_xsim = pytest.mark.skipif(
    not XSIM_AVAILABLE, reason="Vivado xsim toolchain (xvlog/xelab/xsim) not on PATH"
)


class Result(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> Result:
    parser = build_parser()
    code = 0
    try:
        parsed = parser.parse_args(["init", *args])
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return Result(code, captured.out, captured.err)


def _tree_snapshot(root: Path) -> frozenset:
    if not root.exists():
        return frozenset()
    return frozenset(str(p.relative_to(root)) for p in root.rglob("*"))


@skip_without_xsim
def test_init_runs_full_chain_with_zero_manual_edits(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """The literal acceptance bar: run `forge init throwaway_plugin`,
    touch nothing by hand in between, and confirm a JUnit XML with a
    passing test and a report bundle both exist at the end."""
    plugins_root = tmp_path / "plugins"

    result = _run(
        capsys, "throwaway_plugin",
        "--plugins-root", str(plugins_root), "--consumer-root", str(tmp_path),
        "--json",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["metrics"]["steps_completed"] == [
        "topgen-init-plugin", "verify-init-plugin", "validate",
        "build", "test-prepare", "test-run", "report",
    ]

    # Scaffolded topgen + verify files, all real (not stubs).
    plugin_root = plugins_root / "throwaway_plugin"
    assert (plugin_root / "forge/designs/design.yml").exists()
    assert (plugin_root / "forge/modules.yml").exists()
    assert (plugin_root / "algo/rtl/throwaway_plugin.v").exists()
    assert (plugin_root / "forge/verify/design.verification.yml").exists()
    assert (plugin_root / "forge/verify/tools/gen_stimulus.py").exists()
    assert (plugin_root / "forge/verify/schemas/data/throwaway_plugin_golden.xml").exists()

    # A real, passing JUnit XML.
    junit_path = plugin_root / "forge/verify/junit.xml"
    assert junit_path.exists()
    testsuite = ET.parse(junit_path).getroot()
    assert testsuite.get("tests") == "1"
    assert testsuite.get("failures") == "0"
    assert testsuite.find("testcase").get("name") == "event_0"

    # A real report bundle.
    report_dir = plugin_root / "report"
    for name in ("maturity.md", "latency_check.md", "verification_results.md", "dashboard.html", "summary.md"):
        path = report_dir / name
        assert path.exists() and path.stat().st_size > 0
    assert "event_0" in (report_dir / "verification_results.md").read_text()
    assert "PASS" in (report_dir / "verification_results.md").read_text()

    # Real, generated build artifacts (not stubs) from the build --apply step.
    gen_top_dir = tmp_path / "gen-top" / "design_throwaway_plugin"
    assert (gen_top_dir / "algo_top.v").exists()
    assert (gen_top_dir / "build_manifest.json").exists()


def test_init_dry_run_writes_nothing(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    before = _tree_snapshot(tmp_path)

    result = _run(
        capsys, "throwaway_plugin",
        "--plugins-root", str(tmp_path / "plugins"), "--consumer-root", str(tmp_path),
        "--dry-run", "--json",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["metrics"]["dry_run"] is True
    assert payload["metrics"]["steps_completed"] == ["topgen-init-plugin", "verify-init-plugin"]
    assert payload["artifacts"] == []
    assert _tree_snapshot(tmp_path) == before


def test_init_registered_as_top_level_command() -> None:
    parser = build_parser()
    parsed = parser.parse_args(["init", "some_plugin"])
    assert parsed.func.__name__ == "cmd_init"
