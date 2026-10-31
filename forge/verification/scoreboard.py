"""Observed transactions against expected ones, the same way for every backend.

Modes (``compare.mode``):

- ``transaction``: the k-th observed word must equal the k-th expected one;
  idle cycles do not count.
- ``exact_cycle``: as ``transaction``, and each expected word that gives a
  ``cycle`` must be observed in that cycle.
- ``fixed_latency``: as ``transaction``, and each word must leave
  ``latency`` cycles after its event's first input word on any input
  interface when the data names events, else after the input transaction it
  comes from (``source``, by default its own index, of the first input
  interface).
- ``latency_window``: as ``fixed_latency`` with ``latency: {min, max}``.

``tolerance`` (an absolute difference) relaxes a numeric data comparison;
nothing else is relaxed.

``fields`` compares words by those fields of the project decoder's reading
(``verification.decoder``) instead of whole: bits the reference does not
define are left out, and a word the decoder reads as no object (``None``) is
not a transaction on either side. ``order`` says which reorderings are
allowed: ``strict`` (none, the default), ``event`` (any order within one
event, events in order) or ``any`` (any order). The latency modes need
``strict``. Every expected word is matched, missing or wrong;
every observed word beyond the expected ones is extra, and an extra word
equal to the one before it is reported as a duplicate. A word found at
another position is reported as out of order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

CHECKER_VERSION = "3"
MODES = ("transaction", "exact_cycle", "fixed_latency", "latency_window")
ORDERS = ("strict", "event", "any")

# Verification statuses, per interface, level and run.
VERIFIED = "VERIFIED"
FAILED = "FAILED"
NOT_VERIFIED_NO_ORACLE = "NOT_VERIFIED_NO_ORACLE"
SKIPPED = "SKIPPED"
BLOCKED_MISSING_TOOL = "BLOCKED_MISSING_TOOL"
PARTIAL = "PARTIAL"
STATUSES = (VERIFIED, FAILED, NOT_VERIFIED_NO_ORACLE, SKIPPED, BLOCKED_MISSING_TOOL, PARTIAL)


@dataclass
class Expected:
    value: int
    cycle: Optional[int] = None
    source: Optional[int] = None
    event: Optional[str] = None      # the experiment event the word belongs to


@dataclass
class Compare:
    mode: str = "transaction"
    latency_min: Optional[int] = None
    latency_max: Optional[int] = None
    tolerance: int = 0
    fields: Optional[List[str]] = None
    order: str = "strict"

    @classmethod
    def from_dict(cls, d: Optional[Mapping[str, Any]]) -> "Compare":
        d = d or {}
        mode = str(d.get("mode", "transaction"))
        if mode not in MODES:
            raise ValueError(f"compare.mode {mode!r}: want one of {', '.join(MODES)}")
        lat = d.get("latency")
        lo = hi = None
        if isinstance(lat, Mapping):
            lo, hi = int(lat["min"]), int(lat["max"])
        elif lat is not None:
            lo = hi = int(lat)
        if mode in ("fixed_latency", "latency_window") and lo is None:
            raise ValueError(f"compare.mode {mode} needs compare.latency")
        if mode == "fixed_latency" and lo != hi:
            raise ValueError("compare.mode fixed_latency takes one latency; use latency_window for a range")
        fields = d.get("fields")
        if fields is not None and (not isinstance(fields, list) or not fields):
            raise ValueError("compare.fields: want a list of the decoder's field names")
        order = str(d.get("order", "strict"))
        if order not in ORDERS:
            raise ValueError(f"compare.order {order!r}: want one of {', '.join(ORDERS)}")
        if order != "strict" and mode != "transaction":
            raise ValueError(f"compare.order {order} compares values only; compare.mode {mode} needs order strict")
        return cls(mode, lo, hi, int(d.get("tolerance", 0)), [str(f) for f in fields] if fields else None, order)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"mode": self.mode}
        if self.latency_min is not None:
            out["latency"] = {"min": self.latency_min, "max": self.latency_max}
        if self.tolerance:
            out["tolerance"] = self.tolerance
        if self.fields:
            out["fields"] = list(self.fields)
        if self.order != "strict":
            out["order"] = self.order
        return out


@dataclass
class InterfaceResult:
    interface: str
    status: str
    expected: int = 0
    observed: int = 0
    matched: int = 0
    rows: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"interface": self.interface, "status": self.status, "expected": self.expected,
                "observed": self.observed, "matched": self.matched, "rows": self.rows}


Key = Callable[[int], Optional[Tuple[Any, ...]]]


def compare(interface: str, expected: Optional[Sequence[Expected]], observed: Sequence[Tuple[int, int]],
            cmp: Compare, *, input_cycles: Sequence[int] = (), unknown: Sequence[int] = (),
            event_cycles: Optional[Mapping[str, int]] = None, key: Optional[Key] = None) -> InterfaceResult:
    """Compare one output interface. *observed* is ``(cycle, value)`` in order;
    *unknown* the cycles where it carried X or Z; *event_cycles* the cycle of
    each event's first input word. A row about an expected word names its
    event. *key* reads a word as the fields ``cmp.fields`` compares, or None
    for a word that carries no object."""
    if key is not None:
        observed = [(c, v) for c, v in observed if key(v) is not None]
        if expected is not None:
            expected = [e for e in expected if key(e.value) is not None]
    res = InterfaceResult(interface, NOT_VERIFIED_NO_ORACLE, observed=len(observed))
    for c in unknown:
        res.rows.append({"kind": "unknown", "cycle": c})
    if expected is None:
        if unknown:
            res.status = FAILED
        return res
    res.expected = len(expected)
    values = [v for _, v in observed]

    def same(a: int, b: int) -> bool:
        if key is not None:
            return key(a) == key(b)
        return abs(a - b) <= cmp.tolerance

    if cmp.order != "strict":
        _unordered(res, expected, observed, same, key if key is not None else _whole, cmp.order == "event")
        res.status = VERIFIED if not res.rows else FAILED
        return res
    for k, exp in enumerate(expected):
        rows_before = len(res.rows)
        _scan_one(res, k, exp, observed, values, same, cmp, input_cycles, event_cycles)
        if exp.event is not None:
            for row in res.rows[rows_before:]:
                row["event"] = exp.event
    for k in range(len(expected), len(observed)):
        cycle, value = observed[k]
        kind = "duplicate" if k > 0 and values[k - 1] == value else "extra"
        res.rows.append({"kind": kind, "index": k, "cycle": cycle, "observed": hex(value)})
    res.status = VERIFIED if not res.rows else FAILED
    return res


def _unordered(res: InterfaceResult, expected: Sequence[Expected], observed: Sequence[Tuple[int, int]],
               same: Any, ident: Any, by_event: bool) -> None:
    """Match words in any order: over the whole interface, or (*by_event*)
    within each event. An alignment of the two sequences gives each observed
    word the event it falls in; where the alignment is not exact the word may
    also match an expected word of an event next to it. So a word missing or
    extra in one event does not displace every later one. An expected word
    left unmatched pairs with an observed one left near its event (a
    mismatch): with decoded fields, the pairs that share most fields first,
    then one left in or next to its event; else it is missing. An observed
    word left over is extra."""
    if not by_event:
        groups = [list(range(len(expected)))]
        home = [0] * len(observed)
        reach = [(0, 0)] * len(observed)
    else:
        from difflib import SequenceMatcher

        groups = []
        for k, exp in enumerate(expected):
            if groups and exp.event is not None and expected[groups[-1][-1]].event == exp.event:
                groups[-1].append(k)
            else:
                groups.append([k])
        group_of = {k: g for g, ks in enumerate(groups) for k in ks}
        home, reach = [], []
        for tag, i1, i2, j1, j2 in SequenceMatcher(None, [ident(e.value) for e in expected],
                                                   [ident(v) for _, v in observed], autojunk=False).get_opcodes():
            near = sorted({group_of[i] for i in range(max(i1 - 1, 0), min(i2 + 1, len(expected)))})
            for t in range(j2 - j1):
                g = group_of[min(i1 + t, i2 - 1)] if i2 > i1 else group_of.get(i1, len(groups) - 1)
                home.append(g)
                reach.append((g, g) if tag == "equal" else (min(near[0], g), max(near[-1], g)))
    by_home: Dict[int, List[int]] = {}
    by_reach: Dict[int, List[int]] = {}
    for j, (lo, hi) in enumerate(reach):
        by_home.setdefault(home[j], []).append(j)
        for g in range(lo, hi + 1):
            by_reach.setdefault(g, []).append(j)
    used: set = set()
    unmatched: Dict[int, List[int]] = {}
    for g, ks in enumerate(groups):
        candidates = [j for j in by_home.get(g, [])] + [j for j in by_reach.get(g, []) if home[j] != g]
        for k in ks:
            found = next((j for j in candidates if j not in used and same(observed[j][1], expected[k].value)), None)
            if found is None:
                unmatched.setdefault(g, []).append(k)
            else:
                used.add(found)
                res.matched += 1
    paired: Dict[int, int] = {}
    if by_event:
        scored = []
        for g, ks in unmatched.items():
            near = [j for j in dict.fromkeys(by_home.get(g, []) + by_reach.get(g, [])) if j not in used]
            for k in ks:
                want = ident(expected[k].value)
                for j in near:
                    agree = _agreement(want, ident(observed[j][1]))
                    if agree:
                        scored.append((-agree, abs(home[j] - g), k, j))
        for _, _, k, j in sorted(scored):
            if k not in paired and j not in used:
                paired[k] = j
                used.add(j)
        for g, ks in unmatched.items():
            for k in ks:
                if k not in paired:
                    spare = next((j for j in by_home.get(g, []) + by_reach.get(g, []) if j not in used), None)
                    if spare is not None:
                        paired[k] = spare
                        used.add(spare)
    for k in sorted(k for ks in unmatched.values() for k in ks):
        exp = expected[k]
        row: Dict[str, Any] = {"kind": "missing", "index": k, "expected": hex(exp.value)}
        if k in paired:
            j = paired[k]
            row.update(kind="mismatch", cycle=observed[j][0], observed=hex(observed[j][1]))
        if exp.event is not None:
            row["event"] = exp.event
        res.rows.append(row)
    for j in range(len(observed)):
        if j not in used:
            res.rows.append({"kind": "extra", "index": j, "cycle": observed[j][0], "observed": hex(observed[j][1])})


def _whole(word: int) -> int:
    return word


def _agreement(want: Any, got: Any) -> int:
    """How many decoded fields two words share (0 for words compared whole)."""
    if not isinstance(want, tuple) or not isinstance(got, tuple):
        return 0
    return sum(a == b for a, b in zip(want, got))


def _scan_one(res: InterfaceResult, k: int, exp: Expected, observed: Sequence[Tuple[int, int]],
              values: Sequence[int], same: Any, cmp: Compare, input_cycles: Sequence[int],
              event_cycles: Optional[Mapping[str, int]]) -> None:
    """Check expected word *k*, adding a row to *res* or counting a match."""
    if k >= len(observed):
        row = {"kind": "missing", "index": k, "expected": hex(exp.value)}
        later = [j for j in range(len(observed)) if same(values[j], exp.value)]
        if later:
            row["seen_at"] = later[0]
        res.rows.append(row)
        return
    cycle, value = observed[k]
    if not same(value, exp.value):
        row = {"kind": "mismatch", "index": k, "cycle": cycle, "expected": hex(exp.value), "observed": hex(value)}
        elsewhere = [j for j in range(len(observed)) if j != k and same(values[j], exp.value)]
        if elsewhere:
            row["kind"] = "out_of_order"
            row["seen_at"] = elsewhere[0]
        res.rows.append(row)
        return
    timing = _timing(k, exp, cycle, cmp, input_cycles, event_cycles)
    if timing:
        res.rows.append(timing)
        return
    res.matched += 1


def _timing(k: int, exp: Expected, cycle: int, cmp: Compare, input_cycles: Sequence[int],
            event_cycles: Optional[Mapping[str, int]] = None) -> Optional[Dict[str, Any]]:
    if cmp.mode == "exact_cycle" and exp.cycle is not None and cycle != exp.cycle:
        return {"kind": "wrong_cycle", "index": k, "cycle": cycle, "expected_cycle": exp.cycle}
    if cmp.mode in ("fixed_latency", "latency_window"):
        if exp.event is not None and event_cycles is not None:
            if exp.event not in event_cycles:
                return {"kind": "no_source", "index": k, "source": exp.event}
            latency = cycle - event_cycles[exp.event]
        else:
            src = exp.source if exp.source is not None else k
            if src >= len(input_cycles):
                return {"kind": "no_source", "index": k, "source": src}
            latency = cycle - input_cycles[src]
        assert cmp.latency_min is not None and cmp.latency_max is not None
        if not cmp.latency_min <= latency <= cmp.latency_max:
            return {"kind": "latency", "index": k, "cycle": cycle, "latency": latency,
                    "allowed": [cmp.latency_min, cmp.latency_max]}
    return None


def combine(statuses: Sequence[str]) -> str:
    """One status for several: a failure fails; all verified is verified;
    verified next to unverified is partial."""
    s = set(statuses)
    if not s:
        return NOT_VERIFIED_NO_ORACLE
    if FAILED in s:
        return FAILED
    if s == {VERIFIED}:
        return VERIFIED
    if VERIFIED in s:
        return PARTIAL
    for status in (BLOCKED_MISSING_TOOL, NOT_VERIFIED_NO_ORACLE, SKIPPED):
        if status in s:
            return status
    return PARTIAL
