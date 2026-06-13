"""Tests for:
  - omtf_tools.framework_validator (test 13 & 14)
  - omtf_tools.gmt_compatibility   (test 15)

The tested modules live in the omtf_firmware plugin, not in ARC core.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Ensure the plugin package is importable
# ---------------------------------------------------------------------------
_PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "plugins" / "omtf_firmware"
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from arc.framework.importer import load
from omtf_tools.framework_validator import (
    validate_mapping_against_framework, FrameworkCheckResult,
)
from omtf_tools.gmt_compatibility import (
    check_gmt_compatibility,
    VERDICT_OK, VERDICT_NEEDS_PACKER, VERDICT_NEEDS_MORE_TX_LINKS,
    VERDICT_NEEDS_MAPPING_UPDATE, VERDICT_UNKNOWN,
)

# ---------------------------------------------------------------------------
# Shared ABI / endpoint fixtures
# ---------------------------------------------------------------------------

_ABI = {
    "generator": "test", "generated_at": "2025-01-01T00:00:00+00:00",
    "project": "test_project", "board": "X2O_VU13P", "module": "payload",
    "ports": [
        {"name": "logic_clk", "direction": "input", "width": 1, "kind": "clock_logic"},
        # SLR1 GT5 RX (4 lanes)
        {"name": "slr1_gt_5_rx_tdata",  "direction": "input",  "width": 256,
         "kind": "gt_rx_tdata",  "slr": 1, "gt_site": 5, "lanes": 4, "lane_width": 64},
        {"name": "slr1_gt_5_rx_tvalid", "direction": "input",  "width": 4,
         "kind": "gt_rx_tvalid", "slr": 1, "gt_site": 5, "lanes": 4},
        # SLR1 GT6 TX (4 lanes)
        {"name": "slr1_gt_6_tx_tdata",  "direction": "output", "width": 256,
         "kind": "gt_tx_tdata",  "slr": 1, "gt_site": 6, "lanes": 4, "lane_width": 64},
        {"name": "slr1_gt_6_tx_tvalid", "direction": "output", "width": 4,
         "kind": "gt_tx_tvalid", "slr": 1, "gt_site": 6, "lanes": 4},
        {"name": "slr1_gt_5_algo_clk",  "direction": "output", "width": 1,
         "kind": "gt_algo_clk",  "slr": 1, "gt_site": 5},
        {"name": "slr1_gt_6_algo_clk",  "direction": "output", "width": 1,
         "kind": "gt_algo_clk",  "slr": 1, "gt_site": 6},
    ],
}

_EPS = {
    "generator": "test", "board": "X2O_VU13P",
    "endpoints": [
        {"endpoint_id": "rx_slr1_gt5_lane0", "direction": "rx", "slr": 1, "gt_site": 5,
         "lane": 0, "lane_width": 64, "protocol": "csp_64b", "quad_type": "gty25",
         "cage": 12, "fiber": 1, "polarity": 0, "link_function": "CSC",
         "logical_id": 17, "logical_name": "csc_rx_17",
         "endpoint_role": "detector_input_csc", "include_in_framework": True},
        {"endpoint_id": "tx_slr1_gt6_lane0", "direction": "tx", "slr": 1, "gt_site": 6,
         "lane": 0, "lane_width": 64, "protocol": "csp_64b", "quad_type": "gty25",
         "cage": 3, "fiber": 1, "polarity": 0, "link_function": "GMT",
         "logical_id": 0, "logical_name": "gmt_tx_0",
         "endpoint_role": "trigger_output_gmt", "include_in_framework": True},
    ],
}


@pytest.fixture()
def fw(tmp_path):
    abi_p = tmp_path / "abi.json"
    ep_p  = tmp_path / "ep.json"
    abi_p.write_text(json.dumps(_ABI))
    ep_p.write_text(json.dumps(_EPS))
    return load("blobfish", abi_p, ep_p)


# ---------------------------------------------------------------------------
# Helpers for mapping JSON
# ---------------------------------------------------------------------------

def _make_mapping(endpoints: list, tmp_path: Path) -> Path:
    data = {
        "generator": "test",
        "normalized_at": "2025-01-01T00:00:00+00:00",
        "source_csv": "test.csv",
        "statistics": {},
        "warnings": [],
        "endpoints": endpoints,
    }
    p = tmp_path / "mapping.json"
    p.write_text(json.dumps(data))
    return p


def _ep(ep_id, direction, slr, gt_site, lane, link_function="CSC",
        endpoint_role="detector_input_csc", include=True) -> dict:
    return {
        "endpoint_id": ep_id, "direction": direction, "slr": slr,
        "gt_site": gt_site, "lane": lane, "link_function": link_function,
        "endpoint_role": endpoint_role, "include_in_framework": include,
        "has_config": True, "logical_id": 0,
    }


# ---------------------------------------------------------------------------
# Test 13: mapping validation fails if endpoint not in framework import
# ---------------------------------------------------------------------------

class TestFrameworkValidation:
    def test_valid_rx_passes(self, fw, tmp_path):
        mapping = _make_mapping([
            _ep("rx_slr1_gt5_lane0", "rx", 1, 5, 0),
        ], tmp_path)
        result = validate_mapping_against_framework(mapping, fw)
        assert result.ok

    def test_missing_abi_site_fails(self, fw, tmp_path):
        """Test 13: endpoint not in framework import → failure."""
        mapping = _make_mapping([
            _ep("rx_slr1_gt99_lane0", "rx", 1, 99, 0),   # gt_site 99 not in ABI
        ], tmp_path)
        result = validate_mapping_against_framework(mapping, fw)
        assert not result.ok
        assert any("no ABI GT group" in f for f in result.failures)

    def test_lane_out_of_range_fails(self, fw, tmp_path):
        mapping = _make_mapping([
            _ep("rx_slr1_gt5_lane9", "rx", 1, 5, 9),   # lane 9, ABI has 4
        ], tmp_path)
        result = validate_mapping_against_framework(mapping, fw)
        assert not result.ok
        assert any("out of range" in f for f in result.failures)

    def test_tx_endpoint_valid(self, fw, tmp_path):
        mapping = _make_mapping([
            _ep("tx_slr1_gt6_lane0", "tx", 1, 6, 0,
                link_function="GMT", endpoint_role="trigger_output_gmt"),
        ], tmp_path)
        result = validate_mapping_against_framework(mapping, fw)
        assert result.ok

    def test_rx_on_tx_abi_fails(self, fw, tmp_path):
        """Test 14: RX endpoint points to TX-only ABI."""
        # gt_site 6 has only TX in our test ABI
        mapping = _make_mapping([
            _ep("rx_slr1_gt6_lane0", "rx", 1, 6, 0),  # rx but gt_site 6 is TX only
        ], tmp_path)
        result = validate_mapping_against_framework(mapping, fw)
        assert not result.ok

    def test_non_selected_skipped(self, fw, tmp_path):
        mapping = _make_mapping([
            _ep("rx_slr1_gt99_lane0", "rx", 1, 99, 0, include=False),  # not selected
        ], tmp_path)
        result = validate_mapping_against_framework(mapping, fw)
        assert result.ok  # non-selected are not validated

    def test_missing_mapping_file(self, fw, tmp_path):
        with pytest.raises(FileNotFoundError):
            validate_mapping_against_framework(tmp_path / "nonexistent.json", fw)


# ---------------------------------------------------------------------------
# Test 15: GMT output compatibility report
# ---------------------------------------------------------------------------

_DESIGN_OK = {
    "modules": [
        {"name": "out_csp_best_constr",   "ref": "csp_pack_bx_sync",
         "external_out_ports": ["csp_out", "frame_cnt"]},
        {"name": "out_csp_best_unconstr", "ref": "csp_pack_bx_sync",
         "external_out_ports": ["csp_out", "frame_cnt"]},
    ]
}

_DESIGN_NO_PACKER = {
    "modules": [
        {"name": "raw_output", "ref": "best_candidate",
         "external_out_ports": ["best_constr_candidate"]},
    ]
}

_DESIGN_EMPTY = {"modules": []}


def _mapping_with_gmt(tmp_path, n_gmt=2) -> Path:
    endpoints = [
        _ep(f"tx_slr0_gt0_lane{i}", "tx", 0, 0, i,
            link_function="GMT", endpoint_role="trigger_output_gmt")
        for i in range(n_gmt)
    ]
    return _make_mapping(endpoints, tmp_path)


def _write_design(tmp_path: Path, design: dict) -> Path:
    import yaml
    p = tmp_path / "design.yml"
    p.write_text(yaml.dump(design))
    return p


class TestGmtCompatibility:
    def test_ok_verdict_2_csp_2_tx(self, tmp_path):
        """Test 15: GMT output compatibility report generated."""
        design_p  = _write_design(tmp_path, _DESIGN_OK)
        mapping_p = _mapping_with_gmt(tmp_path, n_gmt=2)
        report = check_gmt_compatibility(design_p, mapping_p)
        assert report.verdict == VERDICT_OK
        assert report.gmt_tx_count == 2
        assert report.algo_output_count == 2

    def test_needs_packer_verdict(self, tmp_path):
        design_p  = _write_design(tmp_path, _DESIGN_NO_PACKER)
        mapping_p = _mapping_with_gmt(tmp_path, n_gmt=1)
        report = check_gmt_compatibility(design_p, mapping_p)
        # raw_output is not a csp_pack module → could be UNKNOWN or NEEDS_PACKER
        # The design has 0 CSP outputs (best_candidate is not recognized)
        assert report.verdict in (VERDICT_UNKNOWN, VERDICT_NEEDS_PACKER,
                                   VERDICT_NEEDS_MORE_TX_LINKS)

    def test_needs_more_tx_links(self, tmp_path):
        design_p  = _write_design(tmp_path, _DESIGN_OK)
        mapping_p = _mapping_with_gmt(tmp_path, n_gmt=1)   # only 1 TX, design has 2 CSP outs
        report = check_gmt_compatibility(design_p, mapping_p)
        assert report.verdict == VERDICT_NEEDS_MORE_TX_LINKS

    def test_needs_mapping_update(self, tmp_path):
        design_p  = _write_design(tmp_path, _DESIGN_OK)
        mapping_p = _mapping_with_gmt(tmp_path, n_gmt=4)   # 4 TX, design only has 2
        report = check_gmt_compatibility(design_p, mapping_p)
        assert report.verdict == VERDICT_NEEDS_MAPPING_UPDATE

    def test_unknown_verdict_empty_design(self, tmp_path):
        design_p  = _write_design(tmp_path, _DESIGN_EMPTY)
        mapping_p = _mapping_with_gmt(tmp_path, n_gmt=2)
        report = check_gmt_compatibility(design_p, mapping_p)
        assert report.verdict == VERDICT_UNKNOWN

    def test_report_has_findings(self, tmp_path):
        design_p  = _write_design(tmp_path, _DESIGN_OK)
        mapping_p = _mapping_with_gmt(tmp_path, n_gmt=2)
        report = check_gmt_compatibility(design_p, mapping_p)
        assert len(report.findings) > 0

    def test_missing_design_file(self, tmp_path):
        mapping_p = _mapping_with_gmt(tmp_path)
        with pytest.raises(FileNotFoundError):
            check_gmt_compatibility(tmp_path / "nonexistent.yml", mapping_p)

    def test_missing_mapping_file(self, tmp_path):
        design_p = _write_design(tmp_path, _DESIGN_OK)
        with pytest.raises(FileNotFoundError):
            check_gmt_compatibility(design_p, tmp_path / "nonexistent.json")


# ---------------------------------------------------------------------------
# Real design + real CSV regression (if available)
# ---------------------------------------------------------------------------

PLUGIN_DIR   = Path(__file__).resolve().parents[2] / "plugins" / "omtf_firmware"
REAL_CSV     = PLUGIN_DIR / "arc" / "platforms" / "blobfish_x2o_vu13p" / "omtf_mapping.csv"
REAL_DESIGN  = PLUGIN_DIR / "arc" / "designs" / "design.yml"


@pytest.mark.skipif(
    not (REAL_CSV.exists() and REAL_DESIGN.exists()),
    reason="Real CSV/design not available",
)
class TestRealDesignGmtCompatibility:
    @pytest.fixture(scope="class")
    def real_mapping(self, tmp_path_factory):
        from omtf_tools.csv_normalizer import normalize, write_normalized_json
        tmp = tmp_path_factory.mktemp("real_gmt")
        m = normalize(REAL_CSV)
        p = tmp / "mapping.json"
        write_normalized_json(m, p)
        return p

    def test_real_design_gmt_verdict(self, real_mapping):
        report = check_gmt_compatibility(REAL_DESIGN, real_mapping)
        # Real design has 3 csp_pack outputs but only 2 GMT TX selected → NEEDS_MORE_TX_LINKS
        assert report.verdict == VERDICT_NEEDS_MORE_TX_LINKS

    def test_real_algo_output_count(self, real_mapping):
        report = check_gmt_compatibility(REAL_DESIGN, real_mapping)
        # Real design: out_csp_best_constr, out_csp_best_unconstr, out_csp_nn
        assert report.algo_output_count == 3

    def test_real_gmt_tx_count(self, real_mapping):
        report = check_gmt_compatibility(REAL_DESIGN, real_mapping)
        assert report.gmt_tx_count == 2
