"""Tests for forge.analysis.design_explorer.dot_renderer — DOT text
generation, real SVG rendering, and the --dot/--svg CLI split's
honest-failure behavior.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from forge.analysis.design_explorer.dot_renderer import dot_available, render_dot, render_svg
from forge.analysis.design_explorer.graph_model import build_design_graph
from forge.core.cli.main import build_parser
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

DOT_ON_PATH = shutil.which("dot") is not None
skip_without_dot = pytest.mark.skipif(not DOT_ON_PATH, reason="Graphviz `dot` binary not on PATH")


def _build(design: Path, modules: Path) -> ResolvedProject:
    project, _cfg, _mr = build_project_ir_with_match_report(design, contracts_from=modules)
    return project


@pytest.mark.parametrize("design,modules", [
    (PASSTHROUGH_DESIGN, PASSTHROUGH_MODULES),
    (TRIGGER_DESIGN, TRIGGER_MODULES),
])
def test_dot_text_contains_every_real_connection_id_exactly_once(design, modules):
    project = _build(design, modules)
    graph = build_design_graph(project, source_roots=[REPO_ROOT])
    dot_text = render_dot(graph)

    for conn in project.design.connections:
        assert dot_text.count(conn.id) == 1, f"{conn.id} missing or duplicated in rendered DOT text"


def test_dot_text_never_leaks_the_real_checkout_absolute_path():
    project = _build(TRIGGER_DESIGN, TRIGGER_MODULES)
    graph = build_design_graph(project, source_roots=[REPO_ROOT])
    dot_text = render_dot(graph)
    assert str(REPO_ROOT) not in dot_text


@skip_without_dot
def test_render_svg_produces_a_real_well_formed_svg(tmp_path):
    project = _build(PASSTHROUGH_DESIGN, PASSTHROUGH_MODULES)
    graph = build_design_graph(project, source_roots=[REPO_ROOT])
    dot_text = render_dot(graph)
    out = tmp_path / "design.svg"
    assert render_svg(dot_text, out) is True
    content = out.read_text()
    assert content.startswith("<?xml") or "<svg" in content[:200]
    assert "</svg>" in content


def test_dot_available_reflects_path():
    assert dot_available() == DOT_ON_PATH


# ── Escaping / injection ─────────────────────────────────────────────────

_HOSTILE_NAMES = [
    'weird"name',
    "back\\slash",
    "arrow->pin",
    "colon:name",
    "dot.name",
    "angle<brack>et",
    "amp&ersand",
]


def _project_with_module_name(name: str) -> ResolvedProject:
    """A minimal, hand-built ResolvedProject exercising one hostile module
    name through the full node/label/cluster pipeline — the direct way to
    prove the renderer's escaping holds for adversarial-but-plausible
    project-authored text, independent of what either real fixture
    happens to contain today."""
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
        diagnostics=[DiagnosticReference(
            severity="warning", message=f'evidence for "{name}" <bad> & </script>',
            object_id=f"module:{name}",
        )],
    )
    return ResolvedProject(design=design)


@pytest.mark.parametrize("hostile", _HOSTILE_NAMES)
def test_dot_rendering_stays_syntactically_valid_for_adversarial_names(hostile, tmp_path):
    project = _project_with_module_name(hostile)
    graph = build_design_graph(project)
    dot_text = render_dot(graph)

    # Never breaks out of its quoted-string label context: every quote
    # character introduced by the hostile name must be escaped.
    assert '"' not in hostile or '\\"' in dot_text

    if DOT_ON_PATH:
        result = subprocess.run(["dot", "-Tsvg"], input=dot_text, capture_output=True, text=True)
        assert result.returncode == 0, f"dot rejected rendered DOT text for {hostile!r}:\n{result.stderr}"


# ── CLI: --dot never requires `dot`; --svg fails loudly without it ──────

def _run_cli(capsys, *args):
    parser = build_parser()
    code = 0
    try:
        parsed = parser.parse_args(list(args))
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_inspect_dot_never_requires_the_dot_binary(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # scrub PATH — no `dot` resolvable
    out_dot = tmp_path / "out.dot"
    code, stdout, stderr = _run_cli(
        capsys, "inspect", str(PASSTHROUGH_DESIGN),
        "--contracts-from", str(PASSTHROUGH_MODULES),
        "--dot", str(out_dot),
    )
    assert code == 0, stdout + stderr
    assert out_dot.exists() and out_dot.stat().st_size > 0


def test_inspect_svg_fails_loudly_and_specifically_when_dot_is_absent(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # scrub PATH — same convention as test_toolchain_versions.py
    out_svg = tmp_path / "out.svg"
    code, stdout, stderr = _run_cli(
        capsys, "inspect", str(PASSTHROUGH_DESIGN),
        "--contracts-from", str(PASSTHROUGH_MODULES),
        "--svg", str(out_svg), "--json",
    )
    assert code != 0
    assert not out_svg.exists()
    payload = json.loads(stdout)
    assert payload["status"] == "fail"
    assert any("dot" in d["message"].lower() for d in payload["diagnostics"])


@skip_without_dot
def test_inspect_svg_succeeds_when_dot_is_on_path(capsys, tmp_path):
    out_svg = tmp_path / "out.svg"
    code, stdout, stderr = _run_cli(
        capsys, "inspect", str(PASSTHROUGH_DESIGN),
        "--contracts-from", str(PASSTHROUGH_MODULES),
        "--svg", str(out_svg),
    )
    assert code == 0, stdout + stderr
    assert out_svg.exists() and out_svg.stat().st_size > 0
