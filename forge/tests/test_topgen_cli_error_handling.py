from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _run_topgen(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "forge.core.cli.main", "topgen", *args],
        capture_output=True,
        text=True,
    )


def test_clean_missing_design_is_guided_without_traceback(tmp_path: Path) -> None:
    missing = tmp_path / "missing.design.yml"

    result = _run_topgen("clean", str(missing))

    assert result.returncode == 1
    assert f"❌ Design file not found: {missing.resolve()}" in result.stderr
    assert "Check the design.yml path and re-run forge topgen clean." in result.stderr
    assert "Traceback" not in result.stderr
