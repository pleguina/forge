"""Tests for forge.verify.golden_comparison_result.
"""
from __future__ import annotations

import json
from pathlib import Path

from forge.verify.golden_comparison_result import (
    GOLDEN_COMPARISON_RESULT_SCHEMA,
    GoldenComparisonResult,
    GoldenEventComparison,
    build_golden_comparison_result,
    render_golden_comparison_markdown,
)
from forge.verify.results import RESULTS_SCHEMA, CheckResult, EventResult, FlowResult


def test_schema_tag():
    assert GOLDEN_COMPARISON_RESULT_SCHEMA.name == "forge.golden_comparison_result"
    assert GOLDEN_COMPARISON_RESULT_SCHEMA.version == "1.0"


def _check(passed: bool) -> CheckResult:
    return CheckResult(
        check_id="0:normalized_pixel_check", label="normalized_pixel_check",
        signal="thresh_out_pixel", expected="0x00", observed="0x00" if passed else "0xff",
        width=8, passed=passed,
    )


def test_event_comparison_to_dict_roundtrips_fields():
    ev = GoldenEventComparison(event_id="0", passed=True, checks=[_check(True)])
    d = ev.to_dict()
    assert d["event_id"] == "0"
    assert d["passed"] is True
    assert len(d["checks"]) == 1


def test_comparison_result_to_dict_composes_events():
    result = GoldenComparisonResult(
        schema=GOLDEN_COMPARISON_RESULT_SCHEMA,
        provider_id="vision_pipeline.quickstart_normalizer_threshold",
        provider_version="1.0",
        expected_output_hash="deadbeef",
        events=[GoldenEventComparison(event_id="0", passed=True, checks=[_check(True)])],
    )
    d = result.to_dict()
    assert d["schema"] == {"name": "forge.golden_comparison_result", "version": "1.0"}
    assert d["provider_id"] == "vision_pipeline.quickstart_normalizer_threshold"
    assert len(d["events"]) == 1


def test_build_golden_comparison_result_joins_flow_result_with_sidecar(tmp_path: Path):
    sidecar_path = tmp_path / "golden_model_provenance.json"
    sidecar_path.write_text(json.dumps({
        "provider_id": "vision_pipeline.quickstart_normalizer_threshold",
        "provider_version": "1.0",
        "input_dataset_hash": "cafef00d",
        "output_hash": "deadbeef",
    }))

    flow_result = FlowResult(
        schema=RESULTS_SCHEMA, flow_name="quickstart_pipeline_xsim", backend_id="xsim",
        events=[
            EventResult(
                event_id="0", event_index=0, success=True, backend_id="xsim",
                duration_s=0.1, checks=[_check(True), _check(True)],
            ),
            EventResult(
                event_id="1", event_index=1, success=False, backend_id="xsim",
                duration_s=0.1, checks=[_check(True), _check(False)],
            ),
        ],
    )

    result = build_golden_comparison_result(flow_result, sidecar_path)

    assert result.provider_id == "vision_pipeline.quickstart_normalizer_threshold"
    assert result.provider_version == "1.0"
    assert result.expected_output_hash == "deadbeef"
    assert len(result.events) == 2
    assert result.events[0].passed is True
    assert result.events[1].passed is False


def test_build_golden_comparison_result_falls_back_to_event_success_when_no_checks(tmp_path: Path):
    """An event with an empty checks list (e.g. a hand-written SV
    checker that doesn't emit FORGE_CHECK| records) still gets a real
    passed value, from EventResult.success — never fabricated as True."""
    sidecar_path = tmp_path / "golden_model_provenance.json"
    sidecar_path.write_text(json.dumps({
        "provider_id": "p", "provider_version": "1.0",
        "input_dataset_hash": "x", "output_hash": "y",
    }))
    flow_result = FlowResult(
        schema=RESULTS_SCHEMA, flow_name="f", backend_id="xsim",
        events=[EventResult(event_id="0", event_index=0, success=False, backend_id="xsim", duration_s=0.1)],
    )
    result = build_golden_comparison_result(flow_result, sidecar_path)
    assert result.events[0].passed is False
    assert result.events[0].checks == []


def test_build_golden_comparison_result_raises_when_sidecar_missing(tmp_path: Path):
    flow_result = FlowResult(schema=RESULTS_SCHEMA, flow_name="f", backend_id="xsim", events=[])
    missing = tmp_path / "does_not_exist.json"
    try:
        build_golden_comparison_result(flow_result, missing)
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_render_golden_comparison_markdown_rejects_unrecognised_schema():
    md = render_golden_comparison_markdown({"schema": {"name": "not.golden", "version": "1.0"}})
    assert "Unrecognised" in md


def test_render_golden_comparison_markdown_renders_real_payload():
    result = GoldenComparisonResult(
        schema=GOLDEN_COMPARISON_RESULT_SCHEMA,
        provider_id="p", provider_version="1.0", expected_output_hash="deadbeef",
        events=[GoldenEventComparison(event_id="0", passed=True, checks=[_check(True)])],
    )
    md = render_golden_comparison_markdown(result.to_dict())
    assert "1/1 passed" in md
    assert "p v1.0" in md
