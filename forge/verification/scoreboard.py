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
nothing else is relaxed. ``framing: true`` also checks the packet boundaries
of a link-word output: a run of valid words on consecutive cycles is one
packet, with ``start`` on its first word and ``last`` on its final one and on
no other.

``fields`` compares words by those fields of the project decoder's reading
(``verification.decoder``) instead of whole: bits the reference does not
define are left out, and a word the decoder reads as no object (``None``) is
not a transaction on either side. ``order`` says which reorderings are
allowed: ``strict`` (none, the default), ``event`` (any order within one
event, events in order) or ``any`` (any order). ``event`` with a latency
mode attributes each observed word to an event by time alone: the event whose
latency window (its first input word plus ``latency``) holds the word's
cycle. Values are then matched in any order within that event, so a word
moved into a neighbouring event's window fails even when its value is right,
an event with no expected words must stay empty, and a word in no window or
in two is reported, never matched by value. Without event names in the input
there is no attribution and the comparison fails saying so. ``latency: {min: N,
max: next}`` makes each event's window end where the next event's begins,
for designs called once per event. ``any`` and ``exact_cycle`` take no
reordering. Every expected word is matched, missing or wrong;
every observed word beyond the expected ones is extra, and an extra word
equal to the one before it is reported as a duplicate. A word found at
another position is reported as out of order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

CHECKER_VERSION = "4"
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
    framing: bool = False
    until_next: bool = False     # an event's window ends where the next event's begins

    @classmethod
    def from_dict(cls, d: Optional[Mapping[str, Any]]) -> "Compare":
        d = d or {}
        mode = str(d.get("mode", "transaction"))
        if mode not in MODES:
            raise ValueError(f"compare.mode {mode!r}: want one of {', '.join(MODES)}")
        lat = d.get("latency")
        lo = hi = None
        until_next = False
        if isinstance(lat, Mapping):
            until_next = str(lat.get("max")) == "next"
            lo, hi = int(lat["min"]), (None if until_next else int(lat["max"]))
        elif lat is not None:
            lo = hi = int(lat)
        if mode in ("fixed_latency", "latency_window") and lo is None:
            raise ValueError(f"compare.mode {mode} needs compare.latency")
        if mode == "fixed_latency" and lo != hi:
            raise ValueError("compare.mode fixed_latency takes one latency; use latency_window for a range")
        if until_next and (mode != "latency_window" or str(d.get("order")) != "event"):
            raise ValueError("compare.latency max: next (each event until the next one starts) is for "
                             "mode latency_window with order event")
        fields = d.get("fields")
        if fields is not None and (not isinstance(fields, list) or not fields):
            raise ValueError("compare.fields: want a list of the decoder's field names")
        order = str(d.get("order", "strict"))
        if order not in ORDERS:
            raise ValueError(f"compare.order {order!r}: want one of {', '.join(ORDERS)}")
        if order == "any" and mode != "transaction":
            raise ValueError(f"compare.order any compares values only; compare.mode {mode} needs order strict "
                             "or event")
        if order == "event" and mode == "exact_cycle":
            raise ValueError("compare.order event with compare.mode exact_cycle: give the timing as a latency "
                             "(fixed_latency or latency_window) from each event's first input word")
        unknown = sorted(set(d) - {"mode", "latency", "tolerance", "fields", "order", "framing"})
        if unknown:
            raise ValueError(f"compare: unknown key {', '.join(unknown)} "
                             "(want mode, latency, tolerance, fields, order, framing)")
        return cls(mode, lo, hi, int(d.get("tolerance", 0)), [str(f) for f in fields] if fields else None, order,
                   bool(d.get("framing", False)), until_next)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"mode": self.mode}
        if self.latency_min is not None:
            out["latency"] = {"min": self.latency_min, "max": "next" if self.until_next else self.latency_max}
        if self.tolerance:
            out["tolerance"] = self.tolerance
        if self.fields:
            out["fields"] = list(self.fields)
        if self.order != "strict":
            out["order"] = self.order
        if self.framing:
            out["framing"] = True
        return out


