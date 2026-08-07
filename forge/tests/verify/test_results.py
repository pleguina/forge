"""Unit tests for forge.verification.results.

Fast, non-simulator unit coverage for the dataclasses, their `to_dict()`
serialisation, the stage-aware diagnostic-code selection, and Markdown
rendering. Real end-to-end xsim/verilator coverage of the wiring into
`forge test run` lives in tests/test_test_cli_group.py.
"""

from __future__ import annotations

from forge.verification.diagnostics import Severity
from forge.verification.execution_stage import ExecutionStage
from forge.verification.results import (
    RESULTS_SCHEMA,
    ArtifactRef,
    ArtifactSchema,
    EventResult,
    FlowResult,
    diagnostic_for_event_failure,
    render_results_markdown,
)


# ── ArtifactSchema / ArtifactRef ────────────────────────────────────────

def test_artifact_schema_round_trip():
    schema = ArtifactSchema(name="forge.verification_results", version="1.0")
    assert schema.to_dict() == {"name": "forge.verification_results", "version": "1.0"}


def test_artifact_ref_serialises_stage_as_string_value():
    ref = ArtifactRef(path="/tmp/simulate.log", stage=ExecutionStage.SIMULATE, kind="log")
    assert ref.to_dict() == {"path": "/tmp/simulate.log", "stage": "simulate", "kind": "log"}


def test_artifact_ref_stage_none_serialises_to_none():
    ref = ArtifactRef(path="/tmp/x.log", stage=None, kind="log")
    assert ref.to_dict()["stage"] is None


# ── EventResult / FlowResult ────────────────────────────────────────────

def _passing_event(event_id: str, duration: float, backend: str = "xsim") -> EventResult:
    return EventResult(
        event_id=event_id, event_index=None, success=True, backend_id=backend,
        duration_s=duration,
        artifacts=[ArtifactRef(path=f"/work/{event_id}/simulate.log", stage=ExecutionStage.SIMULATE, kind="log")],
    )


def test_event_result_to_dict_shape():
    ev = _passing_event("0", 1.5)
    d = ev.to_dict()
    assert d["event_id"] == "0"
    assert d["event_index"] is None
    assert d["success"] is True
    assert d["backend_id"] == "xsim"
    assert d["duration_s"] == 1.5
    assert d["checks"] == []
    assert d["diagnostics"] == []
    assert len(d["artifacts"]) == 1


def test_flow_result_schema_round_trip():
    flow = FlowResult(schema=RESULTS_SCHEMA, flow_name="passthrough_xsim", backend_id="xsim",
                       events=[_passing_event("0", 1.0)])
    payload = flow.to_dict()
    assert payload["schema"] == {"name": "forge.verification_results", "version": "1.0"}
    # Round-trip: reconstruct the schema tag from the serialised dict and
    # confirm it survives byte-for-byte (the discipline already established
    # for CommandEnvelope).
    schema_back = ArtifactSchema(**payload["schema"])
    assert schema_back == RESULTS_SCHEMA


def test_flow_result_success_true_only_when_every_event_passes():
    flow = FlowResult(schema=RESULTS_SCHEMA, flow_name="f", backend_id="xsim", events=[
        _passing_event("0", 1.0), _passing_event("1", 1.0),
    ])
    assert flow.success is True

    failing = EventResult(event_id="2", event_index=None, success=False, backend_id="xsim", duration_s=0.5)
    flow.events.append(failing)
    assert flow.success is False


def test_flow_result_duration_s_sums_real_event_durations():
    flow = FlowResult(schema=RESULTS_SCHEMA, flow_name="f", backend_id="xsim", events=[
        _passing_event("0", 1.5), _passing_event("1", 2.5),
    ])
    assert flow.duration_s == 4.0


def test_flow_result_duration_s_treats_missing_duration_as_zero():
    ev = EventResult(event_id="0", event_index=None, success=True, backend_id="xsim", duration_s=None)
    flow = FlowResult(schema=RESULTS_SCHEMA, flow_name="f", backend_id="xsim", events=[ev])
    assert flow.duration_s == 0.0


def test_flow_result_artifacts_deduplicated_by_path_order_preserving():
    shared = ArtifactRef(path="/work/shared.log", stage=None, kind="log")
    ev0 = EventResult(event_id="0", event_index=None, success=True, backend_id="xsim",
                       duration_s=1.0, artifacts=[shared])
    ev1 = EventResult(event_id="1", event_index=None, success=True, backend_id="xsim",
                       duration_s=1.0, artifacts=[shared, ArtifactRef(path="/work/1/simulate.log", stage=None, kind="log")])
    flow = FlowResult(schema=RESULTS_SCHEMA, flow_name="f", backend_id="xsim", events=[ev0, ev1])
    paths = [a.path for a in flow.artifacts]
    assert paths == ["/work/shared.log", "/work/1/simulate.log"]


