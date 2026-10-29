"""EMP pattern files and payload port lists."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.integration.emp import IDLE, LinkWord, entity_ports, packet, read_pattern_file, write_pattern_file

# The first frames of emp-fwk v0.9.3 tests/io_data/counter_packet64gap32_ku115/input.txt,
# links 0 and 1.
EMP_FWK_INPUT = """\
ID: x0
Metadata: (strobe,) start of orbit, start of packet, end of packet, valid

      Link              000                    001
Frame 0000    1101 0000000000000000  1101 0000000000010000
Frame 0001    0001 0000000000000001  0001 0000000000010001
Frame 0002    0011 0000000000000002  0011 0000000000010002
"""


def test_reads_emp_fwks_own_pattern_file(tmp_path: Path) -> None:
    path = tmp_path / "input.txt"
    path.write_text(EMP_FWK_INPUT)

    links = read_pattern_file(path)

    assert sorted(links) == [0, 1]
    assert links[0][0] == LinkWord(0, valid=True, start=True, last=False, start_of_orbit=True)
    assert links[1][1] == LinkWord(0x10001, valid=True)
    assert links[0][2] == LinkWord(2, valid=True, last=True)


def test_written_frames_read_back(tmp_path: Path) -> None:
    frames = [{0: packet(0xA00050), 3: packet(0xC00070)}, {}, {1: LinkWord(5, valid=True, start=True)}]
    path = tmp_path / "source.txt"

    write_pattern_file(path, frames, [0, 1, 3])
    links = read_pattern_file(path)

    assert links[0] == [packet(0xA00050), IDLE, IDLE]
    assert links[1] == [IDLE, IDLE, LinkWord(5, valid=True, start=True)]
    assert links[3] == [packet(0xC00070), IDLE, IDLE]
    assert path.read_text().splitlines()[4] == (
        "Frame 0000    0111 0000000000a00050  0000 0000000000000000  0111 0000000000c00070"
    )


def test_a_short_row_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.txt"
    path.write_text(EMP_FWK_INPUT.replace("  1101 0000000000010000", ""))

    with pytest.raises(ValueError, match="frame 0000 has 1 words, expected 2"):
        read_pattern_file(path)


def test_port_lists_ignore_comments_case_and_spacing() -> None:
    a = "entity emp_payload is\n port(\n  clk : in std_logic; -- ipbus\n  q : out ldata(3 downto 0)\n );\nend emp_payload;"
    b = "ENTITY emp_payload IS PORT(clk: in  std_logic;q : out ldata(3 downto 0)); END;"

    assert entity_ports(a) == entity_ports(b) == ["clk:in std_logic", "q:out ldata(3 downto 0)"]


def test_a_changed_port_shows() -> None:
    old = "entity emp_payload is port(clk : in std_logic; d : in ldata(3 downto 0)); end;"
    new = "entity emp_payload is port(clk : in std_logic; d : in ldata(7 downto 0)); end;"

    assert entity_ports(old) != entity_ports(new)
