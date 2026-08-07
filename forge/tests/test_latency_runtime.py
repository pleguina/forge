"""
Tests for forge.analyze.latency_runtime's additions:
  - probe.load_wide_probe_csv, the fix for the previously-broken pipe
    between forge.verification.gen_sim's real Tier-2 probe emission (wide CSV)
    and this package's consumer (which only accepted long-format CSV).
  - comparator.LatencyComparison's predicted/observed_value/path fields.
"""

from __future__ import annotations

from pathlib import Path

from analyze.latency_runtime.comparator import compare
from analyze.latency_runtime.probe import (
    ProbeEvent,
    load_probe_csv,
    load_wide_probe_csv,
    measure_latency,
)


def test_load_wide_probe_csv_matches_gen_sim_emitted_format(tmp_path):
    """Fixture built from forge.verification.gen_sim._render_probe_open/
    _render_probe_fwrite's actual format strings (header:
    'cycle,<name1>,<name2>,...'; rows: '<cycle_count>,<val1>,<val2>,...'),
    not guessed."""
    csv_path = tmp_path / "algo_top_probe.csv"
    csv_path.write_text(
        "cycle,dec_0_raw_valid,tout_out_valid\n"
        "10,1,0\n"
        "18,1,1\n"
    )

    events = load_wide_probe_csv(csv_path)
    assert ProbeEvent(signal="dec_0_raw_valid", cycle=10, value="1") in events
    assert ProbeEvent(signal="tout_out_valid", cycle=10, value="0") in events
    assert ProbeEvent(signal="tout_out_valid", cycle=18, value="1") in events
    assert len(events) == 4  # 2 cycles x 2 probes


def test_load_wide_probe_csv_missing_file_raises(tmp_path):
    try:
        load_wide_probe_csv(tmp_path / "nope.csv")
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_measure_latency_works_on_wide_loaded_events(tmp_path):
    csv_path = tmp_path / "algo_top_probe.csv"
    csv_path.write_text(
        "cycle,in_valid,out_valid\n"
        "0,0,0\n"
        "5,1,0\n"
        "13,1,1\n"
    )
    events = load_wide_probe_csv(csv_path)
    assert measure_latency(events, "in_valid", "out_valid") == 8


def test_long_format_loader_is_unaffected_by_wide_loader_addition(tmp_path):
    """load_probe_csv (long format) must behave exactly as before —
    purely additive change."""
    csv_path = tmp_path / "probes.csv"
    csv_path.write_text(
        "cycle,signal,value\n"
        "10,dec_0_raw_valid,1\n"
        "18,tout_out_valid,1\n"
    )
    events = load_probe_csv(csv_path)
    assert len(events) == 2
    assert events[0].signal == "dec_0_raw_valid"


def test_compare_populates_predicted_and_observed_value_wrappers():
    c = compare("my_module", hls_predicted=5, observed=7)
    assert c.hls_predicted == 5
    assert c.observed == 7
    assert c.delta == 2
    assert c.verdict == "over"
    assert c.predicted is not None
    assert c.predicted.cycles == 5
    assert c.predicted.provenance.source == "hls_report"
    assert c.observed_value is not None
    assert c.observed_value.cycles == 7
    assert c.observed_value.provenance.source == "runtime_observation"


def test_compare_predicted_source_is_configurable():
    c = compare("my_module", hls_predicted=3, observed=3, predicted_source="explicit_contract")
    assert c.predicted.provenance.source == "explicit_contract"


def test_compare_path_is_optional_and_defaults_to_none():
    c = compare("my_module", hls_predicted=3, observed=3)
    assert c.path is None

    c2 = compare("my_module", hls_predicted=3, observed=3, path=["a", "b", "my_module"])
    assert c2.path == ["a", "b", "my_module"]


def test_compare_unknown_case_still_populates_wrappers_when_available():
    """One side missing (unknown verdict) — the side that IS known should
    still get its LatencyValue wrapper populated."""
    c = compare("my_module", hls_predicted=5, observed=None)
    assert c.verdict == "unknown"
    assert c.predicted is not None
    assert c.predicted.cycles == 5
    assert c.observed_value is None
