"""
Tests for forge.analysis.hls_reports.extractor/formatter's recovery of
csynth.xml's Interval-min/Interval-max — data the extractor's underlying
HLSMetricsExtractor already parsed, but that was silently dropped before
reaching HLSModuleReport.

No real HLS build artifact (csynth.xml) is checked into this repo for
either reference plugin (confirmed by search) — this fixture is
necessarily synthetic, built to match the exact XPath schema
forge.hls.extract_hls_metrics.HLSMetricsExtractor already expects (not
guessed), an honest constraint documented here rather than silently
using only synthetic data without saying so.
"""

from __future__ import annotations

from pathlib import Path

from forge.analysis.hls_reports.extractor import collect_reports
from forge.analysis.hls_reports.formatter import to_csv, to_html, to_markdown

_CSYNTH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<profile>
  <UserAssignments>
    <TopModelName>my_module</TopModelName>
    <Part>xcvu13p</Part>
    <ProductFamily>virtexuplus</ProductFamily>
    <FlowTarget>vivado</FlowTarget>
    <TargetClockPeriod>4.00</TargetClockPeriod>
  </UserAssignments>
  <ReportVersion>
    <Version>2020.1</Version>
  </ReportVersion>
  <PerformanceEstimates>
    <SummaryOfTimingAnalysis>
      <EstimatedClockPeriod>3.50</EstimatedClockPeriod>
    </SummaryOfTimingAnalysis>
    <SummaryOfOverallLatency>
      <Best-caseLatency>3</Best-caseLatency>
      <Average-caseLatency>3</Average-caseLatency>
      <Worst-caseLatency>3</Worst-caseLatency>
      <PipelineInitiationInterval>1</PipelineInitiationInterval>
      <Interval-min>1</Interval-min>
      <Interval-max>2</Interval-max>
      <PipelineDepth>4</PipelineDepth>
    </SummaryOfOverallLatency>
    <PipelineType>looped</PipelineType>
  </PerformanceEstimates>
  <AreaEstimates>
    <Resources>
      <BRAM_18K>2</BRAM_18K>
      <DSP>4</DSP>
      <FF>512</FF>
      <LUT>1024</LUT>
      <URAM>0</URAM>
    </Resources>
    <AvailableResources>
      <BRAM_18K>2160</BRAM_18K>
      <DSP>3072</DSP>
      <FF>788160</FF>
      <LUT>394080</LUT>
      <URAM>360</URAM>
    </AvailableResources>
  </AreaEstimates>
</profile>
"""

# No Interval-min/Interval-max elements at all — the honest "XML never
# carried the data" case (get_int's documented 0-default behavior).
_CSYNTH_XML_NO_INTERVAL = _CSYNTH_XML.replace(
    "      <Interval-min>1</Interval-min>\n      <Interval-max>2</Interval-max>\n", "",
)


def _write_module_report(build_root: Path, module_name: str, xml_content: str) -> None:
    report_dir = build_root / module_name / "solution1" / "syn" / "report"
    report_dir.mkdir(parents=True)
    (report_dir / "csynth.xml").write_text(xml_content)


def test_collect_reports_recovers_interval_min_max(tmp_path):
    build_root = tmp_path / "build_hls"
    _write_module_report(build_root, "my_module", _CSYNTH_XML)

    reports = collect_reports(build_root)
    assert len(reports) == 1
    r = reports[0]
    assert r.status == "ok"
    assert r.interval_min == 1
    assert r.interval_max == 2
    assert r.pipeline_ii == 1


def test_collect_reports_defaults_interval_to_zero_when_absent(tmp_path):
    build_root = tmp_path / "build_hls"
    _write_module_report(build_root, "my_module", _CSYNTH_XML_NO_INTERVAL)

    reports = collect_reports(build_root)
    r = reports[0]
    assert r.status == "ok"
    assert r.interval_min == 0
    assert r.interval_max == 0


def test_reserved_throughput_fields_stay_none_not_fabricated(tmp_path):
    """buffering_capacity/occupancy/backpressure/frame_rate have no data
    source in csynth.xml — must stay None, never a guessed value."""
    build_root = tmp_path / "build_hls"
    _write_module_report(build_root, "my_module", _CSYNTH_XML)

    r = collect_reports(build_root)[0]
    assert r.buffering_capacity is None
    assert r.occupancy is None
    assert r.backpressure is None
    assert r.frame_rate is None


def test_csv_includes_interval_columns(tmp_path):
    build_root = tmp_path / "build_hls"
    _write_module_report(build_root, "my_module", _CSYNTH_XML)
    reports = collect_reports(build_root)

    out_csv = tmp_path / "reports" / "hls_summary.csv"
    to_csv(reports, out_csv)
    content = out_csv.read_text()
    assert "interval_min" in content
    assert "interval_max" in content
    lines = content.strip().splitlines()
    header = lines[0].split(",")
    row = dict(zip(header, lines[1].split(",")))
    assert row["interval_min"] == "1"
    assert row["interval_max"] == "2"


def test_markdown_shows_ii_range_for_ok_module(tmp_path):
    build_root = tmp_path / "build_hls"
    _write_module_report(build_root, "my_module", _CSYNTH_XML)
    reports = collect_reports(build_root)

    out_md = tmp_path / "reports" / "hls_summary.md"
    to_markdown(reports, out_md)
    content = out_md.read_text()
    assert "II Range" in content
    assert "1–2" in content


def test_markdown_shows_dash_when_interval_absent(tmp_path):
    """Honest rendering: no Interval-min/max in the XML -> the report
    shows an unmistakable "no data" marker, not a fabricated "0-0"."""
    build_root = tmp_path / "build_hls"
    _write_module_report(build_root, "my_module", _CSYNTH_XML_NO_INTERVAL)
    reports = collect_reports(build_root)

    out_md = tmp_path / "reports" / "hls_summary.md"
    to_markdown(reports, out_md)
    content = out_md.read_text()
    assert "0–0" not in content


def test_html_report_renders_without_error_and_includes_ii_range(tmp_path):
    build_root = tmp_path / "build_hls"
    _write_module_report(build_root, "my_module", _CSYNTH_XML)
    reports = collect_reports(build_root)

    out_html = tmp_path / "reports" / "hls_summary.html"
    to_html(reports, out_html)
    content = out_html.read_text()
    assert "II Range" in content
    assert "1–2" in content


def test_html_report_missing_module_row_has_correct_cell_count(tmp_path):
    """The 'missing csynth.xml' row must have the same number of <td>
    cells as the 'ok' row (minus name/status) — a regression guard for
    the hardcoded cell-count magic number the II Range column addition
    had to update."""
    build_root = tmp_path / "build_hls"
    (build_root / "no_report_module").mkdir(parents=True)

    reports = collect_reports(build_root)
    assert reports[0].status == "missing"

    out_html = tmp_path / "reports" / "hls_summary.html"
    to_html(reports, out_html)
    content = out_html.read_text()
    assert content.count("<td>—</td>") == 14
