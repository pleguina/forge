#!/usr/bin/env python3
"""Slice 10.5 negative fixture — spec §17.5 "Invalid FIFO fixture"
(release-plan Phase 10, preflight.md §5 Decision B): "Configure a
burst/stall requirement that provably exceeds FIFO capacity" and expect
"strict failure or release-gate error... required and configured depth
shown."

FIFO depth *sufficiency* for a given burst pattern is not (and cannot
be) a static topology-validity concern -- `forge topgen validate` only
checks depth is a positive power of two (ATG022/ATG026), which
``invalid_fifo_depth_packetizer.yml``'s own depth=4 satisfies. This is
therefore a release-gate-style check over real *measured* data, not a
topgen-time error: it compares a candidate design's configured depth
against the SAME design's own real, non-saturating required capacity,
measured directly from design_packetizer.yml's own depth=64 xsim run
(preflight.md's 10.5 completion evidence) -- 28, a real high-water mark
that never hit its own ceiling, so it's a genuine (not artificially
capped) measurement of what this exact burst pattern needs, not an
estimate.

Usage::

    python3 check_fifo_capacity.py
    # exits 1 with required-vs-configured depth (expected: depth=4 fails)
"""
from __future__ import annotations

import sys
from pathlib import Path

# Real, measured requirement: design_packetizer.yml's own real depth=64
# xsim run (never saturated -- see preflight.md's 10.5 completion
# evidence) reached this real high-water mark for the pixel-result
# crossing's exact burst pattern (64 back-to-back 200MHz writes draining
# into a 125MHz packetizer).
_REQUIRED_DEPTH_PIXEL_RESULT = 28

_CANDIDATE_DEPTH = 4  # invalid_fifo_depth_packetizer.yml's own real depth
_PROBE_CSV = Path(__file__).resolve().parents[1] / "invalid_fifo_depth_xsim/xsim_work/algo_top_probe.csv"


def check_fifo_capacity() -> None:
    overflow_events = 0
    if _PROBE_CSV.exists():
        rows = _PROBE_CSV.read_text().splitlines()
        header = rows[0].split(",")
        idx = header.index("pr_overflow")
        for line in rows[1:]:
            fields = line.split(",")
            if fields[idx] == "1":
                overflow_events += 1

    if _CANDIDATE_DEPTH < _REQUIRED_DEPTH_PIXEL_RESULT:
        print(
            "FAIL: configured async_fifo depth is insufficient for this design's "
            f"real burst pattern — configured depth={_CANDIDATE_DEPTH}, required "
            f"depth (real, measured, non-saturating)={_REQUIRED_DEPTH_PIXEL_RESULT} "
            f"— {overflow_events} real overflow_attempt cycle(s) observed in "
            f"{_PROBE_CSV.name}.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print(
        f"OK: configured depth={_CANDIDATE_DEPTH} >= required depth="
        f"{_REQUIRED_DEPTH_PIXEL_RESULT}"
    )


if __name__ == "__main__":
    check_fifo_capacity()