@dataclass
class InterfaceResult:
    interface: str
    status: str
    expected: int = 0
    observed: int = 0
    matched: int = 0
    rows: List[Dict[str, Any]] = field(default_factory=list)
    context_rows: List[Dict[str, Any]] = field(default_factory=list)   # about events replayed as history

    def to_dict(self) -> Dict[str, Any]:
        out = {"interface": self.interface, "status": self.status, "expected": self.expected,
               "observed": self.observed, "matched": self.matched, "rows": self.rows}
        if self.context_rows:
            out["context_rows"] = self.context_rows
        return out

    def set_aside(self, context: Sequence[str]) -> None:
        """Move the rows about the events in *context* (replayed only to give
        later events their history) to ``context_rows``: they are reported,
        and do not fail the result."""
        if not context:
            return
        keep = set(context)
        self.context_rows += [r for r in self.rows if r.get("event") in keep]
        self.rows = [r for r in self.rows if r.get("event") not in keep]
        if self.status == FAILED and not self.rows:
            self.status = VERIFIED


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

    if cmp.order == "event" and cmp.mode != "transaction":
        _by_event_window(res, expected, observed, same, key if key is not None else _whole, cmp, event_cycles)
        res.status = VERIFIED if not res.rows else FAILED
        return res
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
    if not expected:
        _extras(res, observed, range(len(observed)), same)
        return
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
    _extras(res, observed, [j for j in range(len(observed)) if j not in used], same)


def _extras(res: InterfaceResult, observed: Sequence[Tuple[int, int]], left: Sequence[int], same: Any,
            event: Optional[str] = None, pool: Optional[Sequence[int]] = None) -> None:
    """Rows for the observed words *left* unmatched: a word equal to the
    observed word before it (in *pool*, the words of its event, when given)
    is a duplicate, any other is extra."""
    order = list(pool) if pool is not None else list(range(len(observed)))
    position = {j: n for n, j in enumerate(order)}
    for j in left:
        cycle, value = observed[j]
        n = position[j]
        kind = "duplicate" if n > 0 and same(observed[order[n - 1]][1], value) else "extra"
        row: Dict[str, Any] = {"kind": kind, "index": j, "cycle": cycle, "observed": hex(value)}
        if event is not None:
            row["event"] = event
        res.rows.append(row)


def _by_event_window(res: InterfaceResult, expected: Sequence[Expected], observed: Sequence[Tuple[int, int]],
                     same: Any, ident: Any, cmp: Compare, event_cycles: Optional[Mapping[str, int]]) -> None:
    """``order: event`` with a latency mode: each observed word belongs to the
    event whose window ``[first input + latency min, first input + latency
    max]`` holds its cycle; values are matched in any order within an event.
    The value of a word never decides its event."""
    assert cmp.latency_min is not None and (cmp.latency_max is not None or cmp.until_next)
    if not event_cycles or any(e.event is None for e in expected):
        res.rows.append({"kind": "no_event_timing",
                         "reason": "compare.order event with a latency mode needs the input to name its events "
                                   "(an events: file or a pattern file's packets) and every expected word to "
                                   "belong to one; without them the words cannot be attributed by time"})
        return
    starts = sorted(event_cycles.items(), key=lambda kv: kv[1])
    lo = cmp.latency_min
    if cmp.until_next:
        ends = {name: (starts[k + 1][1] + lo - 1 if k + 1 < len(starts) else float("inf"))
                for k, (name, _) in enumerate(starts)}
    else:
        assert cmp.latency_max is not None
        ends = {name: start + cmp.latency_max for name, start in starts}
    hi = cmp.latency_max if cmp.latency_max is not None else "next"
    home: Dict[str, List[int]] = {name: [] for name, _ in starts}
    for j, (cycle, value) in enumerate(observed):
        owners = [name for name, start in starts if start + lo <= cycle <= ends[name]]
        if len(owners) == 1:
            home[owners[0]].append(j)
            continue
        row: Dict[str, Any] = {"kind": "extra", "index": j, "cycle": cycle, "observed": hex(value)}
        if owners:
            row.update(kind="ambiguous_event", events=owners,
                       reason="the latency windows of these events overlap at this cycle")
        else:
            before = [(name, start) for name, start in starts if start <= cycle]
            if before:
                row.update(event=before[-1][0], latency=cycle - before[-1][1], allowed=[lo, hi])
            row["reason"] = "outside every event's latency window"
        res.rows.append(row)
    wanted: Dict[str, List[int]] = {}
    for k, exp in enumerate(expected):
        if exp.event not in event_cycles:
            res.rows.append({"kind": "no_source", "index": k, "source": exp.event, "expected": hex(exp.value),
                             "event": exp.event})
        else:
            wanted.setdefault(str(exp.event), []).append(k)
    for name, _ in starts:
        ks, js = wanted.get(name, []), home[name]
        used: set = set()
        left: List[int] = []
        for k in ks:
            found = next((j for j in js if j not in used and same(observed[j][1], expected[k].value)), None)
            if found is None:
                left.append(k)
            else:
                used.add(found)
                res.matched += 1
        spare = [j for j in js if j not in used]
        scored = sorted((-_agreement(ident(expected[k].value), ident(observed[j][1])), k, j)
                        for k in left for j in spare)
        paired: Dict[int, int] = {}
        for _, k, j in scored:
            if k not in paired and j not in used:
                paired[k] = j
                used.add(j)
        for k in left:
            row = {"kind": "missing", "index": k, "expected": hex(expected[k].value), "event": name}
            if k in paired:
                j = paired[k]
                row.update(kind="mismatch", cycle=observed[j][0], observed=hex(observed[j][1]))
            res.rows.append(row)
        _extras(res, observed, [j for j in js if j not in used], same, name, js)


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


