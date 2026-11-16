"""Blobfish CSP words and EMP link words convert field by field, or refuse."""
from __future__ import annotations

import itertools

import pytest

from forge.integration.csp import CspWord, from_link_word, to_link_word
from forge.integration.emp import LinkWord


def test_the_bit_layout_is_the_payloads():
    w = CspWord.pack(0xABCD, valid=True, first=False, last=True)
    assert w.word == (0xABCD << 3) | 0b101
    assert (w.data, w.valid, w.first, w.last) == (0xABCD, True, False, True)


@pytest.mark.parametrize("flags", list(itertools.product([False, True], repeat=4)))
def test_every_flag_combination_round_trips(flags):
    valid, start, last, orbit = flags
    word = LinkWord(0xFFFF_FFFF_FFFF_FFFF, valid=valid, start=start, last=last, start_of_orbit=orbit)
    assert to_link_word(from_link_word(word)) == word


def test_strobe_zero_has_no_csp_form():
    with pytest.raises(ValueError, match="no strobe"):
        from_link_word(LinkWord(1, valid=True, strobe=False))


@pytest.mark.parametrize("make", [lambda: CspWord(1 << 67), lambda: CspWord.pack(1 << 64, valid=True, first=False, last=False)])
def test_out_of_range_words_are_refused(make):
    with pytest.raises(ValueError):
        make()
