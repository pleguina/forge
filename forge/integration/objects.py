"""Physics objects as link words, with one field layout for both directions.

A trigger payload's reference is a list of objects per event (candidates,
muons, tracks) with named fields; the payload carries each object as a link
word. A codec names where each field sits in the word:

    width: 64
    fields:
      valid:   {low: 0, bits: 1, value: 1}     # a constant: every object sets it
      charge:  {low: 1, bits: 1}
      pt:      {low: 2, bits: 13}
      phi:     {low: 15, bits: 13, signed: true}

The same codec packs a reference export into pattern files
(:func:`write_patterns`) and reads the payload's output words back
(``verification.codecs``), so the two cannot disagree on the layout. A word
whose data bits are all zero is no object. ``shift`` places the data above
low bits a link word keeps for its own flags.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from forge.integration.emp import LinkWord, write_pattern_file


@dataclass(frozen=True)
class Field:
    low: int
    bits: int
    signed: bool = False
    value: Optional[int] = None    # a constant every object carries


@dataclass
class Codec:
    name: str
    width: int = 64
    shift: int = 0
    fields: Dict[str, Field] = field(default_factory=dict)

    @classmethod
    def from_spec(cls, spec: Mapping[str, Any], name: str) -> "Codec":
        width = int(spec.get("width", 64))
        fields: Dict[str, Field] = {}
        for fname, f in (spec.get("fields") or {}).items():
            if not isinstance(f, Mapping) or "low" not in f or "bits" not in f:
                raise ValueError(f"codec {name}: field {fname}: want {{low: N, bits: N}} (signed, value optional)")
            made = Field(int(f["low"]), int(f["bits"]), bool(f.get("signed", False)),
                         None if f.get("value") is None else int(f["value"]))
            if made.low < 0 or made.bits < 1 or made.low + made.bits > width:
                raise ValueError(f"codec {name}: field {fname} (bits {made.low}..{made.low + made.bits - 1}) "
                                 f"does not fit {width} bits")
            for other, o in fields.items():
                if made.low < o.low + o.bits and o.low < made.low + made.bits:
                    raise ValueError(f"codec {name}: fields {other} and {fname} overlap")
            fields[str(fname)] = made
        if not fields:
            raise ValueError(f"codec {name}: no fields")
        return cls(name, width, int(spec.get("shift", 0)), fields)

    @classmethod
    def load(cls, path: Path) -> "Codec":
        import yaml
        return cls.from_spec(yaml.safe_load(Path(path).read_text()) or {}, Path(path).stem)

    def pack(self, obj: Mapping[str, Any]) -> int:
        """The word of *obj*, a mapping of field to value; fields the codec
        does not name are ignored, fields it names and *obj* lacks are 0."""
        word = 0
        for fname, f in self.fields.items():
            v = f.value if f.value is not None else int(obj.get(fname, 0))
            lo, hi = (-(1 << (f.bits - 1)), (1 << (f.bits - 1)) - 1) if f.signed else (0, (1 << f.bits) - 1)
            if not lo <= v <= hi:
                raise ValueError(f"codec {self.name}: {fname} = {v} does not fit {f.bits} "
                                 f"{'signed ' if f.signed else ''}bits")
            word |= (v & ((1 << f.bits) - 1)) << f.low
        return word << self.shift

    def unpack(self, word: int) -> Optional[Dict[str, int]]:
        """The fields of *word* (its data bits; flags above them are left
        out), or None when they are all zero: no object."""
        data = (word >> self.shift) & ((1 << self.width) - 1)
        if data == 0:
            return None
        out = {}
        for fname, f in self.fields.items():
            v = (data >> f.low) & ((1 << f.bits) - 1)
            out[fname] = v - (1 << f.bits) if f.signed and v >> (f.bits - 1) else v
        return out


def load_codecs(spec: Mapping[str, Any], root: Path) -> Dict[str, Codec]:
    """``verification.codecs``: interface (or stream) -> codec file or inline layout."""
    out: Dict[str, Codec] = {}
    for iface, value in spec.items():
        out[str(iface)] = (Codec.load(root / str(value)) if isinstance(value, str)
                           else Codec.from_spec(value or {}, str(iface)))
    return out


def codec_decoder(codecs: Mapping[str, Codec]) -> Callable[[str, int], Optional[Dict[str, int]]]:
    """A ``verification.decoder`` reading each interface with its codec (keys
    may be globs: ``q*``)."""
    from fnmatch import fnmatchcase

    def decode(interface: str, word: int) -> Optional[Dict[str, int]]:
        codec = codecs.get(interface) or next((c for k, c in codecs.items() if fnmatchcase(interface, k)), None)
        if codec is None:
            raise ValueError(f"verification.codecs names no codec for {interface} (have {', '.join(codecs)})")
        return codec.unpack(word)
    return decode


def write_patterns(path: Path, events: Sequence[Mapping[str, Any]], *, objects: Callable[[Mapping[str, Any]], Sequence[Mapping[str, Any]]],
                   codec: Codec, link_of: Callable[[Mapping[str, Any]], int], links: Sequence[int],
                   gap: int = 1, names: Optional[Path] = None,
                   name_of: Callable[[Mapping[str, Any]], Any] = lambda e: e.get("id")) -> Tuple[int, int]:
    """Write the objects of *events* as an EMP pattern file: per event, one
    packet on every link of *links* (its objects, packed with *codec*, padded
    with empty words to the event's longest packet), then *gap* idle frames.
    *objects* gives an event's objects and *link_of* the link an object
    travels on. *names* gets the event names (``name_of``) as the ``events:``
    file of a data spec. Returns ``(events, objects)`` written."""
    frames: List[Dict[int, LinkWord]] = []
    listed: List[Any] = []
    count = 0
    for event in events:
        per_link: Dict[int, List[int]] = {link: [] for link in links}
        for obj in objects(event):
            link = int(link_of(obj))
            if link not in per_link:
                raise ValueError(f"event {name_of(event)}: an object on link {link}, not one of {list(links)}")
            per_link[link].append(codec.pack(obj))
            count += 1
        length = max(1, max(len(w) for w in per_link.values()))
        for k in range(length):
            frames.append({link: LinkWord(words[k] if k < len(words) else 0, valid=True,
                                          start=k == 0, last=k == length - 1)
                           for link, words in per_link.items()})
        frames += [{} for _ in range(gap)]
        listed.append(name_of(event))
    write_pattern_file(Path(path), frames, list(links))
    if names is not None:
        Path(names).write_text(json.dumps(listed, indent=1) + "\n")
    return len(listed), count
