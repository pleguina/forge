"""Tests for the passthrough_demo verification contract.

passthrough_demo is the minimal, generically-named FORGE reference plugin —
it exists to prove the pipeline works end to end with no CMS/OMTF-specific
vocabulary anywhere in it. See plugins/trigger_demo/ for a realistic,
richer reference implementation.
"""
from __future__ import annotations

from pathlib import Path

import sys

_TOOLS = Path(__file__).parent.parent / "tools"
sys.path.insert(0, str(_TOOLS))

import bootstrap as _bootstrap  # noqa: E402
_bootstrap.bootstrap()

from forge.verify.design_contract import load_verify_design  # noqa: E402

_VERIFY_ROOT = Path(__file__).resolve().parents[1]
_DESIGN_YML = _VERIFY_ROOT / "design.verification.yml"


def test_design_contract_loads():
    contract = load_verify_design(_DESIGN_YML)
    assert contract.plugin == "passthrough_demo"


def test_single_flow_declared():
    contract = load_verify_design(_DESIGN_YML)
    assert len(contract.flows) == 1
    assert contract.flows[0].name == "passthrough_xsim"
    assert contract.flows[0].kind == "full_chip_rtl"


def test_golden_dataset_declared():
    contract = load_verify_design(_DESIGN_YML)
    assert len(contract.datasets) == 1
    assert contract.datasets[0].name == "passthrough_demo_golden"
