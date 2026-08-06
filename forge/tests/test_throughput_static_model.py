"""Tests for forge.analyze.throughput_static.model.
"""
from __future__ import annotations

from pathlib import Path

from forge.analyze.hls_reports.extractor import HLSModuleReport, collect_reports
from forge.analyze.throughput_static.model import (
    build_design_throughput_analysis,
    build_static_throughput_analysis,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
VPD_HLS_BUILD_ROOT = REPO_ROOT / "build_hls_vision_pipeline_demo"


def _report(**overrides) -> HLSModuleReport:
    defaults = dict(
        module_name="pixel_normalizer", solution="solution1", status="ok", error=None,
        estimated_fmax_mhz=250.0, pipeline_ii=1,
    )
    defaults.update(overrides)
    return HLSModuleReport(**defaults)


def test_build_static_throughput_analysis_from_report():
    analysis = build_static_throughput_analysis(_report(), data_width_bits=8)
    assert analysis.module_name == "pixel_normalizer"
    assert analysis.clock_frequency_mhz == 250.0
    assert analysis.pipeline_ii == 1
    assert analysis.records_per_cycle == 1.0
    assert analysis.data_width_bits == 8
    assert analysis.nominal_capacity_records_per_sec == 250_000_000.0


def test_build_static_throughput_analysis_folds_in_ii_greater_than_one():
    analysis = build_static_throughput_analysis(_report(pipeline_ii=4), data_width_bits=8)
    assert analysis.records_per_cycle == 0.25
    assert analysis.nominal_capacity_records_per_sec == 62_500_000.0


def test_build_static_throughput_analysis_zero_ii_treated_as_one():
    analysis = build_static_throughput_analysis(_report(pipeline_ii=0), data_width_bits=8)
    assert analysis.pipeline_ii == 1
    assert analysis.records_per_cycle == 1.0


def test_build_static_throughput_analysis_explicit_clock_overrides_report():
    analysis = build_static_throughput_analysis(
        _report(estimated_fmax_mhz=250.0), data_width_bits=8, clock_frequency_mhz=125.0,
    )
    assert analysis.clock_frequency_mhz == 125.0
    assert analysis.nominal_capacity_records_per_sec == 125_000_000.0


def test_build_design_throughput_analysis_picks_lowest_capacity_as_bottleneck():
    reports = [
        _report(module_name="fast", estimated_fmax_mhz=500.0, pipeline_ii=1),
        _report(module_name="slow", estimated_fmax_mhz=100.0, pipeline_ii=2),
    ]
    widths = {"fast": 8, "slow": 8}
    analyses, bottleneck, predicted_rate = build_design_throughput_analysis(reports, widths)
    assert len(analyses) == 2
    assert bottleneck == "slow"
    assert predicted_rate == 50_000_000.0  # 100MHz / 2 (II)


def test_build_design_throughput_analysis_skips_non_ok_reports():
    reports = [
        _report(module_name="ok_mod"),
        _report(module_name="missing_mod", status="missing", error="csynth.xml not found"),
    ]
    analyses, bottleneck, _ = build_design_throughput_analysis(
        reports, {"ok_mod": 8, "missing_mod": 8},
    )
    assert [a.module_name for a in analyses] == ["ok_mod"]
    assert bottleneck == "ok_mod"


def test_build_design_throughput_analysis_skips_modules_without_declared_width():
    reports = [_report(module_name="ok_mod")]
    analyses, bottleneck, predicted_rate = build_design_throughput_analysis(reports, {})
    assert analyses == []
    assert bottleneck is None
    assert predicted_rate is None


def test_build_design_throughput_analysis_real_vision_pipeline_demo_hls_build():
    """Integration check against the real, already-synthesized
    pixel_normalizer HLS build cached on disk — not a fresh HLS run."""
    if not VPD_HLS_BUILD_ROOT.exists():
        import pytest
        pytest.skip(f"no cached HLS build at {VPD_HLS_BUILD_ROOT} — run "
                    "./run_vision_pipeline_demo.sh at least once to populate it")

    reports = collect_reports(VPD_HLS_BUILD_ROOT)
    ok_reports = [r for r in reports if r.status == "ok"]
    assert ok_reports, "expected at least one real, synthesized HLS module report"

    widths = {r.module_name: 8 for r in ok_reports}
    analyses, bottleneck, predicted_rate = build_design_throughput_analysis(ok_reports, widths)

    assert len(analyses) == len(ok_reports)
    assert bottleneck is not None
    assert predicted_rate is not None
    assert predicted_rate > 0
    for a in analyses:
        assert a.clock_frequency_mhz > 0
        assert a.pipeline_ii >= 1
