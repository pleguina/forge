"""Mechanical MkDocs pipeline tests (release-plan Phase 9, slice 9.0).

Proves the build/exclude/strict-mode pipeline actually works: a real
``mkdocs build --strict`` run against the repo's ``mkdocs.yml`` succeeds,
and the internal-only content (``docs/plan/**``, ``docs/internal/**``,
``docs/development/release-readiness.md``) that ``exclude_docs:`` is
supposed to keep out of the public site is truly absent from the built
``site/`` output — not just missing from ``nav:``, which would not
actually stop MkDocs from rendering it (release-plan Phase 9, "Defect 1").
"""
from __future__ import annotations

import importlib.util
import shutil
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


def _build_site(tmp_path: Path) -> Path:
    site_dir = tmp_path / "site"
    subprocess.run(
        [
            sys.executable, "-m", "mkdocs", "build", "--strict",
            "--config-file", str(MKDOCS_CONFIG),
            "--site-dir", str(site_dir),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return site_dir


@skip_without_mkdocs
def test_strict_build_succeeds(tmp_path: Path) -> None:
    site_dir = _build_site(tmp_path)
    assert (site_dir / "index.html").is_file()


@skip_without_mkdocs
def test_internal_plan_directory_is_excluded(tmp_path: Path) -> None:
    site_dir = _build_site(tmp_path)
    assert not (site_dir / "plan").exists()
    matches = list(site_dir.rglob("FORGE_release_plan*"))
    assert matches == [], f"internal plan content leaked into built site: {matches}"


@skip_without_mkdocs
def test_release_readiness_audit_trail_is_excluded(tmp_path: Path) -> None:
    site_dir = _build_site(tmp_path)
    matches = list(site_dir.rglob("*release-readiness*"))
    assert matches == [], f"internal audit trail leaked into built site: {matches}"


@skip_without_mkdocs
def test_internal_directory_is_excluded(tmp_path: Path) -> None:
    """``docs/internal/`` holds tracked-in-git release-gating planning
    material (e.g. Phase 10's preflight/spec docs) that still isn't
    public-site content — same exclusion contract as ``docs/plan/``."""
    site_dir = _build_site(tmp_path)
    assert not (site_dir / "internal").exists()
    matches = list(site_dir.rglob("*vision_pipeline*")) + list(site_dir.rglob("preflight*"))
    assert matches == [], f"internal Phase 10 content leaked into built site: {matches}"


@skip_without_mkdocs
def test_docs_source_files_remain_untouched(tmp_path: Path) -> None:
    """exclude_docs keeps excluded content out of the *build*, not off disk."""
    _build_site(tmp_path)
    assert (REPO_ROOT / "docs/plan/FORGE_release_plan.md").is_file()
    assert (REPO_ROOT / "docs/development/release-readiness.md").is_file()
    assert (REPO_ROOT / "docs/internal/phase10/preflight.md").is_file()
    assert (REPO_ROOT / "docs/internal/phase10/vision_pipeline_reference_project.md").is_file()


@pytest.fixture(autouse=True)
def _cleanup_default_site_dir():
    yield
    shutil.rmtree(REPO_ROOT / "site", ignore_errors=True)
