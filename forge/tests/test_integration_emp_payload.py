"""emp_payload generated for the emp-fwk release it is built with (forge.integration.emp_payload)."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.integration.emp_payload import ENTITY_FILE, EmpRelease, generate

_CONTEXT = """library IEEE;
use IEEE.STD_LOGIC_1164.all;
use work.ipbus.all;
use work.emp_data_types.all;
"""
_V09 = """  port(
    clk         : in  std_logic;        -- ipbus signals
    ipb_out     : out ipb_rbus;
    clk_p       : in  std_logic;
    rst_loc     : in  std_logic_vector(N_REGION - 1 downto 0);
    bc0         : out std_logic;
    d           : in  ldata(4 * N_REGION - 1 downto 0);
    q           : out ldata(4 * N_REGION - 1 downto 0);
    gpio        : out std_logic_vector(29 downto 0);
    slink_q : out slink_input_data_quad_array(SLINK_MAX_QUADS-1 downto 0)
    );
"""
_V10 = _V09.replace("    bc0         : out std_logic;\n", "    ttc2        : in tcds2_streams_pkg.tcds2_ttc2;\n").replace(
    "(SLINK_MAX_QUADS-1 downto 0)\n    );",
    "(SLINK_MAX_QUADS-1 downto 0);\n    tts_busy    : out std_logic;\n"
    "    ext_lvds_out : out std_logic_vector(N_LVDS_OUT - 1 downto 0)\n    );")
_IR = {"design": {"top_ports": [
    {"name": "ap_clk", "direction": "in", "width": 1}, {"name": "ap_rst", "direction": "in", "width": 1},
    {"name": "hit", "direction": "in", "width": 32}, {"name": "hit_valid", "direction": "in", "width": 1},
    {"name": "word", "direction": "out", "width": 32}, {"name": "word_valid", "direction": "out", "width": 1},
]}}
_SPEC = {
    "reset": {"source": "rst_loc(0)"},
    "inputs": {"hit": {"link": 2, "field": "data", "bits": [31, 0]}, "hit_valid": {"link": 2, "field": "valid"}},
    "outputs": {5: {"data": "word", "valid": "word_valid"}},
}


def _release(tmp_path: Path, ports: str) -> EmpRelease:
    path = tmp_path / ENTITY_FILE
    path.parent.mkdir(parents=True)
    path.write_text(f"-- null_algo\n{_CONTEXT}\nentity emp_payload is\n{ports}\nend emp_payload;\n\narchitecture rtl of emp_payload is\nbegin\nend rtl;\n")
    return EmpRelease.read(tmp_path)


def test_the_entity_is_the_releases_own(tmp_path):
    text = generate(_SPEC, _release(tmp_path, _V09), _IR)
    assert "    bc0         : out std_logic;" in text
    assert _CONTEXT.strip().splitlines()[-1] in text
    assert "entity emp_payload is" in text and "architecture forge of emp_payload is" in text


@pytest.mark.parametrize("ports, ties, absent", [
    (_V09, ["bc0 <= '0';"], ["tts_busy", "ext_lvds_out"]),
    (_V10, ["tts_busy <= '0';", "ext_lvds_out <= (others => '0');"], ["bc0"]),
])
def test_each_release_gets_its_own_tie_offs(tmp_path, ports, ties, absent):
    text = generate(_SPEC, _release(tmp_path, ports), _IR)
    for tie in ties + ["ipb_out <= IPB_RBUS_NULL;", "gpio <= (others => '0');",
                       "slink_q <= (others => SLINK_INPUT_DATA_ARRAY_NULL);"]:
        assert f"  {tie}" in text
    arch = text.split("architecture forge")[1]
    assert not any(name in arch for name in absent)


def test_links_map_onto_the_algorithm_ports(tmp_path):
    text = generate(_SPEC, _release(tmp_path, _V10), _IR)
    assert "      ap_clk => clk_p" in text and "      ap_rst => rst_loc(0)" in text
    assert "      hit => d(2).data(31 downto 0)" in text and "      hit_valid => d(2).valid" in text
    assert "  q(5).data <= (63 downto 32 => '0') & algo_word;" in text
    assert "  q(5).valid <= algo_word_valid;" in text and "  q(5).strobe <= '1';" in text
    assert "    idle : if i /= 5 generate" in text


def test_drive_replaces_a_tie_off(tmp_path):
    text = generate({**_SPEC, "drive": {"gpio": "(0 => clk_p, others => '0')"}}, _release(tmp_path, _V09), _IR)
    assert "  gpio <= (0 => clk_p, others => '0');" in text and "gpio <= (others => '0')" not in text


@pytest.mark.parametrize("change, message", [
    ({"inputs": {"hit_valid": {"link": 2, "field": "valid"}}}, "hit has no link field"),
    ({"inputs": {**_SPEC["inputs"], "hit": {"link": 2, "bits": [15, 0]}}}, "16 wide, the port 32"),
    ({"inputs": {**_SPEC["inputs"], "ghost": {"link": 1}}}, "ghost"),
    ({"outputs": {0: {"data": "hit"}}}, "hit is not an output"),
    ({"reset": None}, "reset is required"),
    ({"drive": {"q": "x"}}, "not payload outputs"),
    ({"emp_fwk": "v0.10.3"}, "the spec is for emp-fwk v0.10.3"),
])
def test_a_mapping_that_does_not_fit_is_refused(tmp_path, change, message):
    with pytest.raises(ValueError, match=message):
        generate({**_SPEC, **change}, _release(tmp_path, _V10), _IR)


def test_an_output_with_no_known_tie_off_is_refused(tmp_path):
    ports = _V10.replace("(N_LVDS_OUT - 1 downto 0)\n    );", "(N_LVDS_OUT - 1 downto 0);\n    mystery : out some_record_t\n    );")
    with pytest.raises(ValueError, match="no tie-off is known for output mystery"):
        generate(_SPEC, _release(tmp_path, ports), _IR)
