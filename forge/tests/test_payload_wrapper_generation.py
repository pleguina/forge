"""Tests for forge.integration.payload_generator (Milestone 3 — payload.v generation)."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from forge.integration.importer import load
from forge.integration.io_resolver import resolve
from forge.integration.payload_generator import (
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
          algo_port: dt_0_csp_in
    trigger_outputs:
      - name: gmt_output_0
        blobfish_endpoint: tx_slr2_gt0_lane0
        source:
                    instance: out_csp_nn
                    port: csp_out
        wiring_kind: gmt_muon_output
""")


# A payload with links must say what drives the algorithm's reset.
_NO_RESET = ControlPolicies(reset="disabled")


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
        content = generate_payload_verilog(fw, resolved_io, policies=_NO_RESET)
        assert "rx_slr2_gt0_lane0_tdata" in content

    def test_rx_slice_formula(self, fw, resolved_io):
        content = generate_payload_verilog(fw, resolved_io, policies=_NO_RESET)
        assert "slr2_gt_0_rx_tdata[64*0 +: 64]" in content

    def test_one_lane_control_ports_use_scalar_assignments(self, fw, resolved_io):
        content = generate_payload_verilog(fw, resolved_io, policies=_NO_RESET)
        assert "input  wire slr2_gt_0_rx_tvalid" in content
        assert "output wire slr2_gt_0_tx_tvalid" in content
        assert "rx_slr2_gt0_lane0_tvalid = slr2_gt_0_rx_tvalid" in content
        assert "slr2_gt_0_tx_tvalid = tx_slr2_gt0_lane0_tvalid" in content
        assert "slr2_gt_0_rx_tvalid[0]" not in content
        assert "slr2_gt_0_tx_tvalid[0]" not in content

    def test_tx_active_lane_wired(self, fw, resolved_io):
        content = generate_payload_verilog(fw, resolved_io, policies=_NO_RESET)
        assert "tx_slr2_gt0_lane0_tdata" in content

    def test_algo_module_instantiated(self, fw, resolved_io):
        content = generate_payload_verilog(fw, resolved_io, policies=_NO_RESET,
                                           algo_module="arc_algo_top")
        assert "arc_algo_top u_algo_top" in content

    def test_input_uses_explicit_algo_port_override(self, fw, resolved_io):
        content = generate_payload_verilog(fw, resolved_io, policies=_NO_RESET)
        assert ".dt_0_csp_in ({rx_slr2_gt0_lane0_tdata, rx_slr2_gt0_lane0_tlast, rx_slr2_gt0_lane0_tfirst, rx_slr2_gt0_lane0_tvalid})" in content
        assert ".dt_inputs[0]" not in content

    def test_input_without_override_uses_generic_sanitization(self, fw, tmp_path):
        # No `algo_port` override: "name[idx]" is sanitized to "name_idx"
        # generically, with no detector-specific pattern matching.
        det_io = textwrap.dedent("""\
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
        """)
        p = tmp_path / "detector_io.yml"
        p.write_text(det_io)
        resolved = resolve(p, fw)
        content = generate_payload_verilog(fw, resolved, policies=_NO_RESET)
        # Still packed (csp protocol) since that's derived from the endpoint,
        # but the port name is the generic sanitization, not a CMS-specific one.
        assert ".dt_inputs_0 ({rx_slr2_gt0_lane0_tdata, rx_slr2_gt0_lane0_tlast, rx_slr2_gt0_lane0_tfirst, rx_slr2_gt0_lane0_tvalid})" in content

    def test_csp_output_uses_generated_port_and_unpack(self, fw, resolved_io):
        content = generate_payload_verilog(fw, resolved_io, policies=_NO_RESET)
        assert ".out_csp_nn_csp_out (tx_slr2_gt0_lane0_csp)" in content
        assert "tx_slr2_gt0_lane0_tdata = tx_slr2_gt0_lane0_csp[66:3]" in content
        assert "tx_slr2_gt0_lane0_tvalid = tx_slr2_gt0_lane0_csp[0]" in content
        assert ".csp_out" not in content

    def test_custom_algo_module(self, fw, resolved_io):
        content = generate_payload_verilog(fw, resolved_io, algo_module="my_custom_top", policies=_NO_RESET)
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

    def test_port_alias_overrides_output_derivation(self, fw, resolved_io):
        policies = ControlPolicies(port_aliases={"out_csp_nn.csp_out": "renamed_output_port"}, reset="disabled")
        content = generate_payload_verilog(fw, resolved_io, policies=policies)
        assert ".renamed_output_port (tx_slr2_gt0_lane0_csp)" in content
        assert ".out_csp_nn_csp_out" not in content


