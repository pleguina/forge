"""Object fields as link words: one codec packs a reference export and reads outputs back."""

from __future__ import annotations

import json

import pytest

from forge.integration.emp import read_board_data
from forge.integration.objects import Codec, codec_decoder, write_patterns

SPEC = {"width": 32, "fields": {"valid": {"low": 0, "bits": 1, "value": 1}, "pt": {"low": 1, "bits": 8},
                                "phi": {"low": 9, "bits": 8, "signed": True}}}


def test_a_word_packs_and_reads_back_with_signed_fields_and_constants():
    codec = Codec.from_spec(SPEC, "mu")
    word = codec.pack({"pt": 200, "phi": -3, "ignored": 9})

    assert word == 1 | 200 << 1 | (256 - 3) << 9
    assert codec.unpack(word) == {"valid": 1, "pt": 200, "phi": -3}
    assert codec.unpack(0) is None


def test_flags_below_the_data_are_left_out():
    codec = Codec.from_spec({**SPEC, "shift": 3}, "mu")

    assert codec.unpack(codec.pack({"pt": 5}) | 0b101) == {"valid": 1, "pt": 5, "phi": 0}


@pytest.mark.parametrize("change, message", [
    ({"fields": {"a": {"low": 0, "bits": 4}, "b": {"low": 3, "bits": 2}}}, "fields a and b overlap"),
    ({"fields": {"a": {"low": 30, "bits": 4}}}, "does not fit 32 bits"),
    ({"fields": {"a": {"bits": 4}}}, "want {low: N, bits: N}"),
])
def test_a_wrong_layout_is_named(change, message):
    with pytest.raises(ValueError, match=message):
        Codec.from_spec({**SPEC, **change}, "mu")


def test_a_value_that_does_not_fit_is_an_error():
    with pytest.raises(ValueError, match="phi = 200 does not fit 8 signed bits"):
        Codec.from_spec(SPEC, "mu").pack({"phi": 200})


def test_a_decoder_finds_the_codec_by_glob():
    decode = codec_decoder({"q*": Codec.from_spec(SPEC, "mu")})

    assert decode("q3", 1 | 7 << 1)["pt"] == 7
    with pytest.raises(ValueError, match="no codec for z"):
        decode("z", 1)


def test_every_link_gets_a_packet_per_event_padded_to_the_longest(tmp_path):
    codec = Codec.from_spec(SPEC, "mu")
    events = [{"id": 7, "mu": [{"link": 0, "pt": 1}, {"link": 0, "pt": 2}, {"link": 1, "pt": 3}]},
              {"id": 8, "mu": []}]

    n, count = write_patterns(tmp_path / "p.txt", events, objects=lambda e: e["mu"], codec=codec,
                              link_of=lambda o: o["link"], links=[0, 1], names=tmp_path / "names.json")

    board = read_board_data(tmp_path / "p.txt")
    assert (n, count) == (2, 3)
    assert [[codec.unpack(w.data) and codec.unpack(w.data)["pt"] for w in p] for p in board.packets(1)] == [[3, None], [None]]
    assert json.loads((tmp_path / "names.json").read_text()) == [7, 8]
