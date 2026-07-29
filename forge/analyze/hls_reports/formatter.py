"""forge.analyze.hls_reports.formatter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Render a list of :class:`HLSModuleReport` as CSV, Markdown, and HTML.

All three formats write to files under an output directory supplied by the
caller. The caller is responsible for creating the parent directory; each
``to_*`` function creates missing intermediate directories itself.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import List

from forge.analyze.hls_reports.extractor import HLSModuleReport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HTML_STYLE = """
<style>
  body { font-family: sans-serif; margin: 2em; }
  h1   { color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: .3em; }
  table { border-collapse: collapse; width: 100%; font-size: .9em; }
  th, td { border: 1px solid #ccc; padding: 6px 10px; text-align: right; }
  th { background: #2c3e50; color: white; text-align: center; }
  tr:nth-child(even) { background: #f5f5f5; }
  .ok   { color: #27ae60; }
  .warn { color: #c0392b; font-weight: bold; }
  .miss { color: #95a5a6; font-style: italic; }
  td.name { text-align: left; font-weight: bold; }
  footer { margin-top: 2em; font-size: .8em; color: #999; }
</style>
"""

_CSV_FIELDS = [
    "module", "status",
    "target_clock_ns", "estimated_clock_ns", "estimated_fmax_mhz",
    "timing_met", "slack_ns",
    "latency_best", "latency_avg", "latency_worst",
    "pipeline_ii", "pipeline_depth", "pipeline_type",
    # release-plan §4.4 (Phase 4 slice 4): recovered, previously-dropped
    # throughput range data.
    "interval_min", "interval_max",
    "lut", "ff", "dsp", "bram_18k", "uram",
    "lut_pct", "ff_pct", "dsp_pct", "bram_pct", "uram_pct",
]


def _csv_row(r: HLSModuleReport) -> dict:
    ok = r.status == "ok"
    u = r.resources.used
    p = r.resources.utilization_pct
    return {
        "module": r.module_name,
        "status": r.status,
        "target_clock_ns":    r.target_clock_ns    if ok else "",
        "estimated_clock_ns": r.estimated_clock_ns  if ok else "",
        "estimated_fmax_mhz": r.estimated_fmax_mhz  if ok else "",
        "timing_met": ("yes" if r.timing_met else "NO") if ok and r.timing_met is not None else "",
        "slack_ns":          r.slack_ns          if ok else "",
        "latency_best":      r.latency_best      if ok else "",
        "latency_avg":       r.latency_avg       if ok else "",
        "latency_worst":     r.latency_worst     if ok else "",
        "pipeline_ii":       r.pipeline_ii       if ok else "",
        "pipeline_depth":    r.pipeline_depth    if ok else "",
        "pipeline_type":     r.pipeline_type     if ok else "",
        "interval_min":      r.interval_min      if ok else "",
        "interval_max":      r.interval_max      if ok else "",
        "lut":  int(u.LUT)      if ok else "",
        "ff":   int(u.FF)       if ok else "",
        "dsp":  int(u.DSP)      if ok else "",
        "bram_18k": int(u.BRAM_18K) if ok else "",
        "uram": int(u.URAM)     if ok else "",
        "lut_pct":  f"{p.LUT:.1f}"      if ok else "",
        "ff_pct":   f"{p.FF:.1f}"       if ok else "",
        "dsp_pct":  f"{p.DSP:.1f}"      if ok else "",
        "bram_pct": f"{p.BRAM_18K:.1f}" if ok else "",
        "uram_pct": f"{p.URAM:.1f}"     if ok else "",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def to_csv(reports: List[HLSModuleReport], out_path: Path) -> None:
    """Write a CSV summary to *out_path*."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for r in reports:
            writer.writerow(_csv_row(r))


def to_markdown(reports: List[HLSModuleReport], out_path: Path) -> None:
    """Write a Markdown summary table to *out_path*."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# HLS Synthesis Report", "",
        "| Module | Status | Fmax (MHz) | Slack (ns) | II | II Range | Lat (W) |"
        " LUT | FF | DSP | BRAM |",
        "|--------|--------|-----------|-----------|-----|----------|---------|"
        "-----|-----|-----|------|",
    ]
    for r in reports:
        if r.status == "ok":
            warn = " ⚠" if r.timing_met is False else ""
            fmax = f"{r.estimated_fmax_mhz:.1f}{warn}"
            slack = f"{r.slack_ns:.3f}{warn}"
            u = r.resources.used
            # release-plan §4.4: interval_min/interval_max express
            # throughput as a range — omitted (not "0-0") when the XML
            # never carried the data at all, to avoid implying a
            # fabricated 0-cycle interval.
            ii_range = f"{r.interval_min}–{r.interval_max}" if (r.interval_min or r.interval_max) else "—"
            lines.append(
                f"| {r.module_name} | ✅ ok | {fmax} | {slack} | {r.pipeline_ii} | {ii_range} |"
                f" {r.latency_worst} | {int(u.LUT)} | {int(u.FF)} |"
                f" {int(u.DSP)} | {int(u.BRAM_18K)} |"
            )
        else:
            lines.append(
                f"| {r.module_name} | {r.status} | — | — | — | — | — | — | — | — | — |"
            )

    lines += [
        "",
        f"*{len(reports)} module(s) — generated by `forge analyze hls-report`*",
        "",
    ]
    out_path.write_text("\n".join(lines))


def to_html(reports: List[HLSModuleReport], out_path: Path) -> None:
    """Write a standalone HTML report to *out_path*."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    row_html: List[str] = []
    for r in reports:
        if r.status == "ok":
            tc = "ok" if r.timing_met else "warn"
            u = r.resources.used
            p = r.resources.utilization_pct
            ii_range = f"{r.interval_min}–{r.interval_max}" if (r.interval_min or r.interval_max) else "—"
            row_html.append(
                f'<tr>'
                f'<td class="name">{r.module_name}</td>'
                f'<td class="ok">ok</td>'
                f'<td>{r.target_clock_ns}</td>'
                f'<td>{r.estimated_clock_ns}</td>'
                f'<td class="{tc}">{r.estimated_fmax_mhz:.1f}</td>'
                f'<td class="{tc}">{r.slack_ns:.3f}</td>'
                f'<td>{r.pipeline_ii}</td>'
                f'<td>{ii_range}</td>'
                f'<td>{r.latency_best}</td>'
                f'<td>{r.latency_avg}</td>'
                f'<td>{r.latency_worst}</td>'
                f'<td>{int(u.LUT)} <small>({p.LUT:.1f}%)</small></td>'
                f'<td>{int(u.FF)} <small>({p.FF:.1f}%)</small></td>'
                f'<td>{int(u.DSP)} <small>({p.DSP:.1f}%)</small></td>'
                f'<td>{int(u.BRAM_18K)} <small>({p.BRAM_18K:.1f}%)</small></td>'
                f'<td>{int(u.URAM)}</td>'
                f'</tr>'
            )
        else:
            row_html.append(
                f'<tr>'
                f'<td class="name">{r.module_name}</td>'
                f'<td class="miss">{r.status}</td>'
                + "<td>—</td>" * 14
                + "</tr>"
            )

    html = (
        "<!DOCTYPE html>\n"
        '<html><head><meta charset="UTF-8">'
        "<title>HLS Synthesis Report</title>"
        f"{_HTML_STYLE}"
        "</head><body>"
        "<h1>HLS Synthesis Report</h1>"
        "<table><tr>"
        "<th>Module</th><th>Status</th>"
        "<th>Target (ns)</th><th>Est. (ns)</th>"
        "<th>Fmax (MHz)</th><th>Slack (ns)</th>"
        "<th>II</th><th>II Range</th><th>Lat Best</th><th>Lat Avg</th><th>Lat Worst</th>"
        "<th>LUT</th><th>FF</th><th>DSP</th><th>BRAM</th><th>URAM</th>"
        "</tr>\n"
        + "\n".join(row_html)
        + "\n</table>"
        "<footer>Generated by <code>forge analyze hls-report</code></footer>"
        "</body></html>"
    )
    out_path.write_text(html)
