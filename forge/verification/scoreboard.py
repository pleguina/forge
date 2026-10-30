"""Observed transactions against expected ones, the same way for every backend.

Modes (``compare.mode``):

- ``transaction``: the k-th observed word must equal the k-th expected one;
  idle cycles do not count.
- ``exact_cycle``: as ``transaction``, and each expected word that gives a
  ``cycle`` must be observed in that cycle.
- ``fixed_latency``: as ``transaction``, and each word must leave
  ``latency`` cycles after the input transaction it comes from (``source``,
  by default its own index, of the first input interface).
- ``latency_window``: as ``fixed_latency`` with ``latency: {min, max}``.

``tolerance`` (an absolute difference) relaxes a numeric data comparison;
nothing else is relaxed. Every expected word is matched, missing or wrong;
every observed word beyond the expected ones is extra, and an extra word
equal to the one before it is reported as a duplicate. A word found at
another position is reported as out of order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

CHECKER_VERSION = "1"
MODES = ("transaction", "exact_cycle", "fixed_latency", "latency_window")

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


@dataclass
class Compare:
    mode: str = "transaction"
    latency_min: Optional[int] = None
    latency_max: Optional[int] = None
    tolerance: int = 0

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
        return cls(mode, lo, hi, int(d.get("tolerance", 0)))

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"mode": self.mode}
        if self.latency_min is not None:
            out["latency"] = {"min": self.latency_min, "max": self.latency_max}
        if self.tolerance:
            out["tolerance"] = self.tolerance
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


def compare(interface: str, expected: Optional[Sequence[Expected]], observed: Sequence[Tuple[int, int]],
            cmp: Compare, *, input_cycles: Sequence[int] = (), unknown: Sequence[int] = ()
            ) -> InterfaceResult:
    """Compare one output interface. *observed* is ``(cycle, value)`` in order;
    *unknown* the cycles where it carried X or Z."""
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
        return abs(a - b) <= cmp.tolerance

    for k, exp in enumerate(expected):
        if k >= len(observed):
            row = {"kind": "missing", "index": k, "expected": hex(exp.value)}
            later = [j for j in range(len(observed)) if same(values[j], exp.value)]
            if later:
                row["seen_at"] = later[0]
            res.rows.append(row)
            continue
        cycle, value = observed[k]
        if not same(value, exp.value):
            row = {"kind": "mismatch", "index": k, "cycle": cycle, "expected": hex(exp.value), "observed": hex(value)}
            elsewhere = [j for j in range(len(observed)) if j != k and same(values[j], exp.value)]
            if elsewhere:
                row["kind"] = "out_of_order"
                row["seen_at"] = elsewhere[0]
            res.rows.append(row)
            continue
        timing = _timing(k, exp, cycle, cmp, input_cycles)
        if timing:
            res.rows.append(timing)
            continue
        res.matched += 1
    for k in range(len(expected), len(observed)):
        cycle, value = observed[k]
        kind = "duplicate" if k > 0 and values[k - 1] == value else "extra"
        res.rows.append({"kind": kind, "index": k, "cycle": cycle, "observed": hex(value)})
    res.status = VERIFIED if not res.rows else FAILED
    return res


def _timing(k: int, exp: Expected, cycle: int, cmp: Compare, input_cycles: Sequence[int]) -> Optional[Dict[str, Any]]:
    if cmp.mode == "exact_cycle" and exp.cycle is not None and cycle != exp.cycle:
        return {"kind": "wrong_cycle", "index": k, "cycle": cycle, "expected_cycle": exp.cycle}
    if cmp.mode in ("fixed_latency", "latency_window"):
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
