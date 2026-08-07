#!/usr/bin/env python3
"""vision_pipeline_demo's ReportAttachmentProvider — contributes the
project-owned sections `forge report` has no business understanding
itself: the ownership legend (source-vs-generated) and this design's own
real image-domain/topology figures, reusing the already-committed,
already-verified reference assets under
docs/assets/generated/vision-pipeline/ rather than regenerating them on
every report run (the same "release/acceptance execution produces
canonical artifacts, a report just displays them" model
tutorial/generate_reference_assets.py itself follows).

Registers via forge.analysis.dashboards.attachments — see
docs/development/adr's golden-model/dataset ADRs for the analogous
ownership-boundary reasoning applied here to reports instead.
"""
from __future__ import annotations

import importlib.util as _ilu
import shutil
import sys
from pathlib import Path
from typing import List, Sequence

from forge.analysis.dashboards.attachments import ReportAttachment

PROVIDER_ID = "vision_pipeline.report_attachments"

_PLUGIN_ROOT = Path(__file__).resolve().parents[3]
_ASSETS_ROOT = _PLUGIN_ROOT.parents[1] / "docs" / "assets" / "generated" / "vision-pipeline"

# design.yml basename -> (figures under docs/assets/.../figures/, diagrams under .../diagrams/)
_DESIGN_ASSETS = {
    "design.yml": (
        ["quickstart-input", "quickstart-normalized", "quickstart-mask"],
        ["quickstart"],
    ),
    "design_pixel_result.yml": (
        ["pixel-result-input", "pixel-result-normalized", "pixel-result-gradient", "pixel-result-mask"],
        ["pixel-result"],
    ),
    "design_tile_stats.yml": (
        ["tile-stats-overlay"],
        ["tile-stats"],
    ),
    "design_cdc.yml": (
        [],
        ["cdc"],
    ),
    "design_packetizer.yml": (
        ["fifo-high-water-mark"],
        [],
    ),
    "invalid_fifo_depth_packetizer.yml": (
        ["fifo-high-water-mark"],
        [],
    ),
    "design_full_functional.yml": (
        ["full-functional-tile-overlay", "fifo-high-water-mark"],
        ["full-functional"],
    ),
    "design_platform_wrapper.yml": (
        [],
        ["platform-wrapper"],
    ),
}


def _load_ownership_module():
    tutorial_dir = _PLUGIN_ROOT / "tutorial"
    spec = _ilu.spec_from_file_location(
        "vpd_ownership_for_report", tutorial_dir / "ownership.py",
    )
    module = _ilu.module_from_spec(spec)  # type: ignore[union-attr]
    spec.loader.exec_module(module)       # type: ignore[union-attr]
    return module


class VisionPipelineReportAttachmentProvider:
    provider_id = PROVIDER_ID

    def build_attachments(self, design_path: Path, output_dir: Path) -> Sequence[ReportAttachment]:
        attachments: List[ReportAttachment] = []

        ownership = _load_ownership_module()
        legend_lines = ["# Ownership", ""]
        for category in ownership.CATEGORIES:
            legend_lines.append(f"- **{category}**: {ownership.LEGEND[category]}")
        legend_lines += ["", "## Project source tree", "", "```", ownership.render_source_tree(), "```"]
        legend_path = output_dir / "vision_pipeline_ownership.md"
        legend_path.write_text("\n".join(legend_lines) + "\n")
        attachments.append(ReportAttachment(
            provider_id=PROVIDER_ID,
            title="Ownership (project source vs. generated)",
            kind="markdown",
            path=legend_path.name,
            description="Which files in this plugin you own and edit, versus what FORGE/the toolchain generate.",
        ))

        figure_names, diagram_names = _DESIGN_ASSETS.get(design_path.name, ([], []))
        figures_out = output_dir / "vision_pipeline_figures"
        figures_out.mkdir(parents=True, exist_ok=True)

        for name in figure_names:
            src = _ASSETS_ROOT / "figures" / f"{name}.png"
            if not src.is_file():
                continue
            dst = figures_out / src.name
            shutil.copyfile(src, dst)
            attachments.append(ReportAttachment(
                provider_id=PROVIDER_ID,
                title=name.replace("-", " "),
                kind="image",
                path=str(dst.relative_to(output_dir)),
                description="Rendered from a real golden dataset and this plugin's own GoldenModelProvider.",
            ))

        for name in diagram_names:
            src = _ASSETS_ROOT / "diagrams" / f"{name}.svg"
            if not src.is_file():
                continue
            dst = figures_out / src.name
            shutil.copyfile(src, dst)
            attachments.append(ReportAttachment(
                provider_id=PROVIDER_ID,
                title=f"{name} topology",
                kind="image",
                path=str(dst.relative_to(output_dir)),
                description="Rendered by FORGE core's own forge inspect --svg.",
            ))

        return attachments


PROVIDER = VisionPipelineReportAttachmentProvider()
