"""The C++ front end: reading an HLS top function's interface from source.

Together with forge.hls.port_prediction this predicts an unbuilt HLS
module's RTL ports. The fixture in fixtures/hls_port_matrix/ holds the real
synthesised output these tests check against.
"""

from __future__ import annotations

import json
from pathlib import Path

from forge.hls.cpp_signature import parse_signature
from forge.hls.port_prediction import predict_ports

FIXTURE = Path(__file__).parent / "fixtures" / "hls_port_matrix"
SRC = FIXTURE / "src"
GOLDEN = json.loads((FIXTURE / "golden_ports.json").read_text())


def _real(top: str) -> dict:
    out = {}
    for p in GOLDEN[top]:
        t = p["width"].strip()
        if ":" not in t:
            w = 1
        else:
            hi = t.split(":", 1)[0].strip()
            w = int(hi) + 1 if hi.isdigit() else None
        out[p["name"]] = (p["direction"], w)
    return out


def test_reads_block_protocol_from_the_return_pragma():
    sig = parse_signature(SRC / "m_scalars.cpp", "m_scalars")
    assert sig.block_protocol == "ap_ctrl_none"
    assert not sig.returns_value


def test_reads_every_scalar_mode_and_direction():
    sig = parse_signature(SRC / "m_scalars.cpp", "m_scalars")
    by = {a.name: a for a in sig.args}

    assert by["in_vld"].mode == "ap_vld"
    assert by["in_hs"].mode == "ap_hs"
    assert by["in_none"].is_output is False
    assert by["out_hs"].is_output is True
    assert by["in_none"].width == 12        # word_t -> ap_uint<12>
    assert by["in_signed"].width == 8       # small_t -> ap_int<8>


def test_scalar_source_predicts_the_real_port_list():
    """End to end: source -> signature -> prediction -> real synthesis."""
    sig = parse_signature(SRC / "m_scalars.cpp", "m_scalars")
    pred = predict_ports(sig.args, block_protocol=sig.block_protocol)
    real = _real("m_scalars")

    assert {p.name for p in pred.ports} == set(real)
    for p in pred.ports:
        assert (p.direction, p.width) == real[p.name], p.name


def test_resolves_typedef_array_dimensions_through_defines():
    """grid_t is word_t[ROWS][COLS] with ROWS/COLS as #defines."""
    sig = parse_signature(SRC / "m_arrays.cpp", "m_arrays")
    by = {a.name: a for a in sig.args}

    assert by["grid_full"].dims == (3, 4)
    assert by["grid_full"].width == 12


def test_reads_the_array_partition_dim_including_the_bare_default():
    """A bare `complete` is dim=1; `complete dim=0` is every dimension."""
    sig = parse_signature(SRC / "m_dimdefault.cpp", "m_dimdefault")
    by = {a.name: a for a in sig.args}

    assert by["a_bare"].partition_dim == 1
    assert by["b_dim0"].partition_dim == 0


def test_struct_as_an_array_element_gets_the_summed_width():
    """HLS bit-packs a struct used as an array element, so the field sum is
    exact. m_structs.cpp's as_array_elem synthesises to 16 bits."""
    sig = parse_signature(SRC / "m_structs.cpp", "m_structs")
    by = {a.name: a for a in sig.args}

    assert by["as_array_elem"].is_struct
    assert by["as_array_elem"].width == 16
    assert _real("m_structs")["as_array_elem_0_0"][1] == 16


def test_struct_as_a_scalar_is_left_unknown_because_hls_pads_it():
    """The same struct is 48 bits as a scalar argument — padded, not summed.
    A summed width would be wrong in a way that looks right."""
    sig = parse_signature(SRC / "m_structs.cpp", "m_structs")
    by = {a.name: a for a in sig.args}

    assert by["as_scalar"].is_struct
    assert by["as_scalar"].width == 0            # unknown, not guessed
    assert _real("m_structs")["as_scalar"][1] == 48
    assert any("as_scalar" in w for w in sig.warnings)


def test_struct_return_width_is_reported_unknown():
    sig = parse_signature(SRC / "m_structs.cpp", "m_structs")
    assert sig.returns_value
    assert sig.return_is_struct
    assert sig.return_width is None
    assert any("ap_return" in w for w in sig.warnings)


def test_missing_top_function_is_reported_not_crashed(tmp_path):
    f = tmp_path / "x.cpp"
    f.write_text("void other(int a) {}\n")

    sig = parse_signature(f, "nope")

    assert sig.args == []
    assert any("not found" in w for w in sig.warnings)


def test_unresolvable_width_is_reported_not_guessed(tmp_path):
    f = tmp_path / "x.cpp"
    f.write_text("void top(mystery_t a) {\n#pragma HLS INTERFACE ap_none port=a\n}\n")

    sig = parse_signature(f, "top")

    assert sig.args[0].width == 0
    assert any("could not resolve the width" in w for w in sig.warnings)


def test_resolves_constants_declared_as_const_int(tmp_path):
    """HLS headers use `static const int W = 32;` as often as #define, and
    the width is unresolvable without it."""
    (tmp_path / "types.h").write_text("static const int W = 24;\n")
    f = tmp_path / "m.cpp"
    f.write_text('#include "types.h"\n'
                 "void m(ap_uint<W> a) {\n#pragma HLS INTERFACE ap_none port=a\n}\n")

    sig = parse_signature(f, "m")

    assert sig.args[0].width == 24
    assert sig.warnings == []


def test_follows_includes_transitively(tmp_path):
    """A top's .cpp includes its own header, which includes the project's
    shared types header — the widths live in that third file. Found on
    trigger_demo, where one level of include resolution left every width
    unknown."""
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "types.h").write_text("#define W 18\n")
    (tmp_path / "mod").mkdir()
    (tmp_path / "mod" / "m.h").write_text('#include "types.h"\n')
    f = tmp_path / "mod" / "m.cpp"
    f.write_text('#include "m.h"\n'
                 "void m(ap_uint<W> a) {\n#pragma HLS INTERFACE ap_none port=a\n}\n")

    sig = parse_signature(f, "m")

    assert sig.args[0].width == 18


def test_predicts_a_real_unbuilt_hls_module_matching_its_hand_written_contract():
    """The end this feature exists for: an HLS module's contract can be
    drafted before the IP is built. Predicted from trigger_demo's C++ alone,
    every role matches the contract that was authored against the built IP.
    """
    import yaml
    repo = Path(__file__).resolve().parents[2]
    cpp = repo / "plugins/trigger_demo/algo/hit_decoder/hit_decoder.cpp"
    contract = repo / "plugins/trigger_demo/forge/interfaces/hit_decoder_ip.interface.yaml"

    sig = parse_signature(cpp, "hit_decoder")
    pred = predict_ports(sig.args, block_protocol=sig.block_protocol,
                         returns_value=sig.returns_value,
                         return_width=sig.return_width).by_name
    hand = yaml.safe_load(contract.read_text())["ip_interface"]["roles"]

    assert sig.warnings == []
    for role, spec in hand.items():
        port = spec.get("raw_port", role)
        assert port in pred, f"{role} -> {port} not predicted"
        assert pred[port].direction == spec["direction"], role
        assert pred[port].width == spec["width"], role
