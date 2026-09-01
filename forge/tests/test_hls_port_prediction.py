"""The HLS port predictor, checked against real Vitis HLS output.

`forge/tests/fixtures/hls_port_matrix/` holds three C++ top functions
covering the interface/pragma combinations, and `golden_ports.json` holds
the exact port list each one synthesises to under Vitis HLS 2024.1. These
tests assert the predictor reproduces that real output — the rules are
derived from synthesis, not from documentation, so this fixture is the
specification.

Regenerating the golden data (needs vitis_hls on PATH):
    python -m forge.tests.fixtures.hls_port_matrix.regenerate
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge.hls.port_prediction import Argument, predict_ports

FIXTURE = Path(__file__).parent / "fixtures" / "hls_port_matrix"
GOLDEN = json.loads((FIXTURE / "golden_ports.json").read_text())


def _real(top: str) -> dict:
    """Real synthesised {name: (direction, width)} for a fixture top.

    A recorded width is the Verilog slice text: "47:0" for a vector, "1" for
    a bare bit, or a parameterised expression like
    "C_M_AXI_GMEM_ADDR_WIDTH - 1:0" whose value isn't known until synthesis —
    recorded as None rather than guessed.
    """
    out = {}
    for port in GOLDEN[top]:
        text = port["width"].strip()
        if ":" not in text:
            width = 1
        else:
            high = text.split(":", 1)[0].strip()
            width = int(high) + 1 if high.isdigit() else None
        out[port["name"]] = (port["direction"], width)
    return out


# ── m_scalars: ap_ctrl_none + every scalar interface mode ──────────────────

def test_scalar_modes_match_real_synthesis():
    real = _real("m_scalars")

    pred = predict_ports(
        [
            Argument("in_none", 12, mode="ap_none"),
            Argument("in_vld", 12, mode="ap_vld"),
            Argument("in_ack", 12, mode="ap_ack"),
            Argument("in_hs", 12, mode="ap_hs"),
            Argument("in_stable", 12, mode="ap_stable"),
            Argument("in_signed", 8, mode="ap_none"),
            Argument("out_none", 12, is_output=True, mode="ap_none"),
            Argument("out_vld", 12, is_output=True, mode="ap_vld"),
            Argument("out_ack", 12, is_output=True, mode="ap_ack"),
            Argument("out_hs", 12, is_output=True, mode="ap_hs"),
        ],
        block_protocol="ap_ctrl_none",
    )

    assert {p.name for p in pred.ports} == set(real)
    for p in pred.ports:
        assert p.direction == real[p.name][0], f"{p.name} direction"


def test_ap_ctrl_none_emits_no_handshake_ports():
    pred = predict_ports([], block_protocol="ap_ctrl_none")
    assert [p.name for p in pred.ports] == ["ap_clk", "ap_rst"]


def test_handshake_side_channels_follow_the_data_direction():
    """On an input, _ap_vld is an input and _ap_ack an output; on an output
    they swap. Taken from the synthesised fixture, not assumed."""
    real = _real("m_scalars")

    assert real["in_hs_ap_vld"][0] == "input"
    assert real["in_hs_ap_ack"][0] == "output"
    assert real["out_hs_ap_vld"][0] == "output"
    assert real["out_hs_ap_ack"][0] == "input"

    pred = predict_ports([
        Argument("in_hs", 12, mode="ap_hs"),
        Argument("out_hs", 12, is_output=True, mode="ap_hs"),
    ], block_protocol="ap_ctrl_none").by_name

    assert pred["in_hs_ap_vld"].direction == "input"
    assert pred["in_hs_ap_ack"].direction == "output"
    assert pred["out_hs_ap_vld"].direction == "output"
    assert pred["out_hs_ap_ack"].direction == "input"


# ── m_arrays: ap_ctrl_hs + array shapes ────────────────────────────────────

def test_array_shapes_match_real_synthesis():
    real = _real("m_arrays")

    pred = predict_ports(
        [
            Argument("grid_full", 12, dims=(3, 4), mode="ap_none", partition_dim=0),
            Argument("grid_dim1", 12, dims=(3, 4), partition_dim=1),
            Argument("line_mem", 12, dims=(4,)),
            Argument("line_fifo", 12, dims=(4,), mode="ap_fifo"),
            Argument("st_in", 48, is_struct=True, mode="ap_none"),
            Argument("line_out", 12, dims=(4,), is_output=True),
        ],
        block_protocol="ap_ctrl_hs",
        returns_value=True,
        return_width=48,
    )

    assert {p.name for p in pred.ports} == set(real)
    for p in pred.ports:
        assert p.direction == real[p.name][0], f"{p.name} direction"


def test_complete_partition_dim0_scalarises_every_dimension():
    """`ARRAY_PARTITION complete dim=0` on hit_t[3][4] gives 12 scalars."""
    pred = predict_ports(
        [Argument("grid", 12, dims=(3, 4), mode="ap_none", partition_dim=0)],
        block_protocol="ap_ctrl_none",
    )
    names = {p.name for p in pred.ports if p.role_hint == "grid"}
    assert names == {f"grid_{i}_{j}" for i in range(3) for j in range(4)}


def test_partition_dim1_leaves_the_inner_dimension_as_a_memory():
    """The distinction that produced two completely different port sets for
    the same hit_t[18][14] argument in this repository's own history: a bare
    `complete` defaults to dim=1, leaving the inner dimension a BRAM."""
    pred = predict_ports(
        [Argument("grid", 12, dims=(3, 4), partition_dim=1)],
        block_protocol="ap_ctrl_none",
    )
    names = {p.name for p in pred.ports if p.role_hint == "grid"}
    assert names == {
        f"grid_{i}_{sig}" for i in range(3) for sig in ("address0", "ce0", "q0")
    }


def test_written_array_gets_we_and_d_instead_of_q():
    pred = predict_ports(
        [Argument("out_mem", 12, dims=(4,), is_output=True)],
        block_protocol="ap_ctrl_none",
    ).by_name
    assert "out_mem_we0" in pred and "out_mem_d0" in pred
    assert "out_mem_q0" not in pred


def test_memory_address_width_follows_depth():
    pred = predict_ports([Argument("m", 8, dims=(4,))], block_protocol="ap_ctrl_none").by_name
    assert pred["m_address0"].width == 2
    pred = predict_ports([Argument("m", 8, dims=(64,))], block_protocol="ap_ctrl_none").by_name
    assert pred["m_address0"].width == 6


# ── m_axi: streams and the interfaces we refuse to predict ─────────────────

def test_axis_stream_ports_match_real_synthesis():
    real = _real("m_axi")

    pred = predict_ports([
        Argument("strm_in", 12, dims=(1,), mode="axis"),
        Argument("strm_out", 12, dims=(1,), is_output=True, mode="axis"),
    ]).by_name

    for name in ("strm_in_TDATA", "strm_in_TVALID", "strm_in_TREADY",
                 "strm_out_TDATA", "strm_out_TVALID", "strm_out_TREADY"):
        assert name in pred and name in real
        assert pred[name].direction == real[name][0], name


def test_axis_tdata_is_rounded_up_to_a_byte_multiple():
    """A 12-bit element becomes a 16-bit TDATA — confirmed in the fixture."""
    assert _real("m_axi")["strm_in_TDATA"][1] == 16
    pred = predict_ports([Argument("s", 12, dims=(1,), mode="axis")]).by_name
    assert pred["s_TDATA"].width == 16


def test_axi_interfaces_flip_the_reset_to_active_low():
    """Adding an AXI interface renames ap_rst to ap_rst_n. Nothing about the
    argument list says so — it is a whole-block consequence."""
    real = _real("m_axi")
    assert "ap_rst_n" in real and "ap_rst" not in real

    pred = predict_ports([Argument("s", 12, dims=(1,), mode="axis")])
    assert "ap_rst_n" in pred.by_name
    assert any("active-low" in w for w in pred.warnings)


@pytest.mark.parametrize("mode", ["s_axilite", "m_axi"])
def test_unpredictable_interfaces_are_reported_not_guessed(mode):
    """Their port sets depend on widths chosen at synthesis, so the
    predictor declines rather than emitting something plausible-looking."""
    pred = predict_ports([Argument("p", 12, dims=(4,), mode=mode)])

    assert not [p for p in pred.ports if p.role_hint == "p"]
    assert any(mode in w and "not predicted" in w for w in pred.warnings)


def test_struct_width_is_flagged_rather_than_summed():
    """packed_t is 16 bits of declared fields and synthesises to 48: HLS pads
    members. A summed width would be wrong in a way that looks right."""
    assert _real("m_arrays")["st_in"][1] == 48

    pred = predict_ports([Argument("st", 16, is_struct=True, mode="ap_none")])
    assert any("struct width is not predicted" in w for w in pred.warnings)


# ── block protocols ────────────────────────────────────────────────────────

def test_ap_ctrl_hs_control_ports_match_real_synthesis():
    real = _real("m_arrays")
    for name in ("ap_start", "ap_done", "ap_idle", "ap_ready"):
        assert name in real
    pred = predict_ports([], block_protocol="ap_ctrl_hs").by_name
    assert pred["ap_start"].direction == "input"
    assert pred["ap_done"].direction == "output"


def test_ap_ctrl_chain_adds_ap_continue():
    pred = predict_ports([], block_protocol="ap_ctrl_chain").by_name
    assert pred["ap_continue"].direction == "input"


def test_memory_interfaces_warn_that_a_second_port_set_may_appear():
    """Validated against a real module: predicting inputProcessor's ports
    produced zero spurious names, and every port it missed was the second
    memory port set HLS added because the pipeline needs two accesses per
    cycle. That is a scheduler decision, so it is flagged, not invented."""
    pred = predict_ports(
        [Argument("hits_in", 21, dims=(18, 14), partition_dim=1)],
        block_protocol="ap_ctrl_hs",
    )

    assert any("second port set" in w for w in pred.warnings)
    # Exactly one warning for the argument, not one per generated memory.
    assert sum("second port set" in w for w in pred.warnings) == 1
    assert "hits_in_0_address0" in pred.by_name


def test_reference_output_defaults_to_ap_vld_not_ap_none():
    """With no INTERFACE pragma, a by-value input is ap_none but a
    by-reference output is ap_vld. Found on inputProcessor, which declares
    no INTERFACE pragmas and still has a proc_out_ap_vld port."""
    pred = predict_ports(
        [Argument("din", 12), Argument("dout", 12, is_output=True)],
        block_protocol="ap_ctrl_none",
    ).by_name

    assert "din_ap_vld" not in pred
    assert pred["dout_ap_vld"].direction == "output"


def test_explicit_ap_none_on_an_output_still_wins():
    pred = predict_ports(
        [Argument("dout", 12, is_output=True, mode="ap_none")],
        block_protocol="ap_ctrl_none",
    ).by_name
    assert "dout_ap_vld" not in pred


# ── the partitioning default, and why it costs ports ───────────────────────

def test_bare_complete_partitions_only_dim1_not_every_dimension():
    """`ARRAY_PARTITION complete` with no `dim=` means dim=1, not dim=0.

    m_dimdefault.cpp puts both spellings on the same word_t[3][4] in one
    function. The bare form leaves depth-4 memories (18 ports: 3 memories x
    address0/ce0/q0 + a second port set); `dim=0` scalarises everything
    (12 plain wires). This one-word difference is why two identically
    shaped hit_t[18][14] arguments in this project produced completely
    different port sets.
    """
    real = _real("m_dimdefault")

    bare = {n for n in real if n.startswith("a_bare")}
    dim0 = {n for n in real if n.startswith("b_dim0")}

    assert dim0 == {f"b_dim0_{i}_{j}" for i in range(3) for j in range(4)}
    assert bare == {
        f"a_bare_{i}_{sig}"
        for i in range(3)
        for sig in ("address0", "ce0", "q0", "address1", "ce1", "q1")
    }

    pred = predict_ports(
        [Argument("b_dim0", 12, dims=(3, 4), mode="ap_none", partition_dim=0)],
        block_protocol="ap_ctrl_none",
    )
    assert {p.name for p in pred.ports if p.role_hint == "b_dim0"} == dim0


def test_the_second_port_set_is_what_the_predictor_warns_about():
    """a_bare's `_address1/_ce1/_q1` are the ports the predictor knowingly
    omits: HLS adds them because the unrolled body needs more reads per
    cycle than one BRAM port supplies. That depends on the function body,
    which a signature-level prediction never sees."""
    real = _real("m_dimdefault")
    second = {n for n in real if n.endswith(("address1", "ce1", "q1"))}
    assert second, "fixture should contain a second port set"

    pred = predict_ports(
        [Argument("a_bare", 12, dims=(3, 4), partition_dim=1)],
        block_protocol="ap_ctrl_none",
    )

    assert not ({p.name for p in pred.ports} & second)
    assert any("second port set" in w for w in pred.warnings)
