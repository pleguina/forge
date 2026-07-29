"""Tests for ci/docs_publish.sh (release-plan Phase 9, slice 9.5).

Builds a small, throwaway scratch git repo (its own commits/tags) so this
test can exercise the real `mike`/git worktree mechanics without touching
this repository's own git state — the same real risk the script itself
guards against for the actual repo (see the script's module docstring).
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_DOCS_PUBLISH_SCRIPT = REPO_ROOT / "ci" / "docs_publish.sh"

skip_without_mike = pytest.mark.skipif(
    importlib.util.find_spec("mike") is None or importlib.util.find_spec("mkdocs") is None,
    reason="mike/mkdocs not installed (optional 'docs' extra — pip install -e 'forge[docs]')",
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _run_docs_publish(repo: Path, output_dir: Path, **kwargs) -> "subprocess.CompletedProcess":
    """`ci/docs_publish.sh` derives REPO_ROOT from its own `BASH_SOURCE`
    location, not the caller's cwd (so it works correctly however CI
    invokes it) — so exercising it against a scratch repo requires
    running *that repo's own copy* of the script (copied in by
    `_make_repo` below), not this repo's real one."""
    return subprocess.run(
        ["bash", str(repo / "ci" / "docs_publish.sh"), "--output-dir", str(output_dir)],
        cwd=repo, capture_output=True, text=True, **kwargs,
    )


def _make_repo(root: Path) -> Path:
    """A minimal, self-contained git repo with a real mkdocs.yml/docs/
    tree (copied from this repo's own, already-passing config) and its
    own copy of the script under test."""
    root.mkdir()
    for name in ("mkdocs.yml", "docs"):
        src = REPO_ROOT / name
        dst = root / name
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    (root / "ci").mkdir()
    shutil.copy2(_REAL_DOCS_PUBLISH_SCRIPT, root / "ci" / "docs_publish.sh")

    _git(root, "init", "-q", "-b", "master")
    _git(root, "config", "user.email", "test@test.com")
    _git(root, "config", "user.name", "test")
    return root


@pytest.fixture
def scratch_repo(tmp_path: Path) -> Path:
    """A scratch repo with two commits: one tagged v1.0.0, and a later
    one (simulating "dev") with a small, detectable content change — so
    tests can prove each deployed version reflects *its own* commit, not
    the current HEAD's content relabeled."""
    repo = _make_repo(tmp_path / "scratch")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "v1 snapshot")
    _git(repo, "tag", "v1.0.0")

    marker_file = repo / "docs" / "index.md"
    with marker_file.open("a") as f:
        f.write("\n<!-- dev-only marker -->\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "post-tag change")

    return repo


@skip_without_mike
def test_deploys_dev_and_latest_tagged_release(scratch_repo: Path, tmp_path: Path) -> None:
    output_dir = tmp_path / "artifact"
    result = _run_docs_publish(scratch_repo, output_dir)
    assert result.returncode == 0, result.stdout + result.stderr

    versions = json.loads((output_dir / "versions.json").read_text())
    by_version = {v["version"]: v for v in versions}
    assert set(by_version) == {"dev", "v1.0.0"}
    assert by_version["v1.0.0"]["aliases"] == ["latest"]
    assert by_version["dev"]["aliases"] == []


@skip_without_mike
def test_each_version_reflects_its_own_commit_content(scratch_repo: Path, tmp_path: Path) -> None:
    """The real regression this design guards against: a naive
    implementation might just relabel the current HEAD's build under
    multiple version names instead of building each version from its own
    historical commit."""
    output_dir = tmp_path / "artifact"
    result = _run_docs_publish(scratch_repo, output_dir)
    assert result.returncode == 0, result.stdout + result.stderr

    dev_html = (output_dir / "dev" / "index.html").read_text()
    tagged_html = (output_dir / "v1.0.0" / "index.html").read_text()
    assert "dev-only marker" in dev_html
    assert "dev-only marker" not in tagged_html


@skip_without_mike
def test_default_version_redirects_to_latest_alias(scratch_repo: Path, tmp_path: Path) -> None:
    output_dir = tmp_path / "artifact"
    result = _run_docs_publish(scratch_repo, output_dir)
    assert result.returncode == 0, result.stdout + result.stderr

    root_index = (output_dir / "index.html").read_text()
    assert "v1.0.0" in root_index or "latest" in root_index


@skip_without_mike
def test_deploys_only_dev_when_no_tag_exists(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "no_tag_repo")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "only commit, no tag")

    output_dir = tmp_path / "artifact"
    result = _run_docs_publish(repo, output_dir)
    assert result.returncode == 0, result.stdout + result.stderr

    versions = json.loads((output_dir / "versions.json").read_text())
    assert [v["version"] for v in versions] == ["dev"]


@skip_without_mike
def test_never_creates_gh_pages_branch_in_the_real_repo(scratch_repo: Path, tmp_path: Path) -> None:
    """Runs against the scratch repo (not REPO_ROOT) by construction, but
    this asserts the invariant explicitly: whatever repo the script is
    invoked in, only *that* repo's git state changes."""
    before = set(_git(REPO_ROOT, "branch", "-a").splitlines())
    output_dir = tmp_path / "artifact"
    result = _run_docs_publish(scratch_repo, output_dir)
    assert result.returncode == 0, result.stdout + result.stderr

    after = set(_git(REPO_ROOT, "branch", "-a").splitlines())
    assert before == after
    assert not any("gh-pages" in b for b in after)


@skip_without_mike
def test_leaves_no_stray_worktrees_in_real_repo(scratch_repo: Path, tmp_path: Path) -> None:
    before = _git(REPO_ROOT, "worktree", "list")
    output_dir = tmp_path / "artifact"
    result = _run_docs_publish(scratch_repo, output_dir)
    assert result.returncode == 0, result.stdout + result.stderr

    after = _git(REPO_ROOT, "worktree", "list")
    assert before == after
