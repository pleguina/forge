"""Structural regression coverage for the tutorial's local visual language
(docs/assets/stylesheets/forge.css): ownership badges, the tutorial
landing page, and chapter-to-chapter navigation. Mirrors the `built_site`
fixture pattern already established in test_docs_site_offline.py/
test_docs_site_links.py rather than sharing a fixture across files, same
as those two do.

This does not re-check "no external assets" (test_docs_site_offline.py
already owns that) or link/anchor validity (test_docs_site_links.py
already owns that) -- only the visual-language additions themselves.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MKDOCS_CONFIG = REPO_ROOT / "mkdocs.yml"

skip_without_mkdocs = pytest.mark.skipif(
    importlib.util.find_spec("mkdocs") is None,
    reason="mkdocs not installed (optional 'docs' extra — pip install -e 'forge[docs]')",
)


@pytest.fixture(scope="module")
def built_site(tmp_path_factory) -> Path:
    site_dir = tmp_path_factory.mktemp("site")
    subprocess.run(
        [
            sys.executable, "-m", "mkdocs", "build", "--strict",
            "--config-file", str(MKDOCS_CONFIG),
            "--site-dir", str(site_dir),
        ],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    )
    return site_dir


@skip_without_mkdocs
def test_forge_css_is_defined_and_linked(built_site: Path) -> None:
    css_path = built_site / "assets" / "stylesheets" / "forge.css"
    assert css_path.is_file() and css_path.stat().st_size > 0

    css_text = css_path.read_text()
    for cls in ("badge-source", "badge-generated", "badge-toolchain", "badge-verification", "badge-tutorial"):
        assert f".{cls}" in css_text, f"forge.css is missing the {cls} rule"

    quickstart_html = (built_site / "tutorials" / "vision-pipeline" / "01-quickstart" / "index.html").read_text()
    assert 'href="../../../assets/stylesheets/forge.css"' in quickstart_html


@skip_without_mkdocs
def test_logo_and_favicon_are_present_and_reasonably_sized(built_site: Path) -> None:
    """The site's brand assets are local files under docs/assets/images/
    (no CDN/external logo host — offline requirement) and must stay small
    since the favicon/logo load on every single page."""
    logo = built_site / "assets" / "images" / "forge-logo.png"
    favicon = built_site / "assets" / "images" / "favicon.png"
    assert logo.is_file() and 0 < logo.stat().st_size < 200_000
    assert favicon.is_file() and 0 < favicon.stat().st_size < 20_000

    index_html = (built_site / "index.html").read_text()
    assert 'href="assets/images/favicon.png"' in index_html
    assert 'src="assets/images/forge-logo.png"' in index_html


@skip_without_mkdocs
def test_ownership_labels_render_as_badges_not_bare_bold_text(built_site: Path) -> None:
    """Every chapter that annotates ownership must use the styled badge
    span, not the plain **LABEL** markdown bold text it replaced -- a
    stale chapter still using bare bold text would silently fall back to
    unstyled text with no way to tell from a clean `mkdocs build` alone.
    """
    chapters_with_ownership = [
        "01-quickstart", "02-project-structure-and-contracts", "03-mixed-rtl-hls",
        "05-bounded-and-elastic-processing", "06-clock-domains-and-cdc",
        "07-throughput-backpressure-and-fifos", "08-datasets-and-golden-models",
        "09-full-functional-design", "10-platform-integration",
        "11-inspect-report-and-reproduce",
    ]
    for chapter in chapters_with_ownership:
        html = (built_site / "tutorials" / "vision-pipeline" / chapter / "index.html").read_text()
        assert 'class="badge badge-' in html, f"{chapter} has no rendered ownership badge"
        assert "<strong>PROJECT SOURCE</strong>" not in html, f"{chapter} still has an un-migrated bold label"

    index_html = (built_site / "tutorials" / "vision-pipeline" / "index.html").read_text()
    assert 'class="badge badge-source">PROJECT SOURCE</span>' in index_html


@skip_without_mkdocs
def test_vision_pipeline_nav_group_is_not_buried(built_site: Path) -> None:
    """Plan requirement: the tutorial must be a visible top-level nav
    group, not nested under Explanation/Reference."""
    index_html = (built_site / "index.html").read_text()
    assert "Vision Pipeline" in index_html
    assert 'href="tutorials/vision-pipeline/"' in index_html or "vision-pipeline/" in index_html


@skip_without_mkdocs
def test_chapter_prev_next_links_are_present(built_site: Path) -> None:
    """Every interior chapter should link to the chapter before and
    after it, not only to next-chapter prose links inside the body."""
    chapter_html = (built_site / "tutorials" / "vision-pipeline" / "05-bounded-and-elastic-processing" / "index.html").read_text()
    assert 'rel="prev"' in chapter_html
    assert 'rel="next"' in chapter_html


@skip_without_mkdocs
def test_step_progress_strip_marks_current_chapter_and_links_siblings(built_site: Path) -> None:
    """Regression test for a real bug caught during T7: the strip was
    first written as raw <a href="NN-slug.md"> HTML, which MkDocs does
    NOT relative-link-rewrite (only real Markdown link syntax gets
    rewritten for use_directory_urls) -- it silently produced a
    double-nested, broken href in the rendered site. Fixed by using
    `markdown="span"` (md_in_html) with real Markdown link syntax, same
    as the existing prev/next chapter links. This test would have caught
    that bug; keep it red-first if the strip's markup changes again.
    """
    html = (built_site / "tutorials" / "vision-pipeline" / "05-bounded-and-elastic-processing" / "index.html").read_text()
    assert '<nav aria-label="Chapter progress" class="forge-step-strip">' in html
    assert "<strong>05</strong>" in html
    assert 'href="../06-clock-domains-and-cdc/">06</a>' in html
    # every sibling-chapter target must actually exist in the built site
    for slug in ("01-quickstart", "06-clock-domains-and-cdc", "12-diagnostics-and-negative-fixtures"):
        assert (built_site / "tutorials" / "vision-pipeline" / slug / "index.html").is_file()
