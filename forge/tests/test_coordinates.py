"""
Tests for forge.contracts.coordinates — the structured-coordinates /
legacy-partition compatibility layer (audit gap #2: "No structured
coordinates / no cardinality schema").
"""

from __future__ import annotations

import pytest

from forge.contracts.coordinates import coordinate_key, coordinate_label


def test_legacy_partition_string_normalizes():
    key = coordinate_key({"partition": "lower_pair"})
    assert key == (("partition", "lower_pair"),)
    assert coordinate_label(key) == "lower_pair"


def test_structured_coordinates_normalize_sorted():
    # Insertion order shouldn't matter — two equivalent dicts produce the
    # same key regardless of key order.
    key_a = coordinate_key({"coordinates": {"sector": 2, "station": 1}})
    key_b = coordinate_key({"coordinates": {"station": 1, "sector": 2}})
    assert key_a == key_b
    assert key_a == (("sector", 2), ("station", 1))


def test_structured_coordinates_label_is_human_readable():
    key = coordinate_key({"coordinates": {"sector": 2, "station": 1}})
    assert coordinate_label(key) == "sector=2, station=1"


def test_distinct_coordinates_produce_distinct_keys():
    a = coordinate_key({"coordinates": {"sector": 1, "station": 1}})
    b = coordinate_key({"coordinates": {"sector": 2, "station": 1}})
    assert a != b


def test_no_partition_or_coordinates_is_none():
    assert coordinate_key({}) is None
    assert coordinate_label(None) == "<none>"


def test_coordinates_must_be_a_mapping():
    with pytest.raises(ValueError, match="must be a mapping"):
        coordinate_key({"coordinates": "sector2"})


def test_conflicting_partition_and_coordinates_rejected():
    with pytest.raises(ValueError, match="conflicting legacy 'partition'"):
        coordinate_key({"partition": "lower_pair", "coordinates": {"sector": 2}})


def test_consistent_partition_and_coordinates_allowed():
    # A single-axis 'partition' coordinate matching the legacy string is not
    # a conflict — this is the exact normalized form the legacy string
    # produces on its own.
    key = coordinate_key({"partition": "lower_pair", "coordinates": {"partition": "lower_pair"}})
    assert key == (("partition", "lower_pair"),)
