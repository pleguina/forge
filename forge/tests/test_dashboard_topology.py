"""Tests for the dashboard's "Design Topology" section — `forge report`
already writes topology.svg/topology_explorer.html unconditionally
alongside dashboard.html, but until now nothing in the aggregated
dashboard linked to them, so a reader had to know to browse the output
directory to find the interactive explorer. See
forge/tests/test_report_cli_group.py for the real end-to-end coverage
of `forge report` itself producing these files; this file covers just
the aggregator/renderer plumbing that surfaces them.
"""
from __future__ import annotations

from pathlib import Path

from forge.analysis.dashboards.aggregator import collect
from forge.analysis.dashboards.renderer import render_html, render_markdown_summary

_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>'


def test_collect_finds_topology_files(tmp_path: Path) -> None:
    (tmp_path / "topology.svg").write_bytes(_SVG)
    (tmp_path / "topology_explorer.html").write_text("<html>explorer</html>")

    report = collect(tmp_path)
    assert report.topology_svg == tmp_path / "topology.svg"
    assert report.topology_explorer_html == tmp_path / "topology_explorer.html"


def test_collect_without_topology_files_is_none(tmp_path: Path) -> None:
    report = collect(tmp_path)
    assert report.topology_svg is None
    assert report.topology_explorer_html is None


def test_render_html_embeds_svg_and_links_explorer(tmp_path: Path) -> None:
    (tmp_path / "topology.svg").write_bytes(_SVG)
    (tmp_path / "topology_explorer.html").write_text("<html>explorer</html>")
    report = collect(tmp_path)

    out = tmp_path / "dashboard.html"
    render_html(report, out)
    html = out.read_text()

    assert "Design Topology" in html
    assert "data:image/svg+xml;base64," in html
    assert 'href="topology_explorer.html"' in html


def test_render_html_omits_topology_section_when_absent(tmp_path: Path) -> None:
    report = collect(tmp_path)
    out = tmp_path / "dashboard.html"
    render_html(report, out)
    assert "Design Topology" not in out.read_text()


def test_render_markdown_summary_links_topology_files(tmp_path: Path) -> None:
    (tmp_path / "topology.svg").write_bytes(_SVG)
    (tmp_path / "topology_explorer.html").write_text("<html>explorer</html>")
    report = collect(tmp_path)

    out = tmp_path / "summary.md"
    render_markdown_summary(report, out)
    text = out.read_text()
    assert "[topology.svg](topology.svg)" in text
    assert "[topology_explorer.html](topology_explorer.html) (interactive)" in text
