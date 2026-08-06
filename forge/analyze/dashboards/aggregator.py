"""forge.analyze.dashboards.aggregator
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Read the outputs produced by the other four analyze modules from a reports
directory and return an :class:`AggregatedReport` used by the renderer.

Expected directory layout (produced by ``forge analyze`` commands)::

    out/reports/
        hls_summary.csv
        hls_summary.md
        hls_summary.html           # optional
        latency_check.md           # optional
        runtime_latency.md         # optional
        plots/
            *.png                  # from forge analyze plot-results
        attachments.json           # optional — see forge.analyze.dashboards.attachments
        topology.svg               # optional — from forge report's own topology step
        topology_explorer.html     # optional — interactive companion to topology.svg

The aggregator is intentionally lenient: missing files are silently skipped
and their sections appear as empty in the rendered dashboard.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import List, Optional

from forge.analyze.dashboards.attachments import ReportAttachment


@dataclasses.dataclass
class AggregatedReport:
    """Flat container for paths that the renderer will embed or link."""
    hls_summary_md:       Optional[Path] = None
    latency_check_md:     Optional[Path] = None
    runtime_latency_md:   Optional[Path] = None
    plot_pngs:            List[Path] = dataclasses.field(default_factory=list)
    attachments:          List[ReportAttachment] = dataclasses.field(default_factory=list)
    attachments_dir:      Optional[Path] = None
    topology_svg:         Optional[Path] = None
    topology_explorer_html: Optional[Path] = None


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

    attachments: List[ReportAttachment] = []
    attachments_path = reports_dir / "attachments.json"
    if attachments_path.is_file():
        raw = json.loads(attachments_path.read_text())
        attachments = [ReportAttachment.from_dict(entry) for entry in raw]

    return AggregatedReport(
        hls_summary_md      = _find("hls_summary.md"),
        latency_check_md    = _find("latency_check.md"),
        runtime_latency_md  = _find("runtime_latency.md"),
        plot_pngs           = pngs,
        attachments         = attachments,
        attachments_dir     = reports_dir if attachments else None,
        topology_svg            = _find("topology.svg"),
        topology_explorer_html  = _find("topology_explorer.html"),
    )
