"""Real end-to-end coverage for `forge report` (release-plan Phase 6,
§6.5) — the orchestrator that bundles maturity/latency/verification/
provenance sections plus a dashboard into one output directory.

Runs against both real reference plugins directly (no gen-top needed for
the base report — maturity/latency-check only need design.yml). A
separate xsim-gated test proves the `--provenance`/`--junit-xml` reuse
paths against real artifacts from `forge inspect --provenance` and
`forge test run --junit-xml`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import NamedTuple

import pytest

from forge.core.cli.main import build_parser

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSTHROUGH_DESIGN = REPO_ROOT / "plugins/passthrough_demo/forge/designs/design.yml"
PASSTHROUGH_MODULES = REPO_ROOT / "plugins/passthrough_demo/forge/modules.yml"
TRIGGER_DESIGN = REPO_ROOT / "plugins/trigger_demo/forge/designs/design.yml"
TRIGGER_MODULES = REPO_ROOT / "plugins/trigger_demo/forge/modules.yml"

XSIM_AVAILABLE = all(shutil.which(tool) for tool in ("xvlog", "xelab", "xsim"))
skip_without_xsim = pytest.mark.skipif(
    not XSIM_AVAILABLE, reason="Vivado xsim toolchain (xvlog/xelab/xsim) not on PATH"
)


class Result(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def _run(capsys: pytest.CaptureFixture[str], group: str, *args: str) -> Result:
    parser = build_parser()
    code = 0
    try:
        parsed = parser.parse_args([group, *args])
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return Result(code, captured.out, captured.err)


_CLAIMED_ARTIFACTS = (
    "maturity.md", "latency_check.md", "verification_results.md",
    "dashboard.html", "summary.md",
)


def test_report_bundle_on_passthrough_demo(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """passthrough_demo has no HLS build artifacts — the honest-absence
    path for the HLS summary section exercises here."""
    output_dir = tmp_path / "report"

    result = _run(
        capsys, "report", str(PASSTHROUGH_DESIGN),
        "--contracts-from", str(PASSTHROUGH_MODULES),
        "--output", str(output_dir), "--json",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] in ("pass", "warn")
    for name in _CLAIMED_ARTIFACTS:
        path = output_dir / name
        assert str(path) in payload["artifacts"]
        assert path.exists() and path.stat().st_size > 0, f"{name} missing or empty"

    assert any("hls" in d["message"].lower() for d in payload["diagnostics"])
    assert any("Topology SVG" in a for a in payload["next_actions"]), (
        "the topology-SVG/explorer deferral must be present, not silently missing"
    )

    maturity_md = (output_dir / "maturity.md").read_text()
    assert "Contract maturity" in maturity_md
    assert "contract-driven" in maturity_md


def test_report_bundle_on_trigger_demo(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    output_dir = tmp_path / "report"

    result = _run(
        capsys, "report", str(TRIGGER_DESIGN),
        "--contracts-from", str(TRIGGER_MODULES),
        "--output", str(output_dir), "--json",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    for name in _CLAIMED_ARTIFACTS:
        assert (output_dir / name).exists()
    assert payload["metrics"]["maturity"]["modules"]["total"] == 7


def test_report_missing_design_is_guided(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    result = _run(
        capsys, "report", str(tmp_path / "nope.yml"), "--output", str(tmp_path / "report"),
    )
    assert result.returncode == 1
    assert not (tmp_path / "report" / "maturity.md").exists()


def test_report_registered_as_top_level_command() -> None:
    parser = build_parser()
    parsed = parser.parse_args(["report", str(PASSTHROUGH_DESIGN)])
    assert parsed.func.__name__ == "cmd_report"


@skip_without_xsim
def test_report_reuses_real_provenance_and_junit_artifacts(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """End-to-end: gen-top a real scratch copy, run `forge test run
    --junit-xml`, write a provenance manifest via `forge inspect
    --provenance`, then confirm `forge report` reuses both (not
    recomputing verification results, purely presenting the provenance
    manifest's already-computed data)."""
    dest_plugin = tmp_path / "plugins" / "passthrough_demo"
    shutil.copytree(
        REPO_ROOT / "plugins/passthrough_demo", dest_plugin,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    design_yml = dest_plugin / "forge/designs/design.yml"
    modules_yml = dest_plugin / "forge/modules.yml"
    design_verification_yml = dest_plugin / "forge/verify/design.verification.yml"

    gen_top_result = _run(
        capsys, "topgen", "gen-top", str(design_yml),
        "--mode", "verilog", "--consumer-root", str(tmp_path),
        "--contracts-from", str(modules_yml),
        "--output", str(tmp_path / "gen-top/design_passthrough_demo/algo_top.v"),
        "--build-dir", str(tmp_path / "build/passthrough_demo"),
    )
    assert gen_top_result.returncode == 0, gen_top_result.stdout + gen_top_result.stderr

    junit_path = tmp_path / "junit.xml"
    test_run_result = _run(
        capsys, "test", "run", str(design_verification_yml),
        "--flow", "passthrough_xsim", "--plugin", "passthrough_demo",
        "--consumer-root", str(tmp_path), "--event-id", "0",
        "--junit-xml", str(junit_path),
    )
    assert test_run_result.returncode == 0, test_run_result.stdout + test_run_result.stderr
    assert junit_path.exists()

    provenance_path = tmp_path / "provenance.json"
    inspect_result = _run(
        capsys, "inspect", str(design_yml), "--contracts-from", str(modules_yml),
        "--provenance", str(provenance_path),
    )
    assert inspect_result.returncode == 0, inspect_result.stdout + inspect_result.stderr

    output_dir = tmp_path / "report"
    report_result = _run(
        capsys, "report", str(design_yml), "--contracts-from", str(modules_yml),
        "--output", str(output_dir),
        "--provenance", str(provenance_path), "--junit-xml", str(junit_path),
        "--json",
    )

    assert report_result.returncode == 0, report_result.stdout + report_result.stderr
    payload = json.loads(report_result.stdout)
    assert str(output_dir / "provenance.md") in payload["artifacts"]
    assert not any("no --provenance" in d["message"] for d in payload["diagnostics"])
    assert not any("No verification results yet" in a for a in payload["next_actions"])

    verification_md = (output_dir / "verification_results.md").read_text()
    assert "event_0" in verification_md
    assert "PASS" in verification_md

    provenance_md = (output_dir / "provenance.md").read_text()
    assert "IR content hash" in provenance_md
