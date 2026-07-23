"""Tests for forge.framework.importer (Milestone 2 — FORGE framework import)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from forge.framework.importer import load, FrameworkImportError, AbiPort, Endpoint


# ---------------------------------------------------------------------------
# Fixtures: minimal valid ABI + endpoints JSON written to tmp files
# ---------------------------------------------------------------------------

_MINIMAL_ABI = {
    "generator":    "test",
    "generated_at": "2025-01-01T00:00:00+00:00",
    "project":      "test_project",
    "board":        "X2O_VU13P",
    "module":       "payload",
    "ports": [
        {"name": "logic_clk",   "direction": "input",  "width": 1,   "kind": "clock_logic"},
        {"name": "lhc_clk",     "direction": "input",  "width": 1,   "kind": "clock_lhc"},
        # slr2 gt0 rx (4 lanes)
        {"name": "slr2_gt_0_rx_tdata",  "direction": "input",  "width": 256,
         "kind": "gt_rx_tdata",  "slr": 2, "gt_site": 0, "lanes": 4, "lane_width": 64},
        {"name": "slr2_gt_0_rx_tvalid", "direction": "input",  "width": 4,
         "kind": "gt_rx_tvalid", "slr": 2, "gt_site": 0, "lanes": 4},
        # slr2 gt0 tx (4 lanes)
        {"name": "slr2_gt_0_tx_tdata",  "direction": "output", "width": 256,
         "kind": "gt_tx_tdata",  "slr": 2, "gt_site": 0, "lanes": 4, "lane_width": 64},
        {"name": "slr2_gt_0_tx_tvalid", "direction": "output", "width": 4,
         "kind": "gt_tx_tvalid", "slr": 2, "gt_site": 0, "lanes": 4},
        {"name": "slr2_gt_0_algo_clk",  "direction": "output", "width": 1,
         "kind": "gt_algo_clk",  "slr": 2, "gt_site": 0},
    ],
}

_MINIMAL_EPS = {
    "generator":    "test",
    "generated_at": "2025-01-01T00:00:00+00:00",
    "project":      "test_project",
    "board":        "X2O_VU13P",
    "endpoints": [
        {
            "endpoint_id":          "rx_slr2_gt0_lane0",
            "direction":            "rx",
            "slr":                  2,
            "gt_site":              0,
            "lane":                 0,
            "lane_width":           64,
            "protocol":             "csp_64b",
            "quad_type":            "gty25",
            "cage":                 16,
            "fiber":                1,
            "polarity":             0,
            "link_function":        "DT_BMT",
            "logical_id":           0,
            "logical_name":         "dt_bmt_rx_00",
            "endpoint_role":        "detector_input_dt",
            "include_in_framework": True,
        },
        {
            "endpoint_id":          "tx_slr2_gt0_lane0",
            "direction":            "tx",
            "slr":                  2,
            "gt_site":              0,
            "lane":                 0,
            "lane_width":           64,
            "protocol":             "csp_64b",
            "quad_type":            "gty25",
            "cage":                 16,
            "fiber":                1,
            "polarity":             0,
            "link_function":        "MUON_OUTPUT",
            "logical_id":           0,
            "logical_name":         "gmt_tx_00",
            "endpoint_role":        "trigger_output_gmt",
            "include_in_framework": True,
        },
    ],
}


def _write_tmp(data: dict) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, f)
    f.close()
    return Path(f.name)


@pytest.fixture()
def abi_path(tmp_path):
    p = tmp_path / "payload_abi.json"
    p.write_text(json.dumps(_MINIMAL_ABI))
    return p


@pytest.fixture()
def ep_path(tmp_path):
    p = tmp_path / "payload_endpoints.json"
    p.write_text(json.dumps(_MINIMAL_EPS))
    return p


# ---------------------------------------------------------------------------
# Basic import tests
# ---------------------------------------------------------------------------

class TestLoad:
    def test_successful_load(self, abi_path, ep_path):
        fi = load("blobfish", abi_path, ep_path)
        assert fi.provider == "blobfish"
        assert fi.board == "X2O_VU13P"
        assert fi.module_name == "payload"

    def test_port_count(self, abi_path, ep_path):
        fi = load("blobfish", abi_path, ep_path)
        assert len(fi.ports) == len(_MINIMAL_ABI["ports"])

    def test_endpoint_count(self, abi_path, ep_path):
        fi = load("blobfish", abi_path, ep_path)
        assert len(fi.endpoints) == 2

    def test_rx_endpoints(self, abi_path, ep_path):
        fi = load("blobfish", abi_path, ep_path)
        rx = fi.rx_endpoints()
        assert len(rx) == 1
        assert rx[0].endpoint_id == "rx_slr2_gt0_lane0"

    def test_tx_endpoints(self, abi_path, ep_path):
        fi = load("blobfish", abi_path, ep_path)
        tx = fi.tx_endpoints()
        assert len(tx) == 1
        assert tx[0].endpoint_id == "tx_slr2_gt0_lane0"

    def test_get_port(self, abi_path, ep_path):
        fi = load("blobfish", abi_path, ep_path)
        p = fi.get_port("logic_clk")
        assert p is not None
        assert p.direction == "input"
        assert p.width == 1

    def test_get_endpoint(self, abi_path, ep_path):
        fi = load("blobfish", abi_path, ep_path)
        ep = fi.get_endpoint("rx_slr2_gt0_lane0")
        assert ep is not None
        assert ep.direction == "rx"
        assert ep.lane == 0

    def test_abi_port_for_endpoint(self, abi_path, ep_path):
        fi = load("blobfish", abi_path, ep_path)
        ep = fi.get_endpoint("rx_slr2_gt0_lane0")
        tdata_port = fi.abi_port_for_endpoint(ep)
        assert tdata_port is not None
        assert tdata_port.name == "slr2_gt_0_rx_tdata"

    def test_endpoint_abi_prefix(self, abi_path, ep_path):
        fi = load("blobfish", abi_path, ep_path)
        ep = fi.get_endpoint("rx_slr2_gt0_lane0")
        assert ep.abi_port_prefix == "slr2_gt_0"


class TestLoadErrors:
    def test_unknown_provider(self, abi_path, ep_path):
        with pytest.raises(FrameworkImportError, match="Unknown provider"):
            load("unknown_fw", abi_path, ep_path)

    def test_missing_abi_file(self, ep_path, tmp_path):
        with pytest.raises(FileNotFoundError):
            load("blobfish", tmp_path / "nonexistent.json", ep_path)

    def test_missing_ep_file(self, abi_path, tmp_path):
        with pytest.raises(FileNotFoundError):
            load("blobfish", abi_path, tmp_path / "nonexistent.json")

    def test_endpoint_lane_out_of_range(self, tmp_path):
        abi = tmp_path / "abi.json"
        ep  = tmp_path / "ep.json"
        abi.write_text(json.dumps(_MINIMAL_ABI))
        bad_eps = json.loads(json.dumps(_MINIMAL_EPS))
        bad_eps["endpoints"][0]["lane"] = 99  # out of range
        ep.write_text(json.dumps(bad_eps))
        with pytest.raises(FrameworkImportError, match="out of range"):
            load("blobfish", abi, ep)

    def test_endpoint_missing_abi_site(self, tmp_path):
        abi = tmp_path / "abi.json"
        ep  = tmp_path / "ep.json"
        abi.write_text(json.dumps(_MINIMAL_ABI))
        bad_eps = json.loads(json.dumps(_MINIMAL_EPS))
        bad_eps["endpoints"][0]["gt_site"] = 99  # no such site in ABI
        ep.write_text(json.dumps(bad_eps))
        with pytest.raises(FrameworkImportError, match="no ABI port group"):
            load("blobfish", abi, ep)
