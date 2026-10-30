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
