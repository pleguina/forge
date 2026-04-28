"""arc.analyze.dashboards.aggregator
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Read the outputs produced by the other four analyze modules from a reports
directory and return an :class:`AggregatedReport` used by the renderer.

Expected directory layout (produced by ``arc analyze`` commands)::

    out/reports/
        hls_summary.csv
        hls_summary.md
        hls_summary.html           # optional
        latency_check.md           # optional
        runtime_latency.md         # optional
        plots/
            *.png                  # from arc analyze plot-results

The aggregator is intentionally lenient: missing files are silently skipped
and their sections appear as empty in the rendered dashboard.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import List, Optional


@dataclasses.dataclass
class AggregatedReport:
    """Flat container for paths that the renderer will embed or link."""
    hls_summary_md:       Optional[Path] = None
    latency_check_md:     Optional[Path] = None
    runtime_latency_md:   Optional[Path] = None
    plot_pngs:            List[Path] = dataclasses.field(default_factory=list)


def collect(reports_dir: Path) -> AggregatedReport:
    """Scan *reports_dir* and return paths of found artifacts."""
    def _find(*names: str) -> Optional[Path]:
        for name in names:
            p = reports_dir / name
            if p.exists():
                return p
        return None

    plots_dir = reports_dir / "plots"
    pngs: List[Path] = []
    if plots_dir.is_dir():
        pngs = sorted(plots_dir.glob("*.png"))

    return AggregatedReport(
        hls_summary_md      = _find("hls_summary.md"),
        latency_check_md    = _find("latency_check.md"),
        runtime_latency_md  = _find("runtime_latency.md"),
        plot_pngs           = pngs,
    )