def framing(res: InterfaceResult, observed: Sequence[Tuple[int, int]], flags: Callable[[int], Mapping[str, int]],
            have: Sequence[str]) -> None:
    """Rows for packets whose boundaries are wrong. *observed* holds the valid
    words; *flags* reads a word's ``start``/``last``; *have* names the flags
    the interface has."""
    packets: List[List[Tuple[int, int]]] = []
    for cycle, word in observed:
        if packets and cycle == packets[-1][-1][0] + 1:
            packets[-1].append((cycle, word))
        else:
            packets.append([(cycle, word)])
    for words in packets:
        for n, (cycle, word) in enumerate(words):
            f = flags(word)
            for flag, want in (("start", n == 0), ("last", n == len(words) - 1)):
                if flag in have and bool(f.get(flag)) != want:
                    res.rows.append({"kind": "framing", "cycle": cycle, "flag": flag, "expected_flag": int(want),
                                     "packet_first_cycle": words[0][0], "packet_words": len(words)})


def checks(cmp: Compare, results: Sequence[InterfaceResult], oracle: bool) -> Dict[str, str]:
    """``passed``, ``failed``, ``not_checked`` or ``unavailable`` for each
    property a comparison can establish, from the rows of its *results*. A
    property the settings did not ask for is not checked, never passed."""
    kinds = {row.get("kind") for r in results for row in r.rows}

    def verdict(asked: bool, bad: set, unavailable: set = frozenset()) -> str:
        if not asked:
            return "not_checked"
        if kinds & set(unavailable):
            return "unavailable"
        return "failed" if kinds & bad else "passed"

    values = {"mismatch", "missing", "extra", "duplicate", "out_of_order"}
    timed = cmp.mode in ("fixed_latency", "latency_window")
    return {
        "no_x_on_outputs": "failed" if "unknown" in kinds else "passed",
        "values": verdict(oracle, values),
        "event_association": verdict(oracle and cmp.order == "event" and timed, {"ambiguous_event"} | values,
                                     {"no_event_timing"}),
        "latency": verdict(oracle and timed, {"latency", "no_source", "extra"} if cmp.order == "event"
                           else {"latency", "no_source"}, {"no_event_timing"}),
        "cycle": verdict(oracle and cmp.mode == "exact_cycle", {"wrong_cycle"}),
        "framing": verdict(cmp.framing, {"framing"}),
    }


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
