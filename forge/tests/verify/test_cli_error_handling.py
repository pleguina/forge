from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _run_forge_verify(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "forge.verification", *args],
        capture_output=True,
        text=True,
    )


def test_generate_missing_design_is_guided_without_traceback(tmp_path: Path) -> None:
    missing = tmp_path / "missing.design.verification.yml"

    result = _run_forge_verify("generate", str(missing))

    assert result.returncode == 1
    assert "ERROR (design): design file not found" in result.stderr
    assert "Check the design.verification.yml path and re-run forge verify generate." in result.stderr
    assert "Traceback" not in result.stderr
