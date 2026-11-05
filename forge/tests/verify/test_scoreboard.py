"""Transaction comparison and verification statuses (forge.verification.scoreboard)."""

from __future__ import annotations

import pytest

from forge.verification import scoreboard as sb
from forge.verification.scoreboard import Compare, Expected, compare


def _kinds(res):
    return [r["kind"] for r in res.rows]


def _exp(*values):
    return [Expected(v) for v in values]


def test_matching_transactions_verify_and_idle_cycles_do_not_count():
    res = compare("o", _exp(1, 2, 3), [(5, 1), (9, 2), (40, 3)], Compare())
    assert res.status == sb.VERIFIED and res.matched == 3


@pytest.mark.parametrize("observed, kinds", [
    ([(1, 1), (2, 9), (3, 3)], ["mismatch"]),
    ([(1, 1), (2, 2)], ["missing"]),
    ([(1, 1), (2, 2), (3, 3), (4, 7)], ["extra"]),
    ([(1, 1), (2, 2), (3, 3), (4, 3)], ["duplicate"]),
    ([(1, 1), (2, 3), (3, 2)], ["out_of_order", "out_of_order"]),
])
def test_every_kind_of_wrong_output_is_named(observed, kinds):
    res = compare("o", _exp(1, 2, 3), observed, Compare())
    assert res.status == sb.FAILED and _kinds(res) == kinds


def test_no_oracle_is_not_verified_and_x_still_fails():
    assert compare("o", None, [(1, 5)], Compare()).status == sb.NOT_VERIFIED_NO_ORACLE
    res = compare("o", None, [], Compare(), unknown=[3])
    assert res.status == sb.FAILED and _kinds(res) == ["unknown"]


def test_exact_cycle_checks_each_given_cycle():
    res = compare("o", [Expected(1, cycle=4), Expected(2, cycle=6)], [(4, 1), (7, 2)], Compare("exact_cycle"))
    assert _kinds(res) == ["wrong_cycle"] and res.rows[0]["expected_cycle"] == 6


def test_latency_is_measured_from_the_source_input():
    cmp = Compare.from_dict({"mode": "latency_window", "latency": {"min": 3, "max": 4}})
    res = compare("o", [Expected(1), Expected(2, source=2)], [(13, 1), (30, 2)], cmp, input_cycles=[10, 11, 25])
    assert _kinds(res) == ["latency"] and res.rows[0]["latency"] == 5
    fixed = Compare.from_dict({"mode": "fixed_latency", "latency": 3})
    assert compare("o", _exp(1), [(13, 1)], fixed, input_cycles=[10]).status == sb.VERIFIED


def test_tolerance_relaxes_only_the_data():
    assert compare("o", _exp(100), [(1, 102)], Compare(tolerance=2)).status == sb.VERIFIED
    assert compare("o", _exp(100), [(1, 103)], Compare(tolerance=2)).status == sb.FAILED


@pytest.mark.parametrize("spec, message", [
    ({"mode": "close_enough"}, "compare.mode"),
    ({"mode": "fixed_latency"}, "needs compare.latency"),
    ({"mode": "fixed_latency", "latency": {"min": 1, "max": 2}}, "use latency_window"),
])
def test_bad_compare_specs_are_refused(spec, message):
    with pytest.raises(ValueError, match=message):
        Compare.from_dict(spec)


@pytest.mark.parametrize("statuses, combined", [
    ([sb.VERIFIED, sb.VERIFIED], sb.VERIFIED),
    ([sb.VERIFIED, sb.FAILED], sb.FAILED),
    ([sb.VERIFIED, sb.NOT_VERIFIED_NO_ORACLE], sb.PARTIAL),
    ([sb.NOT_VERIFIED_NO_ORACLE], sb.NOT_VERIFIED_NO_ORACLE),
    ([sb.BLOCKED_MISSING_TOOL, sb.NOT_VERIFIED_NO_ORACLE], sb.BLOCKED_MISSING_TOOL),
    ([], sb.NOT_VERIFIED_NO_ORACLE),
])
def test_statuses_combine_without_turning_absence_into_a_pass(statuses, combined):
    assert sb.combine(statuses) == combined


