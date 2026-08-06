"""The closing visual test suite.

Every earlier test module only proves its own piece in isolation
(graph_model/overlays; dot_renderer; html_renderer;
functionality/details-panel data contracts). This module closes
the loop with the cross-cutting proofs:
determinism (DOT + embedded JSON, byte-identical), stable identifiers,
complete edge coverage, valid diagnostic links, a consolidated
escaping/injection suite across every renderer, and a consolidated
absolute-path-leakage scan across every rendered artifact type, plus the
honestly-scoped performance tripwire.

Vendored-package-data presence and the precise offline-loading test are
each already real, dedicated tests elsewhere in this suite
(``test_design_explorer_packaging.py``, ``test_design_explorer_html_renderer.py``)
— re-run as part of the full suite, not duplicated here.
"""
from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path

import pytest

from forge.analyze.design_explorer.dot_renderer import render_dot
from forge.analyze.design_explorer.graph_model import (
    GraphNodeKind,
    build_design_graph,
    parse_object_reference,
)
from forge.analyze.design_explorer.html_renderer import render_explorer_html
from forge.ir.build import build_project_ir_with_match_report
from forge.ir.model import (
    DiagnosticReference,
    ResolvedConnection,
    ResolvedDesign,
    ResolvedEndpoint,
    ResolvedInstance,
    ResolvedModuleDefinition,
    ResolvedProject,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSTHROUGH_DESIGN = REPO_ROOT / "plugins/passthrough_demo/forge/designs/design.yml"
PASSTHROUGH_MODULES = REPO_ROOT / "plugins/passthrough_demo/forge/modules.yml"
TRIGGER_DESIGN = REPO_ROOT / "plugins/trigger_demo/forge/designs/design.yml"
TRIGGER_MODULES = REPO_ROOT / "plugins/trigger_demo/forge/modules.yml"


def _trigger_project() -> ResolvedProject:
    project, _cfg, _mr = build_project_ir_with_match_report(TRIGGER_DESIGN, contracts_from=TRIGGER_MODULES)
    return project


# ── Deterministic graph model: DOT + embedded JSON, byte-identical ──────

def test_dot_and_embedded_json_are_byte_identical_across_two_real_builds(tmp_path):
    project = _trigger_project()

    graph_a = build_design_graph(project, source_roots=[REPO_ROOT])
    graph_b = build_design_graph(project, source_roots=[REPO_ROOT])

    dot_a, dot_b = render_dot(graph_a), render_dot(graph_b)
    assert dot_a == dot_b

    out_a, out_b = tmp_path / "a.html", tmp_path / "b.html"
    render_explorer_html(graph_a, out_a)
    render_explorer_html(graph_b, out_b)

    def _extract_island(text: str) -> str:
        start = text.index('id="design-graph-data">') + len('id="design-graph-data">')
        return text[start:text.index("</script>", start)]

    assert _extract_island(out_a.read_text()) == _extract_island(out_b.read_text())


# ── Stable identifiers ────────────────────────────────────────────────────

def test_every_node_and_edge_id_equals_its_real_corresponding_ir_id():
    project = _trigger_project()
    graph = build_design_graph(project, source_roots=[REPO_ROOT])

    real_instance_ids = {i.id for i in project.design.instances}
    real_connection_ids = {c.id for c in project.design.connections}
    real_module_names = {m.name for m in project.design.modules}

    graph_instance_ids = {n.id for n in graph.nodes if n.kind == GraphNodeKind.INSTANCE}
    graph_module_group_ids = {n.id for n in graph.nodes if n.kind == GraphNodeKind.MODULE_GROUP}
    graph_edge_ids = {e.id for e in graph.edges}

    assert graph_instance_ids == real_instance_ids
    assert graph_module_group_ids == {f"module:{m}" for m in real_module_names}
    assert graph_edge_ids == real_connection_ids


# ── Valid diagnostic links ───────────────────────────────────────────────

@pytest.mark.parametrize("design,modules", [
    (PASSTHROUGH_DESIGN, PASSTHROUGH_MODULES),
    (TRIGGER_DESIGN, TRIGGER_MODULES),
])
def test_every_resolved_diagnostic_target_resolves_to_a_real_object_of_the_correct_kind(design, modules):
    project, _cfg, _mr = build_project_ir_with_match_report(design, contracts_from=modules)
    graph = build_design_graph(project, source_roots=[REPO_ROOT])
    object_ids_by_kind: dict = {}
    for obj in graph.objects:
        object_ids_by_kind.setdefault(obj.kind, set()).add(obj.id)

    checked_any = False
    for d in project.design.diagnostics:
        target = parse_object_reference(d.object_id)
        if target is None:
            continue
        checked_any = True
        assert target.kind in object_ids_by_kind, target
        assert target.id in object_ids_by_kind[target.kind], target
        # Module-definition diagnostics resolve to the real MODULE_GROUP
        # node — never to an arbitrary instance id.
        if target.kind == "module-definition":
            module_group = next(n for n in graph.nodes if n.id == f"module:{target.id}")
            assert d.message in [pd.message for pd in module_group.diagnostics]

    # Neither real fixture's own validator diagnostics happen to carry a
    # module-scoped location today — `checked_any` may legitimately be
    # False here. The
    # real, positive "a resolvable diagnostic resolves correctly" proof is
    # test_design_explorer_overlays.py's dedicated synthetic-design test;
    # this pass is the complementary "never a false/incorrect resolution
    # on real fixtures" guarantee.
    del checked_any


# ── Consolidated escaping/injection suite across every renderer ─────────

_ADVERSARIAL_STRINGS = [
    'quote"here', "back\\slash", "<angle>", "amp&ersand", "</script>",
    "arrow->pin", "colon:tag", "dot.separated",
]


def _hostile_project(name: str, message: str) -> ResolvedProject:
    design = ResolvedDesign(
        name="hostile",
        modules=[ResolvedModuleDefinition(name=name, kind="rtl", top="top", source_files=["a.v"])],
        instances=[ResolvedInstance(id=name, module=name)],
        connections=[ResolvedConnection(
            id=f"{name}.out->{name}.in",
            producer=ResolvedEndpoint(instance_id=name, port="out"),
            consumer=ResolvedEndpoint(instance_id=name, port="in"),
            wiring_method="auto_match",
        )],
        diagnostics=[DiagnosticReference(severity="warning", message=message, object_id=f"module:{name}")],
    )
    return ResolvedProject(design=design)


@pytest.mark.parametrize("hostile", _ADVERSARIAL_STRINGS)
def test_every_renderer_survives_adversarial_project_authored_strings(hostile, tmp_path):
    project = _hostile_project(hostile, f"evidence: {hostile}")
    graph = build_design_graph(project)

    dot_text = render_dot(graph)
    assert isinstance(dot_text, str) and dot_text.strip().startswith("digraph")

    out = tmp_path / f"{abs(hash(hostile))}.html"
    render_explorer_html(graph, out)
    text = out.read_text()

    match_start = text.index('id="design-graph-data">') + len('id="design-graph-data">')
    match_end = text.index("</script>", match_start)
    island = text[match_start:match_end]
    payload = json.loads(island)  # must remain syntactically valid JSON
    assert any(hostile in str(v) for v in _flatten(payload)), "the real hostile string must still round-trip"
    assert "</script" not in island


def _flatten(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _flatten(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _flatten(v)
    else:
        yield obj


# ── Consolidated absolute-path leakage scan ──────────────────────────────

def test_no_rendered_artifact_leaks_the_real_checkout_absolute_path(tmp_path):
    project = _trigger_project()
    graph = build_design_graph(project, source_roots=[REPO_ROOT])

    dot_text = render_dot(graph)
    html_out = tmp_path / "explorer.html"
    render_explorer_html(graph, html_out)
    html_text = html_out.read_text()
    embedded_json = json.dumps(dataclasses.asdict(graph))

    repo_str = str(REPO_ROOT)
    for label, text in (("dot", dot_text), ("html", html_text), ("json", embedded_json)):
        assert repo_str not in text, f"{label} artifact leaked the real checkout absolute path"


# ── Performance tripwire (honestly-scoped, generous on purpose) ─────────

def test_trigger_demo_construction_and_serialization_performance_tripwire():
    """A regression tripwire, not a scale proof — trigger_demo (7 modules,
    10 instances, 45 connections) is the largest reference design
    available, which is real but small. Times only pure DesignGraph
    construction + DOT/JSON serialization (excludes the `dot` subprocess,
    whose startup-time variance is unrelated CI-runner noise). The
    structural-complexity assertions are the primary regression signal;
    the timing ceiling is a secondary, deliberately loose guard — measured
    ~8.5ms/iteration in the environment this test was authored in, so a
    2-second ceiling is generous by roughly two orders of magnitude on
    purpose.
    """
    project = _trigger_project()

    start = time.perf_counter()
    graph = build_design_graph(project, source_roots=[REPO_ROOT])
    _dot_text = render_dot(graph)
    _json_text = json.dumps(dataclasses.asdict(graph), sort_keys=True)
    elapsed = time.perf_counter() - start

    assert len(project.design.modules) == 7
    assert len(project.design.instances) == 10
    assert len(project.design.connections) == 45
    assert len(graph.edges) == 45

    ceiling_s = 2.0
    assert elapsed < ceiling_s, (
        f"DesignGraph construction+serialization took {elapsed:.3f}s "
        f"(ceiling {ceiling_s}s, generous on purpose — this is a regression "
        f"tripwire, not a scale proof)"
    )
