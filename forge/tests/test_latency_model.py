"""
Tests for forge.analyze.latency_model — the shared, provenance-carrying
latency wrapper.
"""

from __future__ import annotations

import pytest

from forge.analyze.latency_model import (
    LATENCY_KINDS,
    LATENCY_SOURCES,
    LatencyModelError,
    LatencyProvenance,
    LatencyValue,
)


def test_latency_sources_has_all_six_release_plan_values():
    assert LATENCY_SOURCES == {
        "explicit_contract", "hls_report", "generated_transformation",
        "inferred", "user_hint", "runtime_observation",
    }


def test_latency_kinds_has_all_three_release_plan_values():
    assert LATENCY_KINDS == {"fixed", "bounded", "elastic"}


def test_provenance_accepts_known_source():
    p = LatencyProvenance("hls_report", detail="csynth.xml worst_case_latency")
    assert p.source == "hls_report"
    assert p.detail == "csynth.xml worst_case_latency"


def test_provenance_rejects_unknown_source():
    with pytest.raises(LatencyModelError):
        LatencyProvenance("made_up_source")


def test_latency_value_default_kind_is_fixed():
    v = LatencyValue(cycles=3)
    assert v.kind == "fixed"


def test_latency_value_rejects_unknown_kind():
    with pytest.raises(LatencyModelError):
        LatencyValue(kind="not_a_real_kind")


def test_latency_value_carries_provenance():
    v = LatencyValue(cycles=2, provenance=LatencyProvenance("generated_transformation", detail="register_stages=2"))
    assert v.provenance.source == "generated_transformation"
    assert v.cycles == 2