# ── Empty references, events and time ───────────────────────────────────────

@pytest.mark.parametrize("order", ["strict", "event", "any"])
def test_an_object_against_an_empty_reference_is_extra(order):
    res = compare("o", [], [(0, 1)], Compare(order=order))
    assert res.status == sb.FAILED and _kinds(res) == ["extra"]


@pytest.mark.parametrize("order", ["strict", "event", "any"])
def test_an_empty_run_against_an_empty_reference_verifies(order):
    assert compare("o", [], [], Compare(order=order)).status == sb.VERIFIED


def _window(lo, hi=None):
    return Compare.from_dict({"order": "event", "mode": "latency_window" if hi is not None else "fixed_latency",
                              "latency": {"min": lo, "max": hi} if hi is not None else lo})


# Three BXs 10 cycles apart; each BX's output occupies cycles start+4 .. start+6.
STARTS = {"bx0": 0, "bx1": 10, "bx2": 20}


def _bx(*pairs):
    return [Expected(v, event=e) for e, v in pairs]


def test_order_event_with_a_latency_window_allows_reordering_within_a_bx():
    exp = _bx(("bx0", 1), ("bx0", 2), ("bx2", 3))
    res = compare("o", exp, [(4, 2), (5, 1), (25, 3)], _window(4, 6), event_cycles=STARTS)
    assert res.status == sb.VERIFIED and res.matched == 3


def test_a_whole_bx_shift_fails_although_every_value_is_right():
    exp = _bx(("bx0", 1), ("bx1", 2))
    shifted = compare("o", exp, [(14, 1), (24, 2)], _window(4, 6), event_cycles=STARTS)
    assert shifted.status == sb.FAILED
    assert [(r["kind"], r.get("event")) for r in shifted.rows] == [
        ("missing", "bx0"), ("mismatch", "bx1"), ("extra", "bx2")]
    value_only = compare("o", exp, [(14, 1), (24, 2)], Compare(order="event"), event_cycles=STARTS)
    assert value_only.status == sb.VERIFIED


def test_an_object_in_an_empty_bx_between_occupied_ones_is_extra():
    exp = _bx(("bx0", 1), ("bx2", 3))
    res = compare("o", exp, [(4, 1), (15, 9), (24, 3)], _window(4, 6), event_cycles=STARTS)
    assert [(r["kind"], r.get("event")) for r in res.rows] == [("extra", "bx1")]


def test_identical_objects_in_neighbouring_bxs_are_both_needed():
    exp = _bx(("bx0", 7), ("bx1", 7))
    assert compare("o", exp, [(4, 7), (14, 7)], _window(4, 6), event_cycles=STARTS).status == sb.VERIFIED
    both_in_one = compare("o", exp, [(4, 7), (5, 7)], _window(4, 6), event_cycles=STARTS)
    assert [(r["kind"], r.get("event")) for r in both_in_one.rows] == [("duplicate", "bx0"), ("missing", "bx1")]


def test_a_word_outside_every_window_or_in_two_is_never_matched_by_value():
    exp = _bx(("bx0", 1))
    late = compare("o", exp, [(8, 1)], _window(4, 6), event_cycles=STARTS)
    assert _kinds(late) == ["extra", "missing"] and late.rows[0]["latency"] == 8
    overlapping = compare("o", exp, [(14, 1)], _window(4, 14), event_cycles=STARTS)
    assert "ambiguous_event" in _kinds(overlapping)


def test_order_event_with_latency_needs_named_events():
    res = compare("o", [Expected(1)], [(4, 1)], _window(4), event_cycles=None)
    assert res.status == sb.FAILED and _kinds(res) == ["no_event_timing"]
