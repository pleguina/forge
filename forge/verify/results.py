#!/usr/bin/env python3
"""Versioned, structured per-flow/per-event verification results (Phase 7,
slice 7.2 — §7.5 core).

Before this module, `forge test run`'s per-event loop
(`forge/core/cli/groups/test.py::cmd_run`) built a bare `dict` per event and
threw away everything but three printed counters — no structured
`backend_id`, no real duration, no waveform path, and log selection for a
failed event always read `simulate_log` even when the failure happened at
`xvlog`/`xelab`/`verilate` time (`ExecutionResult.stage`, added in slice
7.0, is the direct fix for that).

`ArtifactSchema` is the shared version tag every structured artifact this
phase introduces (results, and — slice 7.4a — dataset envelopes) carries,
so a future reader can tell which shape it is looking at before assuming
field names.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from forge.core.artifact_schema import ArtifactSchema
from forge.verify.diagnostics import Diagnostic, Severity
from forge.verify.execution_stage import ExecutionStage


# ── Schema tag ───────────────────────────────────────────────────────────
#
# ArtifactSchema itself now lives in forge.core.artifact_schema (release-
# plan Phase 8, slice 8.0A) — a neutral, dependency-free location, since
# the concept has nothing to do with verification specifically. Re-
# imported here so every existing `from forge.verify.results import
# ArtifactSchema` call site keeps working unchanged.

RESULTS_SCHEMA = ArtifactSchema("forge.verification_results", "1.0")


# ── Artifact reference ──────────────────────────────────────────────────

@dataclass(frozen=True)
class ArtifactRef:
    """One artifact produced by a backend run, with its provenance.

    ``stage`` is the real ``ExecutionStage`` that produced this artifact
    when known (e.g. the log a failure's ``ExecutionResult.log_path``
    actually points at), and ``None`` when the artifact's producing stage
    isn't tracked at this granularity (most of a backend's
    ``describe_backend_outputs()`` map, which only declares paths, not
    which stage writes each one) — never guessed.
    """
    path:  str
    stage: "ExecutionStage | None"
    kind:  str  # e.g. "log", "waveform", "probe_csv"

    def to_dict(self) -> dict[str, Any]:
        return {
            "path":  self.path,
            "stage": self.stage.value if self.stage is not None else None,
            "kind":  self.kind,
        }


# ── Machine-readable check records (slice 7.3) ──────────────────────────

@dataclass(frozen=True)
class CheckResult:
    """One parsed ``FORGE_CHECK|...`` log record.

    Populated on **both** outcomes — a passing check's ``expected`` and
    ``observed`` are both real values read from the log, not inferred from
    the absence of a failure (the corrected slice 7.3 design; the first
    draft's fail-only-line design could not have produced ``observed`` for
    a passing check at all).
    """
    check_id: str
    label:    str
    signal:   "str | None"
    expected: str
    observed: str
    width:    "int | None"
    passed:   bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "label":    self.label,
            "signal":   self.signal,
            "expected": self.expected,
            "observed": self.observed,
            "width":    self.width,
            "passed":   self.passed,
        }


_FORGE_CHECK_PREFIX = "FORGE_CHECK|"


def parse_forge_check_lines(text: str) -> "list[CheckResult]":
    """Parse every ``FORGE_CHECK|key=value|...`` line in *text* into real
    :class:`CheckResult` objects.

    Only lines containing the literal ``FORGE_CHECK|`` marker are parsed —
    never the human-readable ``FAIL: ... — got ...`` line (free-form
    English, correctly flagged as fragile to parse and not attempted here).
    Malformed records (missing ``check_id``) are silently skipped rather
    than raising, since a log is allowed to contain other output around
    each record.
    """
    results: list[CheckResult] = []
    for line in text.splitlines():
        idx = line.find(_FORGE_CHECK_PREFIX)
        if idx == -1:
            continue
        payload = line[idx + len(_FORGE_CHECK_PREFIX):]
        fields: dict[str, str] = {}
        for part in payload.split("|"):
            key, sep, value = part.partition("=")
            if sep:
                fields[key] = value
        if "check_id" not in fields:
            continue
        width_str = fields.get("width", "")
        results.append(CheckResult(
            check_id=fields["check_id"],
            label=fields.get("label", ""),
            signal=fields.get("signal") or None,
            expected=fields.get("expected", ""),
            observed=fields.get("observed", ""),
            width=int(width_str) if width_str.lstrip("-").isdigit() else None,
            passed=fields.get("passed") == "1",
        ))
    return results


# ── Verification-target schema (release-plan Phase 8, Defect 4) ─────────

@dataclass(frozen=True)
class VerificationTarget:
    """A real, provable claim about what one event's simulation actually
    covered, keyed into the same ``ObjectReference`` kind vocabulary the
    visual design explorer uses (``forge.analyze.design_explorer.graph_model``).

    Additive and schema-only this phase — no real emission site populates
    it yet (see the honest deferral in the release plan's Phase 8 notes).
    A flow's declared ``top_module`` is a real, provable fact ("this flow's
    entry point is this module") but does **not** by itself prove every
    reachable instance was independently, behaviorally exercised — that
    stronger claim is exactly what a populated ``VerificationTarget``
    would represent, once a plugin's flow declaration says what it
    actually covers.
    """
    object_kind:   str  # matches ObjectReference.kind's vocabulary
    object_id:     str
    coverage_kind: str  # "direct-dut" | "included-in-simulation" | "observed-by-probe" | "checked-output" | "not-declared"

    def to_dict(self) -> dict[str, str]:
        return {
            "object_kind":   self.object_kind,
            "object_id":     self.object_id,
            "coverage_kind": self.coverage_kind,
        }


# ── Per-event result ─────────────────────────────────────────────────────

@dataclass
class EventResult:
    """The real, structured result of simulating one event.

    ``event_id`` is always a string (matching the dataset corrections in
    slice 7.4a — real external identifiers are not guaranteed to be small
    contiguous integers). ``event_index`` is the internal, always-numeric
    position used for memory-indexed stimulus addressing (slice 7.5); it
    stays honestly ``None`` until slice 7.4a's dataset envelope exists to
    define it for real — never fabricated from loop position.
    ``checks`` is populated (slice 7.3) by scanning the event's real
    ``simulate_log`` for ``FORGE_CHECK|`` records — only for checks emitted
    via ``StimulusEmitter.check()``/``emit_output_check`` (an honest,
    documented gap for hand-written SV checks and binary checkers that
    don't adopt the same log-line convention); an empty list means "no
    ``FORGE_CHECK|`` records found", never "no checks ran".
    """
    event_id:    str
    event_index: "int | None"
    success:     bool
    backend_id:  str
    duration_s:  "float | None"
    artifacts:   "list[ArtifactRef]" = field(default_factory=list)
    diagnostics: "list[Diagnostic]"  = field(default_factory=list)
    checks:      "list[CheckResult]" = field(default_factory=list)
    # Additive, empty by default (release-plan Phase 8, Defect 4) — every
    # existing Phase 7 EventResult remains valid and simply carries no
    # targets until a future slice populates them for real.
    targets:     "list[VerificationTarget]" = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id":    self.event_id,
            "event_index": self.event_index,
            "success":     self.success,
            "backend_id":  self.backend_id,
            "duration_s":  self.duration_s,
            "artifacts":   [a.to_dict() for a in self.artifacts],
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "checks":      [c.to_dict() for c in self.checks],
            "targets":     [t.to_dict() for t in self.targets],
        }


# ── Per-flow result ───────────────────────────────────────────────────────

@dataclass
class FlowResult:
    """The real, structured result of one ``forge test run`` invocation —
    every event it simulated, plus the schema tag every consumer should
    check before assuming this shape."""
    schema:     ArtifactSchema
    flow_name:  str
    backend_id: str
    events:     "list[EventResult]" = field(default_factory=list)

    @property
    def success(self) -> bool:
        return all(e.success for e in self.events)

    @property
    def duration_s(self) -> float:
        """Aggregate wall-clock time — the sum of every event's real duration."""
        return sum(e.duration_s or 0.0 for e in self.events)

    @property
    def artifacts(self) -> "list[ArtifactRef]":
        """The union of every event's artifacts, de-duplicated by path,
        order-preserving."""
        seen: set[str] = set()
        out: list[ArtifactRef] = []
        for ev in self.events:
            for art in ev.artifacts:
                if art.path not in seen:
                    seen.add(art.path)
                    out.append(art)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema":     self.schema.to_dict(),
            "flow_name":  self.flow_name,
            "backend_id": self.backend_id,
            "success":    self.success,
            "duration_s": self.duration_s,
            "events":     [e.to_dict() for e in self.events],
            "artifacts":  [a.to_dict() for a in self.artifacts],
        }


# ── Diagnostic construction ─────────────────────────────────────────────

def diagnostic_for_event_failure(
    *,
    event_id: str,
    flow_name: str,
    stage: "ExecutionStage | None",
    checker_ok: "bool | None",
    backend_success: bool,
    log_path: "str | None",
    log_tail: "str | None" = None,
) -> Diagnostic:
    """Return the correctly-coded ``Diagnostic`` for one failed event.

    Closes the confirmed "hardcodes FWV013 regardless of cause" bug: a
    compile/elaborate failure, a checker-only failure (simulator exited 0
    but the checker rejected the result), and a genuine simulate-stage
    non-zero exit are three different real causes and now get three
    different codes, each tagged with the real stage that produced it.

    ``log_tail`` (when given) is carried in ``context["log_tail"]`` — the
    richer, human-oriented detail (JUnit's `<failure>` text uses this),
    kept separate from ``message`` (the short, stable, code-driven summary).
    """
    context: dict[str, Any] = {"event_id": event_id}
    if stage is not None:
        context["stage"] = stage.value
    if log_tail:
        context["log_tail"] = log_tail

    if checker_ok is False and backend_success:
        return Diagnostic(
            code="FWV022", severity=Severity.ERROR,
            message=f"event {event_id}: checker rejected the result (simulator exited 0)",
            path=log_path or "", flow=flow_name, context=context,
        )

    if stage in (ExecutionStage.COMPILE, ExecutionStage.ELABORATE):
        return Diagnostic(
            code="FWV021", severity=Severity.ERROR,
            message=f"event {event_id}: {stage.value} failed",
            path=log_path or "", flow=flow_name, context=context,
        )

    if stage == ExecutionStage.PREFLIGHT:
        return Diagnostic(
            code="FWV011", severity=Severity.ERROR,
            message=f"event {event_id}: backend pre-execution validation failed",
            flow=flow_name, context=context,
        )

    # Default: a genuine simulate-stage non-zero exit, or a stage we
    # genuinely don't know (the backend raised before returning a result).
    return Diagnostic(
        code="FWV013", severity=Severity.ERROR,
        message=f"event {event_id}: simulation/checker failed",
        path=log_path or "", flow=flow_name, context=context,
    )


# ── Markdown rendering (for `forge report`) ─────────────────────────────

def render_results_markdown(payload: "dict[str, Any]") -> str:
    """Render a ``FlowResult.to_dict()`` payload (as read back from a
    ``--results-json`` file) as Markdown for ``forge report``.

    Checks ``payload["schema"]["name"]``/``["version"]`` against what this
    version of the renderer expects — a real, if minimal,
    forward-compatibility check, not a silent assumption of shape. An
    unrecognised schema is reported honestly rather than rendered as if
    it were understood.
    """
    schema = payload.get("schema") or {}
    if schema.get("name") != RESULTS_SCHEMA.name:
        return (
            "# Verification results\n\n"
            f"Unrecognised results schema: {schema.get('name')!r} "
            f"(expected {RESULTS_SCHEMA.name!r}) — cannot render.\n"
        )
    if schema.get("version") != RESULTS_SCHEMA.version:
        return (
            "# Verification results\n\n"
            f"Unsupported {RESULTS_SCHEMA.name} schema version: "
            f"{schema.get('version')!r} (this renderer understands "
            f"{RESULTS_SCHEMA.version!r}) — cannot render.\n"
        )

    events = payload.get("events") or []
    lines = [
        "# Verification results",
        "",
        f"- **flow**: {payload.get('flow_name', '(unknown)')}",
        f"- **backend**: {payload.get('backend_id', '(unknown)')}",
        f"- **status**: {'PASS' if payload.get('success') else 'FAIL'}",
        f"- **duration**: {payload.get('duration_s', 0.0):.3f}s",
        f"- **events**: {len(events)}",
        "",
        "| event | result | backend | duration (s) | waveform |",
        "|---|---|---|---|---|",
    ]
    for ev in events:
        result = "PASS" if ev.get("success") else "FAIL"
        waveform = next(
            (a["path"] for a in (ev.get("artifacts") or []) if a.get("kind") == "waveform"),
            "",
        )
        lines.append(
            f"| {ev.get('event_id')} | {result} | {ev.get('backend_id', '')} | "
            f"{ev.get('duration_s') or 0.0:.3f} | {waveform} |"
        )

    failed = [ev for ev in events if not ev.get("success")]
    if failed:
        lines += ["", "## Failures", ""]
        for ev in failed:
            for diag in ev.get("diagnostics") or []:
                lines.append(f"- **{ev.get('event_id')}** [{diag.get('code')}] {diag.get('message')}")

    return "\n".join(lines) + "\n"
