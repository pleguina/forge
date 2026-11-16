"""A contract without a clock_primary or reset_primary role that does not
declare the module clock- or reset-free gets a warning naming the missing
role, not one that claims the contract declared a port called None."""

from __future__ import annotations

from pathlib import Path

from forge.contracts.config import DesignConfig
from forge.contracts.contract_loader import LoadedContract
from forge.contracts.matcher import auto_match_ports


def _design(tmp_path: Path) -> DesignConfig:
    design_yml = tmp_path / "design.yml"
    design_yml.write_text(
        "part: xcvu13p\n"
        "clock_period: 4.0\n"
        "modules:\n"
        "  - name: demux\n"
        "    top: demux_top\n"
        "    src: []\n"
        "    kind: rtl\n"
        "    instances: 1\n"
    )
    return DesignConfig.load_relaxed(design_yml)


_IP_INFO = {"demux": {"ports": [
    {"name": "d_in", "direction": "IN", "width": 8, "type": "wire"},
    {"name": "d_out", "direction": "OUT", "width": 8, "type": "wire"},
]}}

_ROLES = {
    "input_stream_0": {"raw_port": "d_in", "direction": "input", "width": 8},
    "output_stream_0": {"raw_port": "d_out", "direction": "output", "width": 8},
}


def _contract(tmp_path: Path, **flags) -> LoadedContract:
    spec = {"ip_interface": {"module_name": "demux", "roles": dict(_ROLES), **flags}}
    return LoadedContract(tmp_path / "demux.interface.yaml", spec)


def test_missing_primary_roles_are_named(tmp_path):
    _, _, report = auto_match_ports(
        _design(tmp_path), _IP_INFO, contracts={"demux": _contract(tmp_path)})

    assert any("[demux] contract has no clock_primary role" in w for w in report.warnings)
    assert any("[demux] contract has no reset_primary role" in w for w in report.warnings)
    assert not any("=None" in w for w in report.warnings)


def test_combinatorial_contract_has_no_primary_role_warning(tmp_path):
    _, global_nets, report = auto_match_ports(
        _design(tmp_path), _IP_INFO,
        contracts={"demux": _contract(tmp_path, combinatorial=True)})

    assert not any("primary" in w for w in report.warnings)
    assert global_nets == {}
