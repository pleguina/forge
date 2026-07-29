"""A real, install-and-import proof that the vendored Cytoscape.js asset
(release-plan Phase 8, slice 8.2) round-trips through an actual `pip
install` of the built package — not just "the file exists in the working
tree". Matches this codebase's existing discipline of proving packaging
claims with a real install rather than a source-tree-only check.

Marked `integration` (deselected by `-m "not integration"`, same
convention as `tests/verify/test_phase7_integration.py`) since it builds
a real wheel and creates a real, isolated venv — slower than a unit test,
but a real proof, not a shortcut.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
FORGE_DIR = REPO_ROOT / "forge"


def test_vendored_cytoscape_js_survives_a_real_pip_install(tmp_path: Path) -> None:
    # Build from an isolated copy, not FORGE_DIR itself — `pip wheel`
    # against a local source directory builds in-tree (a real `build/` +
    # `*.egg-info` side effect of this project's setup.py-era layout), and
    # a packaging test should not mutate the real source tree it's
    # verifying.
    source_copy = tmp_path / "forge_src"
    shutil.copytree(
        FORGE_DIR, source_copy,
        ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", "build", "htmlcov", ".pytest_cache"),
    )

    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    build = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(source_copy), "-w", str(wheel_dir), "--no-deps", "-q"],
        capture_output=True, text=True,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheels = list(wheel_dir.glob("forge-*.whl"))
    assert len(wheels) == 1, wheels

    venv_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(venv_dir)
    venv_python = venv_dir / "bin" / "python"

    install = subprocess.run(
        [str(venv_python), "-m", "pip", "install", "-q", str(wheels[0])],
        capture_output=True, text=True,
    )
    assert install.returncode == 0, install.stdout + install.stderr

    probe = subprocess.run(
        [str(venv_python), "-c", (
            "import importlib.resources, hashlib\n"
            "data = importlib.resources.files('forge.analyze.design_explorer')"
            ".joinpath('vendor', 'cytoscape.min.js').read_bytes()\n"
            "print(len(data))\n"
            "print(hashlib.sha256(data).hexdigest())\n"
        )],
        capture_output=True, text=True,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    lines = probe.stdout.strip().splitlines()
    assert int(lines[0]) > 100_000, "installed vendored cytoscape.min.js looks truncated"
    real_sha256 = (FORGE_DIR / "analyze/design_explorer/vendor/cytoscape.min.js").read_bytes()
    import hashlib
    assert lines[1] == hashlib.sha256(real_sha256).hexdigest()

    # The renderer itself must also work end-to-end from the installed
    # artifact, not just a raw resource read.
    render_probe = subprocess.run(
        [str(venv_python), "-c", (
            "from forge.analyze.design_explorer.html_renderer import render_explorer_html\n"
            "from forge.analyze.design_explorer.graph_model import DesignGraph\n"
            "from forge.core.artifact_schema import ArtifactSchema\n"
            "import tempfile, pathlib\n"
            "graph = DesignGraph(schema=ArtifactSchema('forge.design_graph', '1.0'), "
            "source_ir_schema_version='0.2.0', source_ir_content_hash='deadbeef', "
            "overlay_hashes={}, nodes=(), edges=(), objects=())\n"
            "out = pathlib.Path(tempfile.mkdtemp()) / 'x.html'\n"
            "render_explorer_html(graph, out)\n"
            "print(out.stat().st_size)\n"
        )],
        capture_output=True, text=True,
    )
    assert render_probe.returncode == 0, render_probe.stdout + render_probe.stderr
    assert int(render_probe.stdout.strip()) > 100_000
