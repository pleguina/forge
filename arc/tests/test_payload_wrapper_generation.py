"""Tests for arc.framework.payload_generator (Milestone 3 — payload.v generation)."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from arc.framework.importer import load
from arc.framework.io_resolver import resolve
from arc.framework.payload_generator import (
    generate_payload_verilog, ControlPolicies, PayloadWrapperGenerator,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ABI = {
    "generator": "test", "generated_at": "2025-01-01T00:00:00+00:00",
    "project": "test_project", "board": "X2O_VU13P", "module": "payload",
    "ports": [
        {"name": "logic_clk",   "direction": "input",  "width": 1, "kind": "clock_logic"},
        {"name": "lhc_clk",     "direction": "input",  "width": 1, "kind": "clock_lhc"},
        {"name": "orbit",       "direction": "input",  "width": 1, "kind": "orbit_sync"},
        {"name": "slr2_config_registers_in",    "direction": "input",  "width": 64,
         "kind": "slr_config_registers", "slr": 2, "n_registers": 1},
        {"name": "slr2_readback_registers_out", "direction": "output", "width": 64,
         "kind": "slr_readback_registers", "slr": 2, "n_registers": 1},
        {"name": "slr2_gt_0_algo_clk", "direction": "output", "width": 1,
         "kind": "gt_algo_clk", "slr": 2, "gt_site": 0},
        {"name": "slr2_gt_0_buffinc_out", "direction": "output", "width": 1,
         "kind": "gt_buffer_increment", "slr": 2, "gt_site": 0, "lanes": 1},
        {"name": "slr2_gt_0_buffdec_out", "direction": "output", "width": 1,
         "kind": "gt_buffer_decrement", "slr": 2, "gt_site": 0, "lanes": 1},
        {"name": "slr2_gt_0_rx_tdata",  "direction": "input",  "width": 64,
         "kind": "gt_rx_tdata",  "slr": 2, "gt_site": 0, "lanes": 1, "lane_width": 64},
        {"name": "slr2_gt_0_rx_tvalid", "direction": "input",  "width": 1,
         "kind": "gt_rx_tvalid", "slr": 2, "gt_site": 0, "lanes": 1},
        {"name": "slr2_gt_0_rx_tfirst", "direction": "input",  "width": 1,
         "kind": "gt_rx_tfirst", "slr": 2, "gt_site": 0, "lanes": 1},
        {"name": "slr2_gt_0_rx_tlast",  "direction": "input",  "width": 1,
         "kind": "gt_rx_tlast",  "slr": 2, "gt_site": 0, "lanes": 1},
        {"name": "slr2_gt_0_tx_tdata",  "direction": "output", "width": 64,
         "kind": "gt_tx_tdata",  "slr": 2, "gt_site": 0, "lanes": 1, "lane_width": 64},
        {"name": "slr2_gt_0_tx_tvalid", "direction": "output", "width": 1,
         "kind": "gt_tx_tvalid", "slr": 2, "gt_site": 0, "lanes": 1},
        {"name": "slr2_gt_0_tx_tfirst", "direction": "output", "width": 1,
         "kind": "gt_tx_tfirst", "slr": 2, "gt_site": 0, "lanes": 1},
        {"name": "slr2_gt_0_tx_tlast",  "direction": "output", "width": 1,
         "kind": "gt_tx_tlast",  "slr": 2, "gt_site": 0, "lanes": 1},
    ],
}

_EPS = {
    "generator": "test", "board": "X2O_VU13P",
    "endpoints": [
        {"endpoint_id": "rx_slr2_gt0_lane0", "direction": "rx", "slr": 2, "gt_site": 0,
         "lane": 0, "lane_width": 64, "protocol": "csp_64b", "quad_type": "gty25",
         "cage": 16, "fiber": 1, "polarity": 0, "link_function": "DT_BMT",
         "logical_id": 0, "logical_name": "dt_bmt_rx_00",
         "endpoint_role": "detector_input_dt", "include_in_framework": True},
        {"endpoint_id": "tx_slr2_gt0_lane0", "direction": "tx", "slr": 2, "gt_site": 0,
         "lane": 0, "lane_width": 64, "protocol": "csp_64b", "quad_type": "gty25",
         "cage": 16, "fiber": 1, "polarity": 0, "link_function": "MUON_OUTPUT",
         "logical_id": 0, "logical_name": "gmt_tx_00",
         "endpoint_role": "trigger_output_gmt", "include_in_framework": True},
    ],
}

_DET_IO = textwrap.dedent("""\
    detector_inputs:
      - name: dt_input_00
        detector: DT
        blobfish_endpoint: rx_slr2_gt0_lane0
        detector_object:
          source_type: BMT-L1
        frontend:
          module: dt_interface
          instance: dt_if_00
          parameters:
            input_id: 0
        output:
          wiring_kind: dt_processed_stub
          target_instance: concentrator
          target_port: dt_inputs[0]
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
def resolved_io(fw, tmp_path):
    p = tmp_path / "detector_io.yml"
    p.write_text(_DET_IO)
    return resolve(p, fw)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDummyPayload:
    """payload.v generation without resolved I/O (stub mode)."""

    def test_generates_without_error(self, fw):
        content = generate_payload_verilog(fw, resolved_io=None)
        assert content

    def test_module_declaration(self, fw):
        content = generate_payload_verilog(fw, resolved_io=None)
        assert "module payload (" in content

    def test_all_ports_declared(self, fw):
        content = generate_payload_verilog(fw, resolved_io=None)
        for port in fw.ports:
            assert port.name in content, f"Port '{port.name}' missing from payload.v"

    def test_endmodule_present(self, fw):
        content = generate_payload_verilog(fw, resolved_io=None)
        assert "endmodule" in content

    def test_algo_clk_assigned(self, fw):
        content = generate_payload_verilog(fw, resolved_io=None)
        assert "slr2_gt_0_algo_clk" in content
        assert "logic_clk" in content

    def test_buffinc_zero(self, fw):
        content = generate_payload_verilog(fw, resolved_io=None)
        assert "slr2_gt_0_buffinc_out" in content
        assert "1'b0" in content

    def test_readback_zero(self, fw):
        content = generate_payload_verilog(fw, resolved_io=None)
        assert "slr2_readback_registers_out" in content
        assert "64'b0" in content

    def test_writes_file(self, fw, tmp_path):
        out = tmp_path / "payload.v"
        generate_payload_verilog(fw, resolved_io=None, output_path=out)
        assert out.exists()
        assert out.stat().st_size > 0


