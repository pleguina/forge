from __future__ import annotations

import sys
from pathlib import Path


_FW_PYTHON = Path(__file__).resolve().parents[1]
if str(_FW_PYTHON) not in sys.path:
    sys.path.insert(0, str(_FW_PYTHON))

from forge.verify.supported_path_validator import validate_supported_path


def test_supported_path_includes_typed_contract_action(tmp_path: Path) -> None:
    design = tmp_path / "design.verification.yml"
    design.write_text("datasets: []\nflows: []\n")

    result = validate_supported_path(design, consumer_root=tmp_path, check_tools=False)

    assert not result.ok
    assert result.issues
    assert "contract parse error" in result.issues[0]
    assert "Add a non-empty 'plugin' field" in result.issues[0]