"""EMP pattern files."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.integration.emp import (
    IDLE, BoardData, LinkWord, is_pattern_file, packet, read_board_data, read_pattern_file,
    write_board_data, write_pattern_file,
)

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


def test_strobe_is_read_from_five_bits_and_written_only_when_needed(tmp_path: Path) -> None:
    path = tmp_path / "s.txt"
    write_pattern_file(path, [{0: LinkWord(1, valid=True, strobe=False)}, {0: packet(2)}], [0])
    assert path.read_text().splitlines()[4].split()[2] == "00001"
    assert [w.strobe for w in read_pattern_file(path)[0]] == [False, True]
    write_pattern_file(path, [{0: packet(2)}], [0])
    assert path.read_text().splitlines()[4].split()[2] == "0111"


@pytest.mark.parametrize("text", [
    EMP_FWK_INPUT,
    EMP_FWK_INPUT.replace("1101 0000000000000000", "01101 0000000000000000")
                 .replace("1101 0000000000010000", "11101 0000000000010000")
                 .replace("0001 0000000000000001", "10001 0000000000000001")
                 .replace("0001 0000000000010001", "10001 0000000000010001")
                 .replace("0011 0000000000000002", "10011 0000000000000002")
                 .replace("0011 0000000000010002", "10011 0000000000010002"),
])
def test_board_data_round_trips_without_loss(tmp_path: Path, text: str) -> None:
    src, out = tmp_path / "in.txt", tmp_path / "out.txt"
    src.write_text(text)
    data = read_board_data(src)
    assert data.name == "x0"
    write_board_data(out, data)
    assert read_board_data(out) == data


def test_packets_run_from_start_to_last(tmp_path: Path) -> None:
    w = lambda d, **k: LinkWord(d, valid=True, **k)
    data = BoardData(links={0: [w(1, start=True), w(2), IDLE, w(3, last=True), w(4, start=True, last=True),
                                LinkWord(9), w(5, start=True), w(6, start=True, last=True)]})
    assert [[x.data for x in p] for p in data.packets(0)] == [[1, 2, 3], [4], [5], [6]]


def test_a_pattern_file_is_known_by_its_header(tmp_path: Path) -> None:
    emp, other = tmp_path / "a.txt", tmp_path / "b.txt"
    emp.write_text(EMP_FWK_INPUT)
    other.write_text("event 0: 1 2 3\n")
    assert is_pattern_file(emp) and not is_pattern_file(other)
