from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


_TOPGEN_ROOT = Path(__file__).resolve().parents[1]


def _run_topgen(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_TOPGEN_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "topgen.cli.main", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_clean_missing_design_is_guided_without_traceback(tmp_path: Path) -> None:
    missing = tmp_path / "missing.design.yml"

    result = _run_topgen("clean", str(missing))

    assert result.returncode == 1
    assert f"❌ Design file not found: {missing.resolve()}" in result.stderr
    assert "Check the design.yml path and re-run topgen clean." in result.stderr
    assert "Traceback" not in result.stderr