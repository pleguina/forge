"""Tests for emp_tools.py; the XSim run itself is run_emp_sim.sh."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import emp_tools  # noqa: E402
from emp_tools import check_entity, check_sink, parse_golden_xml, write_source  # noqa: E402
from forge.integration.emp import IDLE, LinkWord, entity_ports, packet, read_pattern_file, write_pattern_file  # noqa: E402


@pytest.fixture
def events():
    return parse_golden_xml(emp_tools._GOLDEN_XML)


def _sink(tmp_path: Path, link0: list) -> Path:
    path = tmp_path / "sink.txt"
    write_pattern_file(path, [{0: word} for word in link0], [0, 1, 2, 3])
    return path


class TestSource:
    def test_one_frame_per_event_then_the_gap(self, events, tmp_path):
        out = tmp_path / "source.txt"
        assert write_source(events, out, gap=3) == 4 * len(events)
        links = read_pattern_file(out)
        assert sorted(links) == [0, 1, 2, 3]
        for link, words in links.items():
            for event, word in zip(events, words[::4]):
                ch = event.channels[link]
                assert word == (packet(ch.raw) if ch.valid else IDLE)
            assert all(w == IDLE for i, w in enumerate(words) if i % 4)


class TestCheck:
    def _words(self, events):
        return [packet(e.golden.word) for e in events if e.golden.n_hits > 0]

    def test_golden_words_pass(self, events, tmp_path):
        assert check_sink(events, _sink(tmp_path, [IDLE] * 10 + self._words(events))) == []

    def test_wrong_word_fails(self, events, tmp_path):
        words = self._words(events)
        words[1] = packet(0xDEAD)
        errors = check_sink(events, _sink(tmp_path, words))
        assert len(errors) == 1 and "event 1" in errors[0]

    def test_missing_word_fails(self, events, tmp_path):
        errors = check_sink(events, _sink(tmp_path, self._words(events)[:-1]))
        assert errors and "2 valid output frames, expected 3" in errors[0]

    def test_invalid_frames_are_ignored(self, events, tmp_path):
        assert check_sink(events, _sink(tmp_path, [LinkWord(0x1234)] + self._words(events))) == []


class TestEntity:
    def test_payload_declares_the_emp_ports(self):
        names = [p.split(":")[0] for p in entity_ports(emp_tools._PAYLOAD_VHD.read_text())]
        assert names == [
            "clk", "rst", "ipb_in", "ipb_out", "clk40", "clk_payload", "rst_payload", "clk_p",
            "rst_loc", "clken_loc", "ctrs", "bc0", "d", "q", "gpio", "gpio_en", "slink_q",
            "backpressure",
        ]

    def test_a_different_emp_fwk_entity_is_reported(self, tmp_path):
        hdl = tmp_path / "components" / "payload" / "firmware" / "hdl"
        hdl.mkdir(parents=True)
        ours = emp_tools._PAYLOAD_VHD.read_text()
        (hdl / "emp_payload.vhd").write_text(ours)
        assert check_entity(tmp_path) == []
        (hdl / "emp_payload.vhd").write_text(ours.replace("clk40       : in  std_logic;", ""))
        assert any(line.startswith("+clk40") for line in check_entity(tmp_path))