class TestPayloadPolicies:
    """The payload's own logic: link registers, extra connections, driven outputs, gates, Verilog blocks."""

    def test_tx_register_and_orbit(self, tmp_path):
        abi = dict(_ABI, ports=_ABI["ports"] + [
            {"name": "slr2_gt_0_tx_orbit", "direction": "output", "width": 1,
             "kind": "gt_tx_orbit", "slr": 2, "gt_site": 0, "lanes": 1}])
        (tmp_path / "abi.json").write_text(json.dumps(abi))
        (tmp_path / "ep.json").write_text(json.dumps(_EPS))
        fw = load("blobfish", tmp_path / "abi.json", tmp_path / "ep.json")
        (tmp_path / "detector_io.yml").write_text(_DET_IO)
        resolved_io = resolve(tmp_path / "detector_io.yml", fw)
        policies = ControlPolicies.from_dict({"reset": "disabled", "tx": {"register": True, "orbit": "orbit_out"}})
        content = generate_payload_verilog(fw, resolved_io, policies=policies)
        assert "always @(posedge logic_clk) tx_slr2_gt0_lane0_csp_q <= tx_slr2_gt0_lane0_csp;" in content
        assert "assign tx_slr2_gt0_lane0_tdata = tx_slr2_gt0_lane0_csp_q[66:3];" in content
        assert "always @(posedge logic_clk) tx_slr2_gt0_lane0_orbit_q <= algo_tx_orbit;" in content
        assert "assign slr2_gt_0_tx_orbit = tx_slr2_gt0_lane0_orbit_q;" in content
        assert ".orbit_out (algo_tx_orbit)" in content
        assert content.index("wire algo_tx_orbit;") < content.index("u_algo_top (")

    def test_tx_orbit_without_an_orbit_port_is_refused(self, fw, resolved_io):
        policies = ControlPolicies.from_dict({"reset": "disabled", "tx": {"orbit": "orbit_out"}})
        with pytest.raises(ValueError, match="no slr2_gt_0_tx_orbit port"):
            generate_payload_verilog(fw, resolved_io, policies=policies)

    def test_input_register_and_registered_connection(self, fw, resolved_io):
        policies = ControlPolicies.from_dict({"reset": "disabled", 
            "input_register": True,
            "connect": {"bx0_in": {"net": "bx0", "register": True}, "mon_out": "mon_wire"},
        })
        content = generate_payload_verilog(fw, resolved_io, policies=policies)
        assert ('(* shreg_extract = "no" *) reg [66:0] lin_q_dt_0_csp_in = 67\'b0;') in content
        assert ("always @(posedge logic_clk) lin_q_dt_0_csp_in <= {rx_slr2_gt0_lane0_tdata, "
                "rx_slr2_gt0_lane0_tlast, rx_slr2_gt0_lane0_tfirst, rx_slr2_gt0_lane0_tvalid};") in content
        assert ".dt_0_csp_in (lin_q_dt_0_csp_in)" in content
        assert "always @(posedge logic_clk) lin_q_bx0_in <= bx0;" in content
        assert ".bx0_in (lin_q_bx0_in)" in content
        assert ".mon_out (mon_wire)\n);" in content

    def test_driven_outputs_replace_the_tie_offs(self, fw, resolved_io):
        policies = ControlPolicies.from_dict({"reset": "disabled", "drive": {
            "slr2_readback_registers_out": "status",
            "slr2_gt_0_buffinc_out": {"expr": "inc", "comment": "why"},
        }})
        content = generate_payload_verilog(fw, resolved_io, policies=policies)
        assert "64'b0" not in content.split("slr2_readback_registers_out =")[-1].split(";")[0]
        assert content.count("assign slr2_readback_registers_out =") == 1
        assert "assign slr2_readback_registers_out = status;" in content
        assert "assign slr2_gt_0_buffinc_out = inc;  // why" in content
        assert content.index("assign slr2_readback_registers_out = status;") < content.index("u_algo_top (")

    def test_rx_gate_masks_every_lane_signal(self, fw, resolved_io):
        policies = ControlPolicies.from_dict({"reset": "disabled", "rx_gate": {"rx_slr2_gt0_lane0": "mk"}})
        content = generate_payload_verilog(fw, resolved_io, policies=policies)
        assert "assign rx_slr2_gt0_lane0_tdata = {64{mk}} & slr2_gt_0_rx_tdata[64*0 +: 64];" in content
        assert "assign rx_slr2_gt0_lane0_tvalid = mk & slr2_gt_0_rx_tvalid;" in content

    def test_verilog_blocks_after_the_ports_and_before_the_algorithm(self, fw, resolved_io, tmp_path):
        (tmp_path / "head.v").write_text("wire early;\n")
        (tmp_path / "late.v").write_text("wire late;\n")
        policies = ControlPolicies.from_dict(
            {"reset": "disabled", "verilog": {"header": ["head.v"], "before_algo": ["late.v"]}}, base=tmp_path)
        content = generate_payload_verilog(fw, resolved_io, policies=policies)
        assert content.index(");") < content.index("wire early;") < content.index("// Algo clocks")
        assert content.index("// TX lane assignments") < content.index("wire late;") < content.index("u_algo_top (")

    def test_reset_from_an_abi_port_through_a_synchronizer(self, fw, resolved_io):
        abi_in = next(p.name for p in fw.ports if p.direction == "input" and p.width == 1 and p.name != "logic_clk")
        policies = ControlPolicies.from_dict({"reset": {"source": abi_in, "active": "low", "sync_stages": 3}})
        content = generate_payload_verilog(fw, resolved_io, policies=policies)
        assert '(* ASYNC_REG = "TRUE" *) reg [2:0] algo_rst_q = {3{1\'b1}};' in content
        assert f"always @(posedge logic_clk) algo_rst_q <= {{algo_rst_q[1:0], ~{abi_in}}};" in content
        assert ".ap_rst  (algo_rst)" in content
        assert content.index("wire algo_rst =") < content.index("u_algo_top (")

    def test_a_disabled_reset_is_tied_low(self, fw, resolved_io):
        content = generate_payload_verilog(fw, resolved_io, policies=_NO_RESET)
        assert ".ap_rst  (1'b0)" in content and "algo_rst" not in content

    @pytest.mark.parametrize("reset, message", [
        (None, "control_policies.reset is required"),
        ({"source": "no_such_port"}, "not a 1-bit input port"),
        ({"source": "slr2_gt_0_rx_tdata"}, "not a 1-bit input port"),
        ({"source": "orbit", "active": "sideways"}, "reset.active"),
        ({"source": "orbit", "sync_stages": 0}, "reset.sync_stages"),
        ("sometimes", "want 'disabled'"),
    ])
    def test_bad_reset_policies_are_refused(self, fw, resolved_io, reset, message):
        with pytest.raises(ValueError, match=message):
            generate_payload_verilog(fw, resolved_io, policies=ControlPolicies(reset=reset))

    def test_unknown_verilog_place_is_refused(self):
        with pytest.raises(ValueError, match="verilog.middle"):
            ControlPolicies.from_dict({"verilog": {"middle": []}})


