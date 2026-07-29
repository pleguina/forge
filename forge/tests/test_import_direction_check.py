"""
Runs ci/import_direction_check.sh (release-plan §19 revised: a cheap CI
import-linter instead of the premature 5-package split) as a real
subprocess, so the dependency-direction guard has test coverage
independent of the GitLab CI job that also runs it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "ci" / "import_direction_check.sh"


def test_import_direction_check_passes_on_current_tree():
    result = subprocess.run(
        ["bash", str(SCRIPT)], cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No dependency-direction violations found" in result.stdout


def test_import_direction_check_detects_a_real_violation(tmp_path):
    """Sanity-check the linter itself isn't vacuously passing. The script
    self-locates its repo root from its own path (BASH_SOURCE), so this
    copies the script into a scratch tree alongside a planted violation
    rather than relying on `cwd` (which the script ignores)."""
    scratch_script = tmp_path / "ci" / "import_direction_check.sh"
    scratch_script.parent.mkdir(parents=True)
    scratch_script.write_text(SCRIPT.read_text())

    scratch_ir = tmp_path / "forge" / "ir"
    scratch_ir.mkdir(parents=True)
    (scratch_ir / "bad.py").write_text(
        "from ..core.cli.groups import inspect\n"
    )

    result = subprocess.run(
        ["bash", str(scratch_script)], capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "core.cli" in result.stdout
