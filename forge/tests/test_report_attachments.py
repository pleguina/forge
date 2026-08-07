"""Tests for the project-attachment extension point `forge report` uses
to let a plugin contribute extra sections (forge.analysis.dashboards.
attachments/aggregator/renderer) without FORGE core understanding the
domain semantics behind them.

These tests use a fake, in-repo-agnostic provider — not
vision_pipeline_demo's real one — to keep FORGE core's own test suite
decoupled from any specific plugin's domain content. See
plugins/vision_pipeline_demo/forge/verify/tools/tests/test_report_attachment_provider.py
for the real, plugin-side integration coverage.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge.analysis.dashboards.aggregator import collect
from forge.analysis.dashboards.attachments import (
    ReportAttachment,
    _reset_for_testing,
    get_report_attachment_providers,
    register_report_attachment_provider,
)
from forge.analysis.dashboards.renderer import render_html, render_markdown_summary


@pytest.fixture(autouse=True)
def _clean_registry():
    _reset_for_testing()
    yield
    _reset_for_testing()


class _FakeProvider:
    provider_id = "fake_provider"

    def __init__(self, attachments):
        self._attachments = attachments

    def build_attachments(self, design_path: Path, output_dir: Path):
        return self._attachments


# ── ReportAttachment / registry ──────────────────────────────────────────

def test_attachment_rejects_unknown_kind():
    with pytest.raises(ValueError):
        ReportAttachment(provider_id="x", title="t", kind="video", path="x.mp4")


def test_attachment_round_trips_through_dict():
    a = ReportAttachment(provider_id="x", title="t", kind="image", path="figures/a.png", description="d")
    assert ReportAttachment.from_dict(a.to_dict()) == a


def test_registry_register_and_get():
    provider = _FakeProvider([])
    register_report_attachment_provider(provider)
    assert get_report_attachment_providers() == [provider]


def test_registry_reregistering_same_id_replaces():
    p1, p2 = _FakeProvider([]), _FakeProvider([])
    p1.provider_id = p2.provider_id = "same_id"
    register_report_attachment_provider(p1)
    register_report_attachment_provider(p2)
    providers = get_report_attachment_providers()
    assert len(providers) == 1
    assert providers[0] is p2


# ── aggregator.collect() reading attachments.json ────────────────────────

def test_collect_reads_attachments_json(tmp_path):
    (tmp_path / "note.md").write_text("# Hello\n\nReal content.\n")
    attachments = [ReportAttachment(provider_id="p", title="Note", kind="markdown", path="note.md")]
    (tmp_path / "attachments.json").write_text(json.dumps([a.to_dict() for a in attachments]))

    report = collect(tmp_path)
    assert report.attachments == attachments
    assert report.attachments_dir == tmp_path


def test_collect_with_no_attachments_json_is_empty(tmp_path):
    report = collect(tmp_path)
    assert report.attachments == []
    assert report.attachments_dir is None


# ── renderer embeds attachments generically ──────────────────────────────

def test_render_html_embeds_markdown_attachment(tmp_path):
    (tmp_path / "note.md").write_text("Real project content.")
    attachments = [ReportAttachment(provider_id="p", title="Ownership Legend", kind="markdown", path="note.md")]
    (tmp_path / "attachments.json").write_text(json.dumps([a.to_dict() for a in attachments]))
    report = collect(tmp_path)

    out = tmp_path / "dashboard.html"
    render_html(report, out)
    html = out.read_text()
    assert "Project Attachments" in html
    assert "Ownership Legend" in html
    assert "Real project content." in html


def test_render_html_embeds_image_attachment_as_base64(tmp_path):
    png_bytes = bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080600000"
        "01f15c4890000000a4944415478da6360000002000155a2415c0000000049454e44ae426082"
    )
    (tmp_path / "panel.png").write_bytes(png_bytes)
    attachments = [ReportAttachment(provider_id="p", title="Input Panel", kind="image", path="panel.png")]
    (tmp_path / "attachments.json").write_text(json.dumps([a.to_dict() for a in attachments]))
    report = collect(tmp_path)

    out = tmp_path / "dashboard.html"
    render_html(report, out)
    html = out.read_text()
    assert "data:image/png;base64," in html
    assert "Input Panel" in html


def test_render_html_reports_missing_attachment_file_honestly(tmp_path):
    attachments = [ReportAttachment(provider_id="p", title="Ghost", kind="image", path="does-not-exist.png")]
    (tmp_path / "attachments.json").write_text(json.dumps([a.to_dict() for a in attachments]))
    report = collect(tmp_path)

    out = tmp_path / "dashboard.html"
    render_html(report, out)
    html = out.read_text()
    assert "missing: does-not-exist.png" in html


def test_render_markdown_summary_lists_attachments(tmp_path):
    (tmp_path / "note.md").write_text("content")
    attachments = [ReportAttachment(provider_id="p", title="Note", kind="markdown", path="note.md", description="a real note")]
    (tmp_path / "attachments.json").write_text(json.dumps([a.to_dict() for a in attachments]))
    report = collect(tmp_path)

    out = tmp_path / "summary.md"
    render_markdown_summary(report, out)
    text = out.read_text()
    assert "Project Attachments" in text
    assert "Note" in text
    assert "a real note" in text


def test_render_markdown_summary_says_none_without_attachments(tmp_path):
    report = collect(tmp_path)
    out = tmp_path / "summary.md"
    render_markdown_summary(report, out)
    assert "*none*" in out.read_text()