def _quad(gt: int, lanes: int) -> list:
    """RX and TX ports of a quad of *lanes* lanes on SLR2."""
    one = lambda kind, d, w: {"name": f"slr2_gt_{gt}_{kind}", "direction": d, "width": w,
                              "kind": f"gt_{kind}", "slr": 2, "gt_site": gt, "lanes": lanes}
    return [dict(one("rx_tdata", "input", 64 * lanes), lane_width=64),
            *(one(f"rx_{s}", "input", lanes) for s in ("tvalid", "tfirst", "tlast", "orbit")),
            dict(one("tx_tdata", "output", 64 * lanes), lane_width=64),
            *(one(f"tx_{s}", "output", lanes) for s in ("tvalid", "tfirst", "tlast", "orbit"))]


class TestTypedServices:
    """config_words, status_words and rx_taps: written by FORGE and checked against the ABI."""

    @pytest.fixture()
    def wide(self, tmp_path):
        ports = [dict(p, width=192, n_registers=3) if p["name"] == "slr2_config_registers_in" else p
                 for p in _ABI["ports"]]
        ports += [{"name": "slr2_usr_ctrl_in", "direction": "input", "width": 8, "kind": "slr_usr_ctrl"}]
        (tmp_path / "abi.json").write_text(json.dumps(dict(_ABI, ports=ports + _quad(1, 2))))
        (tmp_path / "ep.json").write_text(json.dumps(_EPS))
        fw = load("blobfish", tmp_path / "abi.json", tmp_path / "ep.json")
        (tmp_path / "detector_io.yml").write_text(_DET_IO)
        return fw, resolve(tmp_path / "detector_io.yml", fw)

    def _gen(self, wide, **policies):
        fw, resolved_io = wide
        return generate_payload_verilog(fw, resolved_io, policies=ControlPolicies.from_dict(
            {"reset": "disabled", **policies}))

    def test_config_words_connect_a_register_word(self, wide):
        content = self._gen(wide, config_words={"cfg_a": {"register": "slr2_config_registers_in", "word": 2}})
        assert ".cfg_a (slr2_config_registers_in[64*2 +: 64])" in content

    @pytest.mark.parametrize("spec, message", [
        ({"register": "slr2_readback_registers_out", "word": 0}, "not a config register port"),
        ({"register": "slr2_config_registers_in", "word": 3}, "holds 3 words, not word 3"),
    ])
    def test_config_words_outside_the_abi_are_refused(self, wide, spec, message):
        with pytest.raises(ValueError, match=message):
            self._gen(wide, config_words={"cfg_a": spec})

    def test_status_words_pack_fields_and_pad_with_zeros(self, wide):
        content = self._gen(wide, status_words={"slr2_readback_registers_out": [
            {"net": "locked", "width": 1}, {"zero": 15}, {"net": "count[15:0]", "width": 16}]})
        assert "assign slr2_readback_registers_out = {32'b0, count[15:0], 15'b0, locked};" in content
        assert content.count("assign slr2_readback_registers_out") == 1

    def test_status_words_wider_than_the_port_are_refused(self, wide):
        with pytest.raises(ValueError, match="65 bits of fields, the port is 64"):
            self._gen(wide, status_words={"slr2_readback_registers_out": [
                {"net": "a", "width": 64}, {"net": "b", "width": 1}]})

    def test_rx_tap_copies_a_lane_to_a_spare_tx_lane(self, wide):
        content = self._gen(wide, rx_taps={
            "enable": {"port": "slr2_usr_ctrl_in", "bit": 3}, "stages": 2,
            "taps": [{"name": "tap0", "rx": {"quad": "slr2_gt_1", "lane": 1}, "tx": {"quad": "slr2_gt_1", "lane": 0}},
                     {"name": "tap1", "rx": {"endpoint": "rx_slr2_gt0_lane0"},
                      "tx": {"quad": "slr2_gt_1", "lane": 1}}]})
        assert "always @(posedge logic_clk) rx_tap_en_s <= {rx_tap_en_s[0], slr2_usr_ctrl_in[3]};" in content
        assert ("    tap0_r0 <= rx_tap_en ? {slr2_gt_1_rx_tdata[64*1 +: 64], slr2_gt_1_rx_tvalid[1], "
                "slr2_gt_1_rx_tfirst[1], slr2_gt_1_rx_tlast[1], slr2_gt_1_rx_orbit[1]} : 68'b0;") in content
        assert "    tap0_r1 <= tap0_r0;" in content
        assert "assign slr2_gt_1_tx_tdata[64*0 +: 64] = tap0_r1[67:4];  // RX tap: slr2_gt_1 lane 1" in content
        assert "assign slr2_gt_1_tx_orbit[0] = tap0_r1[0];" in content
        assert ("tap1_r0 <= rx_tap_en ? {rx_slr2_gt0_lane0_tdata, rx_slr2_gt0_lane0_tvalid, "
                "rx_slr2_gt0_lane0_tfirst, rx_slr2_gt0_lane0_tlast, 1'b0} : 68'b0;") in content
        assert "assign slr2_gt_1_tx_tdata[64*1 +: 64] = 64'b0" not in content
        assert content.index("wire [63:0] rx_slr2_gt0_lane0_tdata;") < content.index("tap1_r0 <=")

    @pytest.mark.parametrize("tx, rx, message", [
        ({"quad": "slr2_gt_0", "lane": 0}, {"quad": "slr2_gt_1", "lane": 0}, "already driven"),
        ({"quad": "slr2_gt_1", "lane": 2}, {"quad": "slr2_gt_1", "lane": 0}, "has no TX lane 2"),
        ({"quad": "slr2_gt_1", "lane": 0}, {"endpoint": "rx_slr2_gt9_lane0"}, "not a connected RX endpoint"),
        ({"quad": "slr2_gt_1", "lane": 0}, {"quad": "slr2_gt_0", "lane": 0}, "slr2_gt_0_rx_orbit not found"),
    ])
    def test_rx_taps_outside_the_abi_are_refused(self, wide, tx, rx, message):
        with pytest.raises(ValueError, match=message):
            self._gen(wide, rx_taps={"enable": {"port": "slr2_usr_ctrl_in", "bit": 0},
                                     "taps": [{"name": "t", "rx": rx, "tx": tx}]})

    def test_rx_taps_need_an_enable_bit(self, wide):
        with pytest.raises(ValueError, match="rx_taps.enable"):
            self._gen(wide, rx_taps={"taps": [{"name": "t", "rx": {"quad": "slr2_gt_1", "lane": 0},
                                               "tx": {"quad": "slr2_gt_1", "lane": 1}}]})

    def test_a_service_and_a_hand_drive_of_one_port_are_refused(self, wide):
        with pytest.raises(ValueError, match="by a typed service and by hand: slr2_readback_registers_out"):
            self._gen(wide, drive={"slr2_readback_registers_out": "x"},
                      status_words={"slr2_readback_registers_out": [{"net": "y", "width": 1}]})

    def test_raw_entries_count_what_forge_does_not_check(self, tmp_path):
        (tmp_path / "a.v").write_text("wire a;\n")
        p = ControlPolicies.from_dict({"drive": {"x": "1'b0", "y": "z"}, "verilog": {"before_algo": ["a.v"]},
                                       "config_words": {"c": {"register": "r", "word": 0}}}, base=tmp_path)
        assert p.raw_entries() == {"drive": 2, "verilog": 1}

    def test_a_register_maps_blobfish_export_is_a_payload_policy(self, wide):
        from forge.integration.registers import RegisterMap, to_blobfish
        regs = RegisterMap.from_dict({"registers": [
            {"name": "ctrl", "kind": "config", "width": 64, "port": "cfg_ctrl"},
            {"name": "lock", "kind": "status", "width": 16, "net": "lock_status"}]})
        content = self._gen(wide, **to_blobfish(regs, slr=2)["control_policies"])
        assert ".cfg_ctrl (slr2_config_registers_in[64*0 +: 64])" in content
        assert "assign slr2_readback_registers_out = {48'b0, lock_status};" in content

    def test_the_tap_enable_is_declared_before_the_consumers_verilog(self, wide, tmp_path):
        (tmp_path / "late.v").write_text("wire uses_it = rx_tap_en;\n")
        fw, resolved_io = wide
        content = generate_payload_verilog(fw, resolved_io, policies=ControlPolicies.from_dict({
            "reset": "disabled", "verilog": {"before_algo": ["late.v"]},
            "rx_taps": {"enable": {"port": "slr2_usr_ctrl_in", "bit": 0},
                        "taps": [{"name": "t", "rx": {"quad": "slr2_gt_1", "lane": 0},
                                  "tx": {"quad": "slr2_gt_1", "lane": 1}}]}}, base=tmp_path))
        assert content.index("wire rx_tap_en =") < content.index("wire uses_it = rx_tap_en;")
