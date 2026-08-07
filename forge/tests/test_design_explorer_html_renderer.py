"""Tests for forge.analysis.design_explorer.html_renderer — the
self-contained, offline, interactive HTML explorer.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from forge.analysis.design_explorer.graph_model import build_design_graph
from forge.analysis.design_explorer.html_renderer import render_explorer_html
from forge.ir.build import build_project_ir_with_match_report

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSTHROUGH_DESIGN = REPO_ROOT / "plugins/passthrough_demo/forge/designs/design.yml"
PASSTHROUGH_MODULES = REPO_ROOT / "plugins/passthrough_demo/forge/modules.yml"
TRIGGER_DESIGN = REPO_ROOT / "plugins/trigger_demo/forge/designs/design.yml"
TRIGGER_MODULES = REPO_ROOT / "plugins/trigger_demo/forge/modules.yml"

# The precise, non-blanket offline-loading test: the real mechanisms that
# would cause a network request, never a blanket "https://" string ban
# (which would false-positive on a license comment citing an upstream
# URL, as this file's own vendor/README.md legitimately does).
_NETWORK_PATTERNS = [
    re.compile(r"<script\s+[^>]*src\s*=", re.IGNORECASE),
    re.compile(r"<link\s+[^>]*href\s*=", re.IGNORECASE),
    re.compile(r"fetch\s*\("),
    re.compile(r"XMLHttpRequest"),
    re.compile(r"WebSocket"),
    re.compile(r"(?<!function )import\s*\("),
]
_EXTERNAL_IMAGE_FONT_URL = re.compile(r'(?:src|href)\s*=\s*["\'](?!data:)https?://', re.IGNORECASE)


def _build_and_render(design: Path, modules: Path, out: Path):
    project, _cfg, _mr = build_project_ir_with_match_report(design, contracts_from=modules)
    graph = build_design_graph(project, source_roots=[REPO_ROOT])
    render_explorer_html(graph, out)
    return graph


@pytest.mark.parametrize("design,modules", [
    (PASSTHROUGH_DESIGN, PASSTHROUGH_MODULES),
    (TRIGGER_DESIGN, TRIGGER_MODULES),
])
def test_explorer_html_has_no_external_network_triggering_reference(design, modules, tmp_path):
    out = tmp_path / "explorer.html"
    _build_and_render(design, modules, out)
    text = out.read_text()

    for pattern in _NETWORK_PATTERNS:
        assert not pattern.search(text), f"found a real network-triggering reference: {pattern.pattern}"
    assert not _EXTERNAL_IMAGE_FONT_URL.search(text)


def test_explorer_html_is_well_formed():
    """A real html.parser-based well-formedness check — not a substring
    heuristic. html.parser is lenient by design, so a hard parse failure
    here is a genuine structural defect (e.g. an unescaped `<`/`>` in a
    data island breaking the surrounding markup)."""
    import html.parser

    out_text_holder = {}

    class _Checker(html.parser.HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.open_tags: list[str] = []
            self.saw_script_close = False

        def handle_starttag(self, tag, attrs):
            _VOID = {"br", "hr", "img", "input", "meta", "link"}
            if tag not in _VOID:
                self.open_tags.append(tag)

        def handle_endtag(self, tag):
            assert self.open_tags and self.open_tags[-1] == tag, (
                f"mismatched close tag </{tag}>, open stack was {self.open_tags}"
            )
            self.open_tags.pop()

    import tempfile
    project, _cfg, _mr = build_project_ir_with_match_report(TRIGGER_DESIGN, contracts_from=TRIGGER_MODULES)
    graph = build_design_graph(project, source_roots=[REPO_ROOT])
    out = Path(tempfile.mkdtemp()) / "explorer.html"
    render_explorer_html(graph, out)

    checker = _Checker()
    checker.feed(out.read_text())
    assert checker.open_tags == [], f"unclosed tags: {checker.open_tags}"


def test_explorer_html_never_leaks_the_real_checkout_absolute_path(tmp_path):
    out = tmp_path / "explorer.html"
    _build_and_render(TRIGGER_DESIGN, TRIGGER_MODULES, out)
    text = out.read_text()
    assert str(REPO_ROOT) not in text


def test_explorer_html_embedded_json_contains_real_expected_counts(tmp_path):
    """A non-synthetic proof the real data reached the file: trigger_demo's
    real node count (including EXTERNAL_PORT/MODULE_GROUP/DOMAIN_GROUP
    nodes) and its 45 real connection edges."""
    out = tmp_path / "explorer.html"
    graph = _build_and_render(TRIGGER_DESIGN, TRIGGER_MODULES, out)
    text = out.read_text()

    match = re.search(
        r'<script type="application/json" id="design-graph-data">(.*?)</script>', text, re.S,
    )
    assert match, "design-graph-data island not found"
    embedded = json.loads(match.group(1))

    assert len(embedded["nodes"]) == len(graph.nodes)
    assert len(embedded["edges"]) == 45
    kinds = {}
    for n in embedded["nodes"]:
        kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1
    assert kinds["instance"] == 10
    assert kinds["module-group"] == 7
    assert kinds["external-port"] >= 1
    assert kinds["domain-group"] >= 1


def test_embedded_json_survives_a_literal_close_script_in_a_diagnostic_message(tmp_path):
    """The specific injection this phase calls out: a project-authored
    string containing a literal `</script>` must not terminate the data
    island early."""
    from forge.ir.model import (
        DiagnosticReference, ResolvedDesign, ResolvedInstance, ResolvedModuleDefinition, ResolvedProject,
    )

    design = ResolvedDesign(
        name="hostile",
        modules=[ResolvedModuleDefinition(name="m", kind="rtl", top="m_top", source_files=["m.v"])],
        instances=[ResolvedInstance(id="m", module="m")],
        diagnostics=[DiagnosticReference(
            severity="warning", message="payload </script><script>alert(1)</script>",
            object_id="module:m",
        )],
    )
    project = ResolvedProject(design=design)
    graph = build_design_graph(project)
    out = tmp_path / "hostile.html"
    render_explorer_html(graph, out)
    text = out.read_text()

    match = re.search(
        r'<script type="application/json" id="design-graph-data">(.*?)</script>', text, re.S,
    )
    assert match
    embedded = json.loads(match.group(1))
    messages = [d["message"] for n in embedded["nodes"] for d in n.get("diagnostics", [])]
    assert any("alert(1)" in m for m in messages), "the real message must still round-trip through JSON"
    # The real danger is a literal, unescaped `</script` sequence inside
    # the JSON payload — that (and only that) is what an HTML parser
    # would treat as closing the surrounding <script> element early. A
    # bare `<script>` *start* tag inside script CDATA is inert text, not
    # a hazard — the island's own real closing tag is exactly one
    # `</script>` occurrence per data island, found here by regex, so any
    # occurrence of `</script` *inside* that captured group already
    # proves it was escaped (a raw one would have ended the regex match
    # early instead).
    assert "</script" not in match.group(1)
    assert "<\\/script><script>alert(1)<\\/script>" in match.group(1)
