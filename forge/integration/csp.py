"""Blobfish CSP link words and their exact correspondence with EMP's.

A Blobfish payload carries each link as a 67-bit word ``{data[63:0], last,
first, valid}`` (bit 0 valid, 1 first, 2 last, 66:3 data) and the lane's
orbit marker on its own ``rx_orbit``/``tx_orbit`` port. EMP's ``lword``
(:class:`forge.integration.emp.LinkWord`) has the same data, valid, start
(first) and last, plus ``start_of_orbit`` in the word and ``strobe``. The two
are converted field by field; what the target cannot hold is an error, never
dropped.
"""

from __future__ import annotations

from dataclasses import dataclass

from forge.integration.emp import LinkWord

CSP_WIDTH = 67


@dataclass(frozen=True)
class CspWord:
    word: int        # {data[63:0], last, first, valid}
    orbit: bool = False

    def __post_init__(self) -> None:
        if not 0 <= self.word < 1 << CSP_WIDTH:
            raise ValueError(f"a CSP word is {CSP_WIDTH} bits, got 0x{self.word:x}")

    @classmethod
    def pack(cls, data: int, *, valid: bool, first: bool, last: bool, orbit: bool = False) -> "CspWord":
        if not 0 <= data < 1 << 64:
            raise ValueError(f"CSP data is 64 bits, got 0x{data:x}")
        return cls((data << 3) | (int(last) << 2) | (int(first) << 1) | int(valid), orbit)

    @property
    def data(self) -> int:
        return self.word >> 3

    @property
    def valid(self) -> bool:
        return bool(self.word & 1)

    @property
    def first(self) -> bool:
        return bool(self.word >> 1 & 1)

    @property
    def last(self) -> bool:
        return bool(self.word >> 2 & 1)


def from_link_word(w: LinkWord) -> CspWord:
    """The CSP word and orbit marker of an EMP link word; strobe 0 has no CSP form."""
    if not w.strobe:
        raise ValueError("a CSP link has no strobe: a word with strobe 0 cannot be carried")
    return CspWord.pack(w.data, valid=w.valid, first=w.start, last=w.last, orbit=w.start_of_orbit)


def to_link_word(c: CspWord) -> LinkWord:
    return LinkWord(c.data, valid=c.valid, start=c.first, last=c.last, start_of_orbit=c.orbit, strobe=True)
