from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest


_FW_PYTHON = Path(__file__).resolve().parents[1]
if str(_FW_PYTHON) not in sys.path:
    sys.path.insert(0, str(_FW_PYTHON))

from forge.verify.exceptions import FlowConfigError, MissingArtifactError
from forge.verify.flow_loader import load_generic_flow


def test_load_generic_flow_missing_file_raises_typed_error(tmp_path: Path) -> None:
    missing = tmp_path / "verify.flow.yml"

    with pytest.raises(MissingArtifactError) as exc_info:
        load_generic_flow(missing, consumer_root=tmp_path)

    assert "verify.flow.yml not found" in str(exc_info.value)
    assert "re-run fw_verify generate" in exc_info.value.action


def test_load_generic_flow_missing_required_field_raises_typed_error(tmp_path: Path) -> None:
    flow = tmp_path / "verify.flow.yml"
    flow.write_text(
        textwrap.dedent(
            """\
            flow:
              name: demo
              kind: full_chip_rtl
              backend: xsim
              top_module: algo_top

            dut:
              rtl: out/algo_top.v

            dataset:
              xml: data/events.xml

            simulation:
              clk_period_ns: 2.78
              reset_cycles: 8
              idle_cycles_after_reset: 8
              post_stimulus_drain_cycles: 160
            """
        )
    )

    with pytest.raises(FlowConfigError) as exc_info:
        load_generic_flow(flow, consumer_root=tmp_path)

    assert "missing required fields" in str(exc_info.value)
    assert "Add the required fields" in exc_info.value.action
