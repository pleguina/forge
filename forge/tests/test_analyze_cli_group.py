"""End-to-end coverage for `forge analyze` (core/cli/groups/analyze.py, ~22%).

`plot-results` is skipped: it imports forge.analyze.result_plots.engine
at call time, which requires matplotlib — an optional dependency not
installed in this environment. Everything else here has no such
dependency and is driven for real.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import NamedTuple

import pytest

from forge.core.cli.main import build_parser

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSTHROUGH_DESIGN_YML = REPO_ROOT / "plugins/passthrough_demo/forge/designs/design.yml"

skip_without_matplotlib = pytest.mark.skipif(
    importlib.util.find_spec("matplotlib") is None,
    reason="matplotlib not installed (optional dependency for forge.analyze.result_plots)",
)


class Result(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def _run_analyze(capsys: pytest.CaptureFixture[str], *args: str) -> Result:
    parser = build_parser()
    code = 0
    try:
        parsed = parser.parse_args(["analyze", *args])
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return Result(code, captured.out, captured.err)


class TestHlsReport:
    def test_writes_csv_md_html_for_missing_synth_report(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        # No csynth.xml under the module dir → status="missing", not an error;
        # collect_reports() is lenient by design.
        (tmp_path / "build_hls/hit_decoder").mkdir(parents=True)

        result = _run_analyze(
            capsys, "hls-report",
            "--hls-build-root", str(tmp_path / "build_hls"),
            "--output", str(tmp_path / "reports"),
        )

        assert result.returncode == 0
        assert "1 module(s): 0 ok, 1 missing/error" in result.stdout
        for name in ("hls_summary.csv", "hls_summary.md", "hls_summary.html"):
            assert (tmp_path / "reports" / name).exists()

    def test_missing_build_root_is_guided(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        result = _run_analyze(
            capsys, "hls-report",
            "--hls-build-root", str(tmp_path / "does-not-exist"),
            "--output", str(tmp_path / "reports"),
        )

        assert result.returncode == 1
        assert "HLS build root not found" in result.stderr

    def test_empty_build_root_reports_no_modules(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        (tmp_path / "build_hls").mkdir()

        result = _run_analyze(
            capsys, "hls-report",
            "--hls-build-root", str(tmp_path / "build_hls"),
            "--output", str(tmp_path / "reports"),
        )

        assert result.returncode == 1
        assert "No HLS modules found" in result.stderr


class TestLatencyCheck:
    def test_no_mismatches_on_real_design(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        result = _run_analyze(
            capsys, "latency-check", str(PASSTHROUGH_DESIGN_YML),
            "--output", str(tmp_path / "latency_check.md"),
        )

        assert result.returncode == 0
        assert "No latency mismatches detected" in result.stdout
        assert (tmp_path / "latency_check.md").exists()

    def test_missing_design_file_errors(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        result = _run_analyze(
            capsys, "latency-check", str(tmp_path / "missing.yml"),
            "--output", str(tmp_path / "latency_check.md"),
        )

        assert result.returncode == 1
        assert "ERROR building latency graph" in result.stderr


class TestRuntimeLatency:
    def test_reports_observed_latency_from_probe_csv(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        probe_csv = tmp_path / "probe.csv"
        probe_csv.write_text(
            "cycle,signal,value\n10,data_in_valid,1\n15,data_out_valid,1\n"
        )

        result = _run_analyze(
            capsys, "runtime-latency",
            "--probe-csv", str(probe_csv),
            "--probe-pairs", "pt:data_in_valid:data_out_valid",
            "--output", str(tmp_path / "runtime_latency.md"),
        )

        assert result.returncode == 0
        assert "obs=   5" in result.stdout
        assert (tmp_path / "runtime_latency.md").exists()

    def test_missing_probe_csv_is_guided(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        result = _run_analyze(
            capsys, "runtime-latency",
            "--probe-csv", str(tmp_path / "missing.csv"),
            "--output", str(tmp_path / "out.md"),
        )

        assert result.returncode == 1
        assert "Probe CSV not found" in result.stderr

    def test_no_probe_pairs_is_guided(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        probe_csv = tmp_path / "probe.csv"
        probe_csv.write_text("cycle,signal,value\n")

        result = _run_analyze(
            capsys, "runtime-latency",
            "--probe-csv", str(probe_csv),
            "--output", str(tmp_path / "out.md"),
        )

        assert result.returncode == 1
        assert "pass --probe-pairs" in result.stderr


class TestDashboard:
    def test_aggregates_reports_directory_leniently(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        # aggregator.collect() is deliberately lenient about missing files,
        # so an empty reports dir is a valid (if empty) dashboard input.
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()

        result = _run_analyze(
            capsys, "dashboard",
            "--input", str(reports_dir),
            "--output", str(tmp_path / "dashboard"),
        )

        assert result.returncode == 0
        assert (tmp_path / "dashboard/dashboard.html").exists()
        assert (tmp_path / "dashboard/summary.md").exists()


@skip_without_matplotlib
class TestPlotResults:
    def test_missing_config_is_guided(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        result = _run_analyze(
            capsys, "plot-results",
            "--config", str(tmp_path / "missing.yml"),
            "--observed", str(tmp_path / "observed.csv"),
            "--output", str(tmp_path / "plots"),
        )

        assert result.returncode == 1
        assert "ERROR loading plot config" in result.stderr
