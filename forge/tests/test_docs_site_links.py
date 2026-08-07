"""Built-HTML link/anchor checker.

`mkdocs build --strict` already fails on a broken *Markdown-source* link
it can resolve against its own nav/file tree, but that isn't the same
claim as "the built site has no broken links" — it doesn't crawl the
*rendered* HTML, doesn't check `<img src>`/asset references, and doesn't
check URL fragments (`#anchor`) resolve to a real heading id in the
target page. This module crawls the actual built `site/` output instead —
local pages, assets, and fragments — and explicitly, non-blockingly skips
external `http(s)://`/`mailto:` links (this is an offline build; asserting
those are reachable would require network access and prove nothing about
the site itself).
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MKDOCS_CONFIG = REPO_ROOT / "mkdocs.yml"

skip_without_mkdocs = pytest.mark.skipif(
    importlib.util.find_spec("mkdocs") is None,
    reason="mkdocs not installed (optional 'docs' extra — pip install -e 'forge[docs]')",
)

_EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "javascript:", "data:", "//")
_LINK_ATTRS = {
    "a": "href", "link": "href", "img": "src", "script": "src", "source": "src",
}


class _PageScanner(HTMLParser):
    """Collects every local link/asset target and every real anchor id on
    one rendered page."""

    def __init__(self) -> None:
        super().__init__()
        self.links: "list[str]" = []
        self.anchor_ids: "set[str]" = set()

    def handle_starttag(self, tag: str, attrs: "list[tuple[str, str | None]]") -> None:
        attr_dict = dict(attrs)
        attr_name = _LINK_ATTRS.get(tag)
        if attr_name and attr_dict.get(attr_name):
            self.links.append(attr_dict[attr_name])
        if attr_dict.get("id"):
            self.anchor_ids.add(attr_dict["id"])
        if tag == "a" and attr_dict.get("name"):
            self.anchor_ids.add(attr_dict["name"])


def _is_external(href: str) -> bool:
    return href.startswith(_EXTERNAL_PREFIXES)


def _scan(html_path: Path) -> _PageScanner:
    scanner = _PageScanner()
    scanner.feed(html_path.read_text(encoding="utf-8"))
    return scanner


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


@pytest.fixture(scope="module")
def page_anchors(built_site: Path) -> "dict[Path, set[str]]":
    """Every built HTML file's real anchor-id set, computed once."""
    return {html_file: _scan(html_file).anchor_ids for html_file in built_site.rglob("*.html")}


@skip_without_mkdocs
def test_no_broken_internal_links_assets_or_anchors(
    built_site: Path, page_anchors: "dict[Path, set[str]]",
) -> None:
    broken: "list[str]" = []

    for html_file, _anchors in page_anchors.items():
        for href in _scan(html_file).links:
            if not href or _is_external(href):
                continue

            path_part, _, fragment = href.partition("#")
            path_part = path_part.split("?", 1)[0]

            if not path_part:
                # Same-page fragment only.
                if fragment and fragment not in page_anchors[html_file]:
                    broken.append(
                        f"{html_file.relative_to(built_site)}: broken same-page anchor #{fragment}"
                    )
                continue

            # A leading "/" is *site-root*-relative (Material's 404.html
            # legitimately uses these, since a 404 page can be "reached"
            # from any depth) — resolve against built_site, not the OS
            # filesystem root (pathlib's `/` operator discards the left
            # operand entirely when the right side is absolute, which
            # would otherwise silently resolve to a real filesystem path
            # like `/assets/...` and always report broken).
            if path_part.startswith("/"):
                target = (built_site / path_part.lstrip("/")).resolve()
            else:
                target = (html_file.parent / path_part).resolve()
            if target.is_dir():
                target = target / "index.html"

            if not target.exists():
                broken.append(
                    f"{html_file.relative_to(built_site)}: broken link {href!r} "
                    f"(resolved to {target.relative_to(built_site) if built_site in target.parents else target})"
                )
                continue

            if fragment and target.suffix == ".html":
                target_anchors = page_anchors.get(target)
                if target_anchors is None:
                    target_anchors = _scan(target).anchor_ids
                if fragment not in target_anchors:
                    broken.append(
                        f"{html_file.relative_to(built_site)}: broken anchor "
                        f"#{fragment} in link to {href!r}"
                    )

    assert broken == [], "Broken internal link(s)/anchor(s):\n" + "\n".join(sorted(broken))


@skip_without_mkdocs
def test_excluded_content_is_truly_absent_from_built_site(built_site: Path) -> None:
    """Proves exclude_docs actually worked, for real — not just
    that the config exists (complements forge/tests/test_docs_site_build.py's
    equivalent check, run here too since this module builds its own site
    fixture independently)."""
    assert not (built_site / "plan").exists()
    assert list(built_site.rglob("*release-readiness*")) == []