# ── diagnostic_for_event_failure — the stage-aware code fix ─────────────

def test_diagnostic_compile_stage_gets_fwv021():
    diag = diagnostic_for_event_failure(
        event_id="0", flow_name="f", stage=ExecutionStage.COMPILE,
        checker_ok=None, backend_success=False, log_path="/work/xvlog.log",
    )
    assert diag.code == "FWV021"
    assert diag.severity == Severity.ERROR
    assert diag.context["stage"] == "compile"


def test_diagnostic_elaborate_stage_also_gets_fwv021():
    diag = diagnostic_for_event_failure(
        event_id="0", flow_name="f", stage=ExecutionStage.ELABORATE,
        checker_ok=None, backend_success=False, log_path="/work/xelab.log",
    )
    assert diag.code == "FWV021"


def test_diagnostic_preflight_stage_gets_fwv011():
    diag = diagnostic_for_event_failure(
        event_id="0", flow_name="f", stage=ExecutionStage.PREFLIGHT,
        checker_ok=None, backend_success=False, log_path=None,
    )
    assert diag.code == "FWV011"


def test_diagnostic_checker_only_failure_gets_fwv022_even_though_backend_succeeded():
    """The direct fix for the confirmed bug: simulator exit 0 + checker
    reject must not be reported as FWV013 (simulate-stage exit)."""
    diag = diagnostic_for_event_failure(
        event_id="0", flow_name="f", stage=ExecutionStage.SIMULATE,
        checker_ok=False, backend_success=True, log_path="/work/simulate.log",
    )
    assert diag.code == "FWV022"


def test_diagnostic_genuine_simulate_exit_gets_fwv013():
    diag = diagnostic_for_event_failure(
        event_id="0", flow_name="f", stage=ExecutionStage.SIMULATE,
        checker_ok=None, backend_success=False, log_path="/work/simulate.log",
    )
    assert diag.code == "FWV013"


def test_diagnostic_unknown_stage_falls_back_to_fwv013():
    diag = diagnostic_for_event_failure(
        event_id="0", flow_name="f", stage=None,
        checker_ok=None, backend_success=False, log_path=None,
    )
    assert diag.code == "FWV013"


def test_diagnostic_log_tail_goes_in_context_not_message():
    diag = diagnostic_for_event_failure(
        event_id="0", flow_name="f", stage=ExecutionStage.SIMULATE,
        checker_ok=None, backend_success=False, log_path="/work/simulate.log",
        log_tail="FAIL: data_out_check — expected 0x3A, got 0x00",
    )
    assert diag.context["log_tail"] == "FAIL: data_out_check — expected 0x3A, got 0x00"
    assert "expected 0x3A" not in diag.message


# ── render_results_markdown ─────────────────────────────────────────────

def test_render_results_markdown_real_shape():
    flow = FlowResult(schema=RESULTS_SCHEMA, flow_name="passthrough_xsim", backend_id="xsim", events=[
        _passing_event("0", 1.5),
    ])
    md = render_results_markdown(flow.to_dict())
    assert "# Verification results" in md
    assert "passthrough_xsim" in md
    assert "PASS" in md
    assert "0" in md  # event id


def test_render_results_markdown_rejects_unrecognised_schema_name():
    md = render_results_markdown({"schema": {"name": "something.else", "version": "1.0"}})
    assert "Unrecognised results schema" in md


def test_render_results_markdown_rejects_unsupported_schema_version():
    md = render_results_markdown({"schema": {"name": RESULTS_SCHEMA.name, "version": "99.0"}})
    assert "Unsupported" in md


def test_render_results_markdown_lists_failures_section():
    failing = EventResult(event_id="1", event_index=None, success=False, backend_id="xsim", duration_s=0.1)
    failing.diagnostics = [diagnostic_for_event_failure(
        event_id="1", flow_name="f", stage=ExecutionStage.COMPILE,
        checker_ok=None, backend_success=False, log_path=None,
    )]
    flow = FlowResult(schema=RESULTS_SCHEMA, flow_name="f", backend_id="xsim", events=[failing])
    md = render_results_markdown(flow.to_dict())
    assert "## Failures" in md
    assert "FWV021" in md
