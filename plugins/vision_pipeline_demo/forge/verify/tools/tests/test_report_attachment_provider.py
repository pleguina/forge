"""Plugin-side integration coverage for
plugins/vision_pipeline_demo/forge/verify/tools/report_attachment_provider.py
— the real ReportAttachmentProvider `forge report --plugin
vision_pipeline_demo` collects. See forge/tests/test_report_attachments.py
for FORGE core's own decoupled coverage of the generic registry/
aggregator/renderer plumbing this provider plugs into.
"""
from __future__ import annotations

import json
from pathlib import Path

from forge.analyze.dashboards.attachments import ReportAttachment

_PLUGIN_ROOT = Path(__file__).resolve().parents[3]
_DESIGNS_DIR = _PLUGIN_ROOT / "forge" / "designs"


def _provider():
    import report_attachment_provider as m
    return m.PROVIDER


def test_provider_id_is_stable():
    assert _provider().provider_id == "vision_pipeline.report_attachments"


def test_build_attachments_always_includes_ownership_legend(tmp_path):
    attachments = _provider().build_attachments(_DESIGNS_DIR / "design.yml", tmp_path)
    kinds = {(a.title, a.kind) for a in attachments}
    assert ("Ownership (project source vs. generated)", "markdown") in kinds
    md_attachment = next(a for a in attachments if a.kind == "markdown")
    assert (tmp_path / md_attachment.path).is_file()
    assert "PROJECT SOURCE" in (tmp_path / md_attachment.path).read_text()


def test_build_attachments_for_full_functional_includes_its_own_figures(tmp_path):
    attachments = _provider().build_attachments(_DESIGNS_DIR / "design_full_functional.yml", tmp_path)
    titles = {a.title for a in attachments}
    assert "full functional tile overlay" in titles
    assert "fifo high water mark" in titles
    assert "full-functional topology" in titles
    for a in attachments:
        if a.kind == "image":
            assert (tmp_path / a.path).is_file()


def test_build_attachments_for_cdc_has_no_image_panels_only_topology(tmp_path):
    """design_cdc.yml has no per-pixel data (all RTL, no HLS/golden-model
    image path) -- only its topology diagram is a real attachment.
    """
    attachments = _provider().build_attachments(_DESIGNS_DIR / "design_cdc.yml", tmp_path)
    image_titles = {a.title for a in attachments if a.kind == "image"}
    assert image_titles == {"cdc topology"}


def test_build_attachments_returns_valid_report_attachments(tmp_path):
    attachments = _provider().build_attachments(_DESIGNS_DIR / "design.yml", tmp_path)
    assert attachments
    for a in attachments:
        assert isinstance(a, ReportAttachment)
        assert a.provider_id == _provider().provider_id


def test_build_attachments_is_json_serializable_round_trip(tmp_path):
    attachments = _provider().build_attachments(_DESIGNS_DIR / "design_pixel_result.yml", tmp_path)
    payload = json.dumps([a.to_dict() for a in attachments])
    restored = [ReportAttachment.from_dict(d) for d in json.loads(payload)]
    assert restored == list(attachments)


def test_unknown_design_gets_ownership_legend_only(tmp_path):
    attachments = _provider().build_attachments(_DESIGNS_DIR / "invalid_direct_bus_cdc.yml", tmp_path)
    assert len(attachments) == 1
    assert attachments[0].kind == "markdown"
