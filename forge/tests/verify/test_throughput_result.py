"""Tests for forge.verification.throughput_result (construction/serialization
only; builders are tested separately in
forge.analyze.throughput_static/throughput_runtime's own test files).
"""
from __future__ import annotations

from forge.verification.throughput_result import (
    THROUGHPUT_RESULT_SCHEMA,
    RuntimeThroughputResult,
    StaticThroughputAnalysis,
    ThroughputResult,
    render_throughput_markdown,
)


def test_schema_tag():
    assert THROUGHPUT_RESULT_SCHEMA.name == "forge.throughput_result"
    assert THROUGHPUT_RESULT_SCHEMA.version == "1.0"


def _static() -> StaticThroughputAnalysis:
    return StaticThroughputAnalysis(
        module_name="pixel_normalizer",
        clock_frequency_mhz=250.0,
        pipeline_ii=1,
        records_per_cycle=1.0,
        data_width_bits=8,
        nominal_capacity_records_per_sec=250_000_000.0,
    )


def _runtime() -> RuntimeThroughputResult:
    return RuntimeThroughputResult(
        fifo_object_id="xform:result_stream:async_fifo",
        accepted_transactions=1000,
        emitted_transactions=998,
        stall_cycles=12,
        high_water_mark=31,
        full_events=2,
        empty_events=5,
        measured_rate_records_per_sec=198_750_000.0,
        dropped_transactions=0,
        duplicated_transactions=0,
    )


def test_static_throughput_analysis_to_dict_roundtrips_fields():
    s = _static()
    d = s.to_dict()
    assert d["module_name"] == "pixel_normalizer"
    assert d["pipeline_ii"] == 1
    assert d["nominal_capacity_records_per_sec"] == 250_000_000.0


def test_runtime_throughput_result_to_dict_roundtrips_fields():
    r = _runtime()
    d = r.to_dict()
    assert d["fifo_object_id"] == "xform:result_stream:async_fifo"
    assert d["high_water_mark"] == 31
    assert d["dropped_transactions"] == 0


def test_throughput_result_composes_static_and_runtime():
    result = ThroughputResult(
        schema=THROUGHPUT_RESULT_SCHEMA,
        design_hash="abc123",
        dataset_hash="def456",
        scenario_hash="ghi789",
        static=[_static()],
        runtime=[_runtime()],
        predicted_rate=250_000_000.0,
        observed_rate=198_750_000.0,
        bottleneck="pixel_normalizer",
    )
    d = result.to_dict()
    assert d["schema"] == {"name": "forge.throughput_result", "version": "1.0"}
    assert d["design_hash"] == "abc123"
    assert len(d["static"]) == 1
    assert len(d["runtime"]) == 1
    assert d["bottleneck"] == "pixel_normalizer"


def test_throughput_result_defaults_are_empty_and_none():
    result = ThroughputResult(schema=THROUGHPUT_RESULT_SCHEMA, design_hash="abc123")
    assert result.static == []
    assert result.runtime == []
    assert result.dataset_hash is None
    assert result.scenario_hash is None
    assert result.predicted_rate is None
    assert result.observed_rate is None
    assert result.bottleneck is None


def test_render_throughput_markdown_rejects_unrecognised_schema():
    md = render_throughput_markdown({"schema": {"name": "not.throughput", "version": "1.0"}})
    assert "Unrecognised" in md

def test_render_throughput_markdown_rejects_unsupported_version():
    md = render_throughput_markdown({"schema": {"name": "forge.throughput_result", "version": "9.9"}})
    assert "Unsupported" in md


def test_render_throughput_markdown_renders_real_payload():
    result = ThroughputResult(
        schema=THROUGHPUT_RESULT_SCHEMA, design_hash="abc123",
        static=[_static()], runtime=[_runtime()],
        predicted_rate=250_000_000.0, bottleneck="pixel_normalizer",
    )
    md = render_throughput_markdown(result.to_dict())
    assert "pixel_normalizer" in md
    assert "bottleneck" in md
    assert "xform:result_stream:async_fifo" in md


def test_render_throughput_markdown_handles_no_data():
    result = ThroughputResult(schema=THROUGHPUT_RESULT_SCHEMA, design_hash="abc123")
    md = render_throughput_markdown(result.to_dict())
    assert "No static or runtime" in md
