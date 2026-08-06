"""No-external-runtime-asset test for the built MkDocs site.

The site must build and load fully offline: `mkdocs.yml` disables webfont
loading (`theme.font: false`) and any analytics/social-card plugin, and
this test is the automated proof that holds — not just an unenforced
config claim. Mirrors the precise, non-blanket mechanism
`forge/tests/test_design_explorer_html_renderer.py` already established
for the visual design explorer: a *real resource load* (a `<script src>`/
`<img src>`/stylesheet-or-font `<link>` actually pointing off-host), never
a blanket "https://" string ban — which would false-positive on this
site's own legitimate `<a href="https://...">` content links and
`<link rel="canonical">` SEO metadata (both real, both present, neither a
CDN dependency). Scoped to the *rendered HTML pages*, not third-party
vendored JS bundles (`assets/javascripts/bundle*.min.js`), which may
contain a dormant, feature-flagged code path referencing a host string
that is never reached because this site's own config (`font: false`, no
social/analytics plugins) never activates it — checking the actual
rendered output is what proves the *configured* site is offline-capable,
not what a shared third-party library merely *could* do.
"""
from __future__ import annotations

import importlib.util
import re
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

_RESOURCE_LINK_RELS = {"stylesheet", "preload", "font", "icon", "shortcut icon", "prefetch"}

_NETWORK_CALL_PATTERNS = [
    re.compile(r"fetch\s*\("),
    re.compile(r"XMLHttpRequest"),
    re.compile(r"WebSocket"),
]


def _is_external(url: "str | None") -> bool:
    if not url:
        return False
    return url.startswith("http://") or url.startswith("https://") or url.startswith("//")


class _ExternalResourceCollector(HTMLParser):
    """Finds real resource-loading tags pointing off-host — never `<a
    href>` hyperlinks or `<link rel="canonical">` metadata, neither of
    which triggers a fetch."""

    def __init__(self) -> None:
        super().__init__()
        self.offenders: "list[str]" = []
        self.inline_script_text: "list[str]" = []
        self._in_script = False

    def handle_starttag(self, tag: str, attrs: "list[tuple[str, str | None]]") -> None:
        attr_dict = dict(attrs)
        if tag in ("script", "img") and _is_external(attr_dict.get("src")):
            self.offenders.append(f"<{tag} src={attr_dict.get('src')!r}>")
        elif tag == "link" and (attr_dict.get("rel") or "").lower() in _RESOURCE_LINK_RELS:
            if _is_external(attr_dict.get("href")):
                self.offenders.append(f"<link rel={attr_dict.get('rel')!r} href={attr_dict.get('href')!r}>")
        if tag == "script" and not attr_dict.get("src"):
            self._in_script = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self.inline_script_text.append(data)


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
def test_no_external_resource_loads_in_rendered_html(built_site: Path) -> None:
    offenders = []
    for html_file in built_site.rglob("*.html"):
        collector = _ExternalResourceCollector()
        collector.feed(html_file.read_text(encoding="utf-8"))
        for offense in collector.offenders:
            offenders.append(f"{html_file.relative_to(built_site)}: {offense}")
    assert offenders == [], "external resource load(s) found:\n" + "\n".join(offenders)


@skip_without_mkdocs
def test_no_runtime_network_calls_inlined_in_rendered_html(built_site: Path) -> None:
    """Checks inline `<script>` blocks actually embedded in a rendered
    page — not third-party vendored bundles, whose internals this repo
    doesn't control and which may legitimately implement `fetch`-capable
    features never exercised by this site's own configuration."""
    offenders = []
    for html_file in built_site.rglob("*.html"):
        collector = _ExternalResourceCollector()
        collector.feed(html_file.read_text(encoding="utf-8"))
        inline_js = "\n".join(collector.inline_script_text)
        for pattern in _NETWORK_CALL_PATTERNS:
            if pattern.search(inline_js):
                offenders.append(f"{html_file.relative_to(built_site)}: {pattern.pattern}")
    assert offenders == [], "network-triggering call(s) found:\n" + "\n".join(offenders)
