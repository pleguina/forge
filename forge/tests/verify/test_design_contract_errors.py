from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest


_FW_PYTHON = Path(__file__).resolve().parents[1]
if str(_FW_PYTHON) not in sys.path:
    sys.path.insert(0, str(_FW_PYTHON))

from forge.verify.design_contract import load_verify_design
from forge.verify.exceptions import DesignContractError


def test_load_verify_design_missing_file_raises_typed_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.design.verification.yml"

    with pytest.raises(DesignContractError) as exc_info:
        load_verify_design(missing)

    assert "design.verification.yml not found" in str(exc_info.value)
    assert "Check the contract path" in exc_info.value.action
    assert exc_info.value.context["path"] == str(missing.resolve())


def test_load_verify_design_missing_plugin_raises_typed_error(tmp_path: Path) -> None:
    design = tmp_path / "design.verification.yml"
    design.write_text(
        textwrap.dedent(
            """\
            datasets:
              demo:
                xml: data/demo.xml
            flows:
              - name: demo_flow
                kind: single_module_rtl
                backend: xsim
                top_module: demo_top
                tb_module: tb_demo_top
                dut_rtl_source: build_hls/demo
                dataset: demo
            """
        )
    )

    with pytest.raises(DesignContractError) as exc_info:
        load_verify_design(design)

    assert "'plugin' is required" in str(exc_info.value)
    assert "Add a non-empty 'plugin' field" in exc_info.value.action
