from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

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


_VALID_CONTRACT_BODY = """\
plugin: demo_plugin
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


def _write_contract(tmp_path: Path, *, schema_version: str | None = None) -> Path:
    design = tmp_path / "design.verification.yml"
    body = _VALID_CONTRACT_BODY
    if schema_version is not None:
        body = f"schema_version: {schema_version!r}\n" + body
    design.write_text(textwrap.dedent(body))
    return design


def test_no_schema_version_loads_fine_and_defaults_to_supported(tmp_path: Path) -> None:
    """Absence is silent — the file loads unchanged, and the resolved
    contract still carries the current supported version."""
    from forge.verify.design_contract import VERIFY_CONTRACT_SCHEMA_VERSION

    contract = load_verify_design(_write_contract(tmp_path))
    assert contract.schema_version == VERIFY_CONTRACT_SCHEMA_VERSION


def test_matching_schema_version_loads_fine(tmp_path: Path) -> None:
    from forge.verify.design_contract import VERIFY_CONTRACT_SCHEMA_VERSION

    contract = load_verify_design(_write_contract(tmp_path, schema_version=VERIFY_CONTRACT_SCHEMA_VERSION))
    assert contract.schema_version == VERIFY_CONTRACT_SCHEMA_VERSION


def test_different_major_schema_version_raises_typed_error(tmp_path: Path) -> None:
    design = _write_contract(tmp_path, schema_version="2.0")

    with pytest.raises(DesignContractError) as exc_info:
        load_verify_design(design)

    assert "incompatible" in str(exc_info.value)


def test_flow_level_clk_period_ns_overrides_defaults(tmp_path: Path) -> None:
    """The primary ap_clk period has a per-flow override — without it,
    every flow would silently share SimulationDefaults.clk_period_ns. A
    flow whose own design's primary domain runs at a genuinely different
    rate than every other flow in the same plugin needs its own
    override."""
    design = tmp_path / "design.verification.yml"
    design.write_text(textwrap.dedent("""\
        plugin: demo_plugin
        datasets:
          demo:
            xml: data/demo.xml
        defaults:
          clk_period_ns: 4.0
        flows:
          - name: demo_flow
            kind: single_module_rtl
            backend: xsim
            top_module: demo_top
            tb_module: tb_demo_top
            dut_rtl_source: build_hls/demo
            dataset: demo
            simulation:
              clk_period_ns: 20.0
              extra_clocks:
                clk_pixel: 5.0
              extra_resets:
                rst_pixel: clk_pixel
        """))
    contract = load_verify_design(design)
    flow = contract.get_flow("demo_flow")
    assert flow.clk_period_ns == 20.0
    assert contract.defaults.clk_period_ns == 4.0  # defaults themselves unaffected
    assert flow.extra_clocks == {"clk_pixel": 5.0}
    assert flow.extra_resets == {"rst_pixel": "clk_pixel"}


def test_no_clk_period_ns_override_leaves_it_none(tmp_path: Path) -> None:
    contract = load_verify_design(_write_contract(tmp_path))
    flow = contract.get_flow("demo_flow")
    assert flow.clk_period_ns is None
    assert flow.extra_clocks == {}
    assert flow.extra_resets == {}
