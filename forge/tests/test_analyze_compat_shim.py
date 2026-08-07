"""Proves forge/analyze/ (the permanent compatibility alias for
forge.analysis, see docs/development/adr/0005-package-and-cli-naming.md)
actually works for the exact import patterns real, already-deployed
plugin tool code uses — independent of whether this repo's own reference
plugins happen to import the old or new name at any given time.
"""
from __future__ import annotations


def test_dashboards_attachments_shim_is_the_same_object_as_the_real_module() -> None:
    from forge.analyze.dashboards.attachments import register_report_attachment_provider
    from forge.analysis.dashboards.attachments import (
        register_report_attachment_provider as real,
    )

    assert register_report_attachment_provider is real


def test_hls_reports_extractor_shim_matches_plugin_import_pattern() -> None:
    """`plugins/vision_pipeline_demo/forge/verify/tools/check_throughput_sustainability.py`
    and `render_throughput_result.py` both write this exact statement."""
    from forge.analyze.hls_reports.extractor import collect_reports
    from forge.analysis.hls_reports.extractor import collect_reports as real

    assert collect_reports is real


def test_throughput_static_model_shim_matches_plugin_import_pattern() -> None:
    from forge.analyze.throughput_static.model import build_static_throughput_analysis
    from forge.analysis.throughput_static.model import (
        build_static_throughput_analysis as real,
    )

    assert build_static_throughput_analysis is real


def test_design_explorer_shim_respects_the_real___all__() -> None:
    """forge.analyze.design_explorer is in the frozen public API
    (docs/reference/public-python-api.md) — its __all__ must resolve
    identically through the shim."""
    import forge.analyze.design_explorer as shim_module
    import forge.analysis.design_explorer as real_module

    assert shim_module.DesignGraph is real_module.DesignGraph
    assert shim_module.build_design_graph is real_module.build_design_graph
