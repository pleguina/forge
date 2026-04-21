from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


_FW_PYTHON = Path(__file__).resolve().parents[1]
if str(_FW_PYTHON) not in sys.path:
    sys.path.insert(0, str(_FW_PYTHON))


def _run_fw_verify(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_FW_PYTHON) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "fw_verify", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_generate_missing_design_is_guided_without_traceback(tmp_path: Path) -> None:
    missing = tmp_path / "missing.design.verification.yml"

    result = _run_fw_verify("generate", str(missing))

    assert result.returncode == 1
    assert "ERROR (design): design file not found" in result.stderr
    assert "Check the design.verification.yml path and re-run fw_verify generate." in result.stderr
    assert "Traceback" not in result.stderr
