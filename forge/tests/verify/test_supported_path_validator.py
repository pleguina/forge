from __future__ import annotations

from pathlib import Path

from forge.verify.supported_path_validator import validate_supported_path


def test_supported_path_includes_typed_contract_action(tmp_path: Path) -> None:
    design = tmp_path / "design.verification.yml"
    design.write_text("datasets: []\nflows: []\n")

    result = validate_supported_path(design, consumer_root=tmp_path, check_tools=False)

    assert not result.ok
    assert result.issues
    assert "contract parse error" in result.issues[0]
    assert "Add a non-empty 'plugin' field" in result.issues[0]