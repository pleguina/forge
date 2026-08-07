"""Tests for forge.integration.io_resolver (Milestone 2 — Detector I/O resolution)."""

from __future__ import annotations

import json
import textwrap
import tempfile
from pathlib import Path

import pytest

from forge.integration.importer import load
from forge.integration.io_resolver import resolve, DetectorIOError, to_json


# ---------------------------------------------------------------------------
# Shared ABI / endpoint fixtures
# ---------------------------------------------------------------------------

_ABI = {
    "generator":    "test",
    "generated_at": "2025-01-01T00:00:00+00:00",
    "project":      "test_project",
    "board":        "X2O_VU13P",
    "module":       "payload",
    "ports": [
        {"name": "logic_clk", "direction": "input", "width": 1, "kind": "clock_logic"},
        {"name": "slr2_gt_0_rx_tdata",  "direction": "input",  "width": 256,
         "kind": "gt_rx_tdata",  "slr": 2, "gt_site": 0, "lanes": 4, "lane_width": 64},
        {"name": "slr2_gt_0_tx_tdata",  "direction": "output", "width": 256,
         "kind": "gt_tx_tdata",  "slr": 2, "gt_site": 0, "lanes": 4, "lane_width": 64},
        {"name": "slr2_gt_0_algo_clk",  "direction": "output", "width": 1,
         "kind": "gt_algo_clk",  "slr": 2, "gt_site": 0},
    ],
}

_EPS = {
    "generator": "test",
    "board":     "X2O_VU13P",
    "endpoints": [
        {"endpoint_id": "rx_slr2_gt0_lane0", "direction": "rx", "slr": 2, "gt_site": 0,
         "lane": 0, "lane_width": 64, "protocol": "csp_64b", "quad_type": "gty25",
         "cage": 16, "fiber": 1, "polarity": 0, "link_function": "DT_BMT",
         "logical_id": 0, "logical_name": "dt_bmt_rx_00",
         "endpoint_role": "detector_input_dt", "include_in_framework": True},
        {"endpoint_id": "rx_slr2_gt0_lane1", "direction": "rx", "slr": 2, "gt_site": 0,
         "lane": 1, "lane_width": 64, "protocol": "csp_64b", "quad_type": "gty25",
         "cage": 16, "fiber": 2, "polarity": 0, "link_function": "CSC",
         "logical_id": 12, "logical_name": "csc_rx_12",
         "endpoint_role": "detector_input_csc", "include_in_framework": True},
        {"endpoint_id": "tx_slr2_gt0_lane0", "direction": "tx", "slr": 2, "gt_site": 0,
         "lane": 0, "lane_width": 64, "protocol": "csp_64b", "quad_type": "gty25",
         "cage": 16, "fiber": 1, "polarity": 0, "link_function": "MUON_OUTPUT",
         "logical_id": 0, "logical_name": "gmt_tx_00",
         "endpoint_role": "trigger_output_gmt", "include_in_framework": True},
    ],
}

_DETECTOR_IO_YAML = textwrap.dedent("""\
detector_inputs:
  - name: dt_input_00
    detector: DT
    blobfish_endpoint: rx_slr2_gt0_lane0
    detector_object:
      source_type: BMT-L1
      wheel: null
    frontend:
      module: dt_interface
      instance: dt_if_00
      parameters:
        input_id: 0
    output:
      wiring_kind: dt_processed_stub
      target_instance: concentrator
      target_port: dt_inputs[0]

  - name: csc_input_12
    detector: CSC
    blobfish_endpoint: rx_slr2_gt0_lane1
    detector_object:
      endcap: 1
      station: 1
      ring: 2
    frontend:
      module: csc_interface
      instance: csc_if_12
      parameters:
        input_id: 12
    output:
      wiring_kind: csc_processed_stub
      target_instance: concentrator
      target_port: csc_inputs[12]

trigger_outputs:
  - name: gmt_output_0
    blobfish_endpoint: tx_slr2_gt0_lane0
    source:
      instance: algo_muon_packer
      port: gmt_output_0
    wiring_kind: gmt_muon_output
""")


@pytest.fixture()
def fw(tmp_path):
    abi_p = tmp_path / "abi.json"
    ep_p  = tmp_path / "ep.json"
    abi_p.write_text(json.dumps(_ABI))
    ep_p.write_text(json.dumps(_EPS))
    return load("blobfish", abi_p, ep_p)