class TestResolvedPayload:
    """payload.v generation with resolved detector I/O."""

    def test_rx_lane_slicing_present(self, fw, resolved_io):
        content = generate_payload_verilog(fw, resolved_io)
        assert "rx_slr2_gt0_lane0_tdata" in content

    def test_rx_slice_formula(self, fw, resolved_io):
        content = generate_payload_verilog(fw, resolved_io)
        assert "slr2_gt_0_rx_tdata[64*0 +: 64]" in content

    def test_tx_active_lane_wired(self, fw, resolved_io):
        content = generate_payload_verilog(fw, resolved_io)
        assert "tx_slr2_gt0_lane0_tdata" in content

    def test_algo_module_instantiated(self, fw, resolved_io):
        content = generate_payload_verilog(fw, resolved_io,
                                           algo_module="arc_omtf_algo_top")
        assert "arc_omtf_algo_top u_algo_top" in content

    def test_custom_algo_module(self, fw, resolved_io):
        content = generate_payload_verilog(fw, resolved_io, algo_module="my_custom_top")
        assert "my_custom_top u_algo_top" in content


class TestControlPolicies:
    def test_default_algo_clk(self, fw):
        content = generate_payload_verilog(fw, None)
        assert "assign slr2_gt_0_algo_clk = logic_clk" in content

    def test_custom_algo_clk(self, fw):
        policies = ControlPolicies(algo_clk="lhc_clk")
        content  = generate_payload_verilog(fw, None, policies=policies)
        assert "assign slr2_gt_0_algo_clk = lhc_clk" in content

    def test_port_list_matches_abi(self, fw):
        content = generate_payload_verilog(fw, None)
        # Every port in ABI must appear in the port list section
        for port in fw.ports:
            assert port.name in content
