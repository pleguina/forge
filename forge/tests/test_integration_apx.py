"""APx pattern files (forge.integration.apx) and the apx dataset formats."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.integration.apx import is_apx_file, read_apx, sideband, word, write_apx
from forge.integration.emp import BoardData, LinkWord, packet
from forge.verification.dataset_format import ApxPatternDatasetLoader, get_format_loader

# As cms-sw/cmssw fdcffa67b8e writeAPxFile lays it out: links 0 and 3, three frames.
_CMSSW = (
    "#Sideband ON\n"
    "#LinkLabel                LINK_00                    LINK_03    \n"
    "#BeginData\n"
    "0x0000    0x0b 0x00000000000000aa    0x00 0x0000000000000000\n"
    "0x0001    0x0c 0x00000000000000bb    0x0e 0x0000000000000ccc\n"
    "0x0002    0x00 0x0000000000000000    0x00 0x0000000000000000\n"
)


def test_a_file_written_by_cmssw_reads_with_the_current_sideband(tmp_path):
    path = tmp_path / "rx.txt"
    path.write_text(_CMSSW)
    board = read_apx(path)
    assert sorted(board.links) == [0, 3]
    assert board.links[0][0] == LinkWord(0xAA, valid=True, start=True, start_of_orbit=True)
    assert board.links[0][1] == LinkWord(0xBB, valid=True, last=True)
    assert board.links[3][1] == LinkWord(0xCCC, valid=True, start=True, last=True)
    assert is_apx_file(path)


def test_write_then_read_is_lossless_and_matches_cmssws_layout(tmp_path):
    data = BoardData("", {0: [LinkWord(0xAA, valid=True, start=True, start_of_orbit=True),
                              LinkWord(0xBB, valid=True, last=True), LinkWord()],
                          3: [LinkWord(), packet(0xCCC), LinkWord()]})
    path = tmp_path / "out.txt"
    write_apx(path, data)
    assert path.read_text() == _CMSSW
    assert read_apx(path).links == data.links


@pytest.mark.parametrize("encoding", ["2025", "2021"])
def test_each_encoding_round_trips_every_flag(encoding):
    for w in (LinkWord(1, valid=True), LinkWord(2, start=True), LinkWord(3, last=True),
              LinkWord(4, start_of_orbit=True)):
        assert word(sideband(w, encoding), w.data, encoding) == w


def test_the_2021_encoding_is_cmssws_reader():
    assert sideband(LinkWord(valid=True, start=True, last=True), "2021") == 0b1011


@pytest.mark.parametrize("call, message", [
    (lambda: sideband(LinkWord(strobe=False)), "no strobe"),
    (lambda: word(0x18, 0), "CRC or throttle"),
    (lambda: sideband(LinkWord(), "2019"), "encoding '2019'"),
])
def test_what_apx_or_a_link_word_cannot_hold_is_refused(call, message):
    with pytest.raises(ValueError, match=message):
        call()


@pytest.mark.parametrize("text, message", [
    ("#BeginData\n", "no '#LinkLabel'"),
    ("#LinkLabel LINK_00\n0x0000 0x00 0x0\n", "no '#BeginData'"),
    ("#LinkLabel LANE_0\n#BeginData\n", "LINK_<n>"),
    ("#LinkLabel LINK_00\n#BeginData\n0x0001 0x00 0x0\n", "expected row 0"),
    ("#LinkLabel LINK_00\n#BeginData\n0x0000 0x00\n", "2 tokens, expected 3"),
])
def test_malformed_files_are_refused(tmp_path, text, message):
    path = tmp_path / "bad.txt"
    path.write_text(text)
    with pytest.raises(ValueError, match=message):
        read_apx(path)


def test_an_apx_dataset_has_an_event_per_packet_and_is_known_by_content(tmp_path):
    path = tmp_path / "rx.txt"
    path.write_text(_CMSSW)
    loader = get_format_loader(path)
    assert isinstance(loader, ApxPatternDatasetLoader) and loader.adapter_id == "apx"
    data = loader.load(path)
    assert data.metadata.event_ids == ["0"]
    assert [w["data"] for w in data.events[0]["links"]["0"]] == ["0x00000000000000aa", "0x00000000000000bb"]
    assert get_format_loader(Path(path), "apx-2021").adapter_id == "apx-2021"