@pytest.fixture()
def detector_io_path(tmp_path):
    p = tmp_path / "detector_io.yml"
    p.write_text(_DETECTOR_IO_YAML)
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestResolveSuccess:
    def test_resolves_without_error(self, fw, detector_io_path):
        resolved = resolve(detector_io_path, fw)
        assert resolved is not None

    def test_input_count(self, fw, detector_io_path):
        resolved = resolve(detector_io_path, fw)
        assert len(resolved.inputs) == 2

    def test_output_count(self, fw, detector_io_path):
        resolved = resolve(detector_io_path, fw)
        assert len(resolved.outputs) == 1

    def test_dt_input_endpoint_resolved(self, fw, detector_io_path):
        resolved = resolve(detector_io_path, fw)
        dt = next(ri for ri in resolved.inputs if ri.name == "dt_input_00")
        assert dt.endpoint.endpoint_id == "rx_slr2_gt0_lane0"
        assert dt.endpoint.direction   == "rx"

    def test_csc_input_endpoint_resolved(self, fw, detector_io_path):
        resolved = resolve(detector_io_path, fw)
        csc = next(ri for ri in resolved.inputs if ri.name == "csc_input_12")
        assert csc.endpoint.endpoint_id == "rx_slr2_gt0_lane1"

    def test_output_endpoint_resolved(self, fw, detector_io_path):
        resolved = resolve(detector_io_path, fw)
        out = resolved.outputs[0]
        assert out.endpoint.endpoint_id == "tx_slr2_gt0_lane0"
        assert out.endpoint.direction   == "tx"

    def test_serialise_round_trip(self, fw, detector_io_path):
        resolved = resolve(detector_io_path, fw)
        data = to_json(resolved)
        assert len(data["detector_inputs"]) == 2
        assert len(data["trigger_outputs"]) == 1
        # Ensure JSON-serialisable
        json.dumps(data)


class TestResolveErrors:
    def _write_io(self, tmp_path, content: str) -> Path:
        p = tmp_path / "detector_io.yml"
        p.write_text(content)
        return p

    def test_missing_endpoint(self, fw, tmp_path):
        p = self._write_io(tmp_path, textwrap.dedent("""\
            detector_inputs:
              - name: bad_input
                detector: DT
                blobfish_endpoint: rx_does_not_exist
                frontend:
                  module: dt_interface
                  instance: dt_if_bad
            trigger_outputs: []
        """))
        with pytest.raises(DetectorIOError, match="not found in framework manifest"):
            resolve(p, fw)

    def test_rx_on_tx_endpoint_rejected(self, fw, tmp_path):
        p = self._write_io(tmp_path, textwrap.dedent("""\
            detector_inputs:
              - name: bad_direction
                detector: DT
                blobfish_endpoint: tx_slr2_gt0_lane0
                frontend:
                  module: dt_interface
                  instance: dt_if_bad
            trigger_outputs: []
        """))
        with pytest.raises(DetectorIOError, match="direction"):
            resolve(p, fw)

    def test_tx_on_rx_endpoint_rejected(self, fw, tmp_path):
        p = self._write_io(tmp_path, textwrap.dedent("""\
            detector_inputs: []
            trigger_outputs:
              - name: bad_direction
                blobfish_endpoint: rx_slr2_gt0_lane0
                source:
                  instance: x
                  port: y
                wiring_kind: gmt_muon_output
        """))
        with pytest.raises(DetectorIOError, match="direction"):
            resolve(p, fw)

    def test_wrong_detector_role(self, fw, tmp_path):
        # Try to assign a CSC role endpoint to a DT detector input
        p = self._write_io(tmp_path, textwrap.dedent("""\
            detector_inputs:
              - name: bad_role
                detector: DT
                blobfish_endpoint: rx_slr2_gt0_lane1
                frontend:
                  module: dt_interface
                  instance: dt_if_bad
            trigger_outputs: []
        """))
        # rx_slr2_gt0_lane1 has role 'detector_input_csc', not compatible with DT
        with pytest.raises(DetectorIOError, match="incompatible"):
            resolve(p, fw)

    def test_duplicate_endpoint_consumption(self, fw, tmp_path):
        p = self._write_io(tmp_path, textwrap.dedent("""\
            detector_inputs:
              - name: dt_00
                detector: DT
                blobfish_endpoint: rx_slr2_gt0_lane0
                frontend:
                  module: dt_interface
                  instance: dt_if_00
              - name: dt_00_dup
                detector: DT
                blobfish_endpoint: rx_slr2_gt0_lane0
                frontend:
                  module: dt_interface
                  instance: dt_if_01
            trigger_outputs: []
        """))
        with pytest.raises(DetectorIOError, match="already consumed"):
            resolve(p, fw)

    def test_frontend_module_registry_check(self, fw, detector_io_path):
        # With a registry that doesn't contain 'dt_interface', should fail
        with pytest.raises(DetectorIOError, match="not found in registry"):
            resolve(detector_io_path, fw, registry_modules={"csc_interface", "algo_muon_packer"})

    def test_missing_file(self, fw, tmp_path):
        with pytest.raises(FileNotFoundError):
            resolve(tmp_path / "nonexistent.yml", fw)
