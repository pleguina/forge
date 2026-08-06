"""Coverage for `forge.core.cli.envelope`:
the shared CommandEnvelope shape and exit-code policy every CLI command is
migrated onto.
"""

from __future__ import annotations

import json

import pytest

from forge.core.cli.envelope import (
    CommandEnvelope,
    ENVELOPE_SCHEMA_VERSION,
    emit,
    from_diagnostic_report,
    render_human,
)


# ── CommandEnvelope basics ──────────────────────────────────────────────────

def test_invalid_status_rejected() -> None:
    with pytest.raises(ValueError):
        CommandEnvelope(status="ok")  # type: ignore[arg-type]


def test_to_dict_from_dict_round_trip() -> None:
    envelope = CommandEnvelope(
        status="warn",
        diagnostics=[{"code": "FWV001", "severity": "warning", "message": "m"}],
        artifacts=["out/a.json"],
        metrics={"count": 3},
        next_actions=["do the thing"],
    )
    payload = envelope.to_dict()
    assert payload["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert payload["status"] == "warn"

    restored = CommandEnvelope.from_dict(payload)
    assert restored == envelope


@pytest.mark.parametrize(
    "status,strict,expected",
    [
        ("pass", False, 0),
        ("pass", True, 0),
        ("warn", False, 0),
        ("warn", True, 1),
        ("fail", False, 1),
        ("fail", True, 1),
        ("error", False, 2),
        ("error", True, 2),
    ],
)
def test_exit_code_mapping(status: str, strict: bool, expected: int) -> None:
    envelope = CommandEnvelope(status=status)
    assert envelope.exit_code(strict=strict) == expected


# ── from_diagnostic_report ──────────────────────────────────────────────────

class _FakeDiagnostic:
    def __init__(self, severity: str, action: str = "") -> None:
        self.severity = severity
        self.action = action


class _FakeReport:
    """A minimal double matching the shape both DiagnosticReport and
    ATGDiagnosticReport expose: .ok / .ok_strict / .errors / .warnings /
    .to_dict()["diagnostics"]."""

    def __init__(self, diagnostics: list[dict]) -> None:
        self._diagnostics = diagnostics
        self.label = "fake.yml"

    @property
    def errors(self) -> list:
        return [d for d in self._diagnostics if d["severity"] in ("error", "critical")]

    @property
    def warnings(self) -> list:
        return [d for d in self._diagnostics if d["severity"] == "warning"]

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    @property
    def ok_strict(self) -> bool:
        return self.ok and len(self.warnings) == 0

    def to_dict(self) -> dict:
        return {"diagnostics": self._diagnostics}


def test_status_pass_when_no_warnings_or_errors() -> None:
    report = _FakeReport([{"code": "X001", "severity": "note", "message": "all good"}])
    envelope = from_diagnostic_report(report)
    assert envelope.status == "pass"


def test_status_warn_when_only_warnings_closes_latent_bug() -> None:
    # Pre-Phase-6, DiagnosticReport.to_dict() only ever emitted "pass"/"fail"
    # even when warnings (no errors) were present. This is the regression
    # guard for that fix.
    report = _FakeReport([
        {"code": "X002", "severity": "warning", "message": "careful", "action": "fix it"},
    ])
    envelope = from_diagnostic_report(report)
    assert envelope.status == "warn"


def test_status_fail_when_errors_present() -> None:
    report = _FakeReport([
        {"code": "X003", "severity": "warning", "message": "careful"},
        {"code": "X004", "severity": "error", "message": "broken", "action": "fix the break"},
    ])
    envelope = from_diagnostic_report(report)
    assert envelope.status == "fail"


def test_next_actions_deduplicated_preserving_order() -> None:
    report = _FakeReport([
        {"code": "A", "severity": "warning", "message": "m1", "action": "do X"},
        {"code": "B", "severity": "error", "message": "m2", "action": "do Y"},
        {"code": "C", "severity": "error", "message": "m3", "action": "do X"},
        {"code": "D", "severity": "note", "message": "m4", "action": "do Z (note, ignored)"},
    ])
    envelope = from_diagnostic_report(report)
    assert envelope.next_actions == ["do X", "do Y"]


def test_metrics_carry_counts_and_label() -> None:
    report = _FakeReport([
        {"code": "A", "severity": "note", "message": "m1"},
        {"code": "B", "severity": "warning", "message": "m2"},
    ])
    envelope = from_diagnostic_report(report, metrics={"extra": 1})
    assert envelope.metrics["counts"] == {"notes": 1, "warnings": 1, "errors": 0}
    assert envelope.metrics["label"] == "fake.yml"
    assert envelope.metrics["extra"] == 1


def test_artifacts_passed_through() -> None:
    report = _FakeReport([])
    envelope = from_diagnostic_report(report, artifacts=["a.json", "b.xml"])
    assert envelope.artifacts == ["a.json", "b.xml"]


# ── emit / render_human ─────────────────────────────────────────────────────

def test_emit_json_mode_prints_full_envelope(capsys: pytest.CaptureFixture[str]) -> None:
    envelope = CommandEnvelope(status="pass")
    code = emit(envelope, json_mode=True)
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "pass"


def test_emit_human_mode_routes_errors_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    envelope = CommandEnvelope(
        status="fail",
        diagnostics=[
            {"code": "N1", "severity": "note", "message": "fine"},
            {"code": "E1", "severity": "error", "message": "broken", "action": "fix it"},
        ],
    )
    code = emit(envelope, json_mode=False)
    captured = capsys.readouterr()
    assert code == 1
    assert "fine" in captured.out
    assert "broken" in captured.err
    assert "fix it" in captured.err


def test_emit_strict_promotes_warn_to_exit_1_with_note(capsys: pytest.CaptureFixture[str]) -> None:
    envelope = CommandEnvelope(
        status="warn",
        diagnostics=[{"code": "W1", "severity": "warning", "message": "careful"}],
    )
    code = emit(envelope, json_mode=False, strict=True)
    captured = capsys.readouterr()
    assert code == 1
    assert "--strict" in captured.err


def test_render_human_is_single_string_with_uppercase_status() -> None:
    envelope = CommandEnvelope(status="pass")
    text = render_human(envelope)
    assert "status: PASS" in text
