"""forge CLI shared output envelope (release-plan Phase 6, §6.7).

Every ``forge`` command that supports ``--json`` today invents its own
JSON shape (``{"checks","ok"}`` for ``doctor``, ``{"plan","plan_hash"}``
for ``build``, several incompatible shapes for ``inspect``, ...) and exit
code ``2`` means three different things across the CLI (a hard
contract-verification failure, a CLI usage error, an unexpected internal
exception). :class:`CommandEnvelope` is the one shape every command is
migrated onto (incrementally), and
:func:`emit`/:data:`EXIT_CODE_POLICY` is the one place exit codes are
decided, closing both inconsistencies at once.

This module is deliberately placed in ``forge/core/cli/`` rather than
``forge/core/`` — it is CLI-layer-only, importable from both
``forge/core/cli/groups/*`` and ``forge/verify/__main__.py`` (a separate
CLI entry point that does not import ``forge/core/cli/_shared.py``
today).

See ``docs/development/cli_exit_codes.md`` for the full exit-code policy.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TextIO

ENVELOPE_SCHEMA_VERSION = "0.1.0"

#: The four statuses a command can report. ``warn`` is a real, distinct
#: status (unlike the pre-Phase-6 ``DiagnosticReport.to_dict()``, which only
#: ever emitted ``pass``/``fail`` even though ``ok_strict`` distinguished a
#: warning-only report from a clean one — a latent bug this schema closes).
STATUSES = ("pass", "warn", "fail", "error")

#: status -> exit code when *not* running with ``--strict``.
#: ``--strict`` promotes ``warn`` to the same exit code as ``fail`` (1) —
#: callers apply that promotion via ``emit(..., strict=True)``.
_EXIT_CODES: Dict[str, int] = {
    "pass": 0,
    "warn": 0,
    "fail": 1,
    "error": 2,
}


@dataclass
class CommandEnvelope:
    """Uniform structured result for a ``forge`` CLI command.

    Attributes:
        status:       One of :data:`STATUSES`.
        diagnostics:  List of diagnostic dicts (same per-item shape as
                      ``Diagnostic.to_dict()``/``ATGDiagnostic.to_dict()`` —
                      ``code``/``severity``/``message``/optional
                      ``action``/``path``/``flow``/``context``).
        artifacts:    Paths of files this command wrote (as strings).
        metrics:      Free-form structured data specific to the command
                      (counts, summaries, query results) — not diagnostics,
                      not artifacts.
        next_actions: De-duplicated, ordered list of suggested next steps.
        schema_version: Envelope schema version, for consumers that need
                      to handle multiple envelope versions over time.
    """

    status: str
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    next_actions: List[str] = field(default_factory=list)
    schema_version: str = ENVELOPE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(
                f"invalid CommandEnvelope status {self.status!r}, "
                f"must be one of {STATUSES}"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "diagnostics": self.diagnostics,
            "artifacts": self.artifacts,
            "metrics": self.metrics,
            "next_actions": self.next_actions,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CommandEnvelope":
        return cls(
            status=data["status"],
            diagnostics=list(data.get("diagnostics", [])),
            artifacts=list(data.get("artifacts", [])),
            metrics=dict(data.get("metrics", {})),
            next_actions=list(data.get("next_actions", [])),
            schema_version=data.get("schema_version", ENVELOPE_SCHEMA_VERSION),
        )

    def exit_code(self, *, strict: bool = False) -> int:
        """Map ``status`` to a process exit code.

        ``error`` -> 2 is the *only* place exit code 2 is produced by any
        envelope-adopting command — closing the pre-Phase-6 inconsistency
        where ``2`` meant a hard contract-verification failure in one file,
        a CLI usage error in another, and an unexpected internal exception
        in a third.
        """
        if strict and self.status == "warn":
            return 1
        return _EXIT_CODES[self.status]


def _severity_of(diag: Dict[str, Any]) -> str:
    return str(diag.get("severity", "")).lower()


def from_diagnostic_report(
    report: Any,
    *,
    artifacts: Optional[List[str]] = None,
    metrics: Optional[Dict[str, Any]] = None,
    strict: bool = False,
) -> CommandEnvelope:
    """Adapt any ``DiagnosticReport``/``ATGDiagnosticReport``-shaped object
    (both expose ``.ok``, ``.ok_strict``, ``.errors``, ``.warnings``, and a
    ``.to_dict()`` whose ``"diagnostics"`` key is a list of per-item dicts)
    into a :class:`CommandEnvelope`.

    Status is derived from real WARNING/ERROR/CRITICAL counts rather than
    delegated to the source report's own ``to_dict()["status"]`` — this is
    what fixes the pre-Phase-6 bug where ``DiagnosticReport.to_dict()`` only
    ever emitted ``"pass"``/``"fail"`` (never ``"warn"``, even though
    ``ok_strict`` already distinguished the warning-only case).

    ``next_actions`` de-duplicates every non-empty ``action`` string from
    WARNING+ERROR+CRITICAL diagnostics, preserving first-seen order.
    """
    diagnostics = list(report.to_dict().get("diagnostics", []))

    has_error = len(report.errors) > 0
    has_warning = len(report.warnings) > 0
    if has_error:
        status = "fail"
    elif has_warning:
        status = "warn"
    else:
        status = "pass"

    next_actions: List[str] = []
    seen = set()
    counts = {"notes": 0, "warnings": 0, "errors": 0}
    for diag in diagnostics:
        sev = _severity_of(diag)
        if sev == "warning":
            counts["warnings"] += 1
        elif sev in ("error", "critical"):
            counts["errors"] += 1
        else:
            counts["notes"] += 1

        if sev not in ("warning", "error", "critical"):
            continue
        action = diag.get("action")
        if action and action not in seen:
            seen.add(action)
            next_actions.append(action)

    merged_metrics: Dict[str, Any] = {"counts": counts}
    label = getattr(report, "label", "") or getattr(report, "context_label", "")
    if label:
        merged_metrics["label"] = label
    merged_metrics.update(metrics or {})

    return CommandEnvelope(
        status=status,
        diagnostics=diagnostics,
        artifacts=list(artifacts or []),
        metrics=merged_metrics,
        next_actions=next_actions,
    )


_STATUS_ICON = {
    "pass": "✅",
    "warn": "⚠️",
    "fail": "❌",
    "error": "\U0001f4a5",
}


def _format_diag_line(diag: Dict[str, Any]) -> str:
    code = diag.get("code")
    prefix = f"[{code}] " if code else ""
    flow = diag.get("flow")
    flow_label = f"[{flow}] " if flow else ""
    sev = diag.get("severity", "")
    line = f"  {sev:<8} {prefix}{flow_label}{diag.get('message', '')}"
    action = diag.get("action")
    if action:
        line += f"\n           → {action}"
    return line


def _non_diagnostic_lines(envelope: CommandEnvelope) -> List[str]:
    lines: List[str] = []
    if envelope.metrics:
        lines.append("")
        lines.append("metrics:")
        for key, value in envelope.metrics.items():
            lines.append(f"  {key}: {value}")

    if envelope.artifacts:
        lines.append("")
        lines.append("artifacts:")
        for path in envelope.artifacts:
            lines.append(f"  {path}")

    if envelope.next_actions:
        lines.append("")
        lines.append("next actions:")
        for action in envelope.next_actions:
            lines.append(f"  - {action}")

    return lines


def render_human(envelope: CommandEnvelope) -> str:
    """Render *envelope* as a single human-readable text block.

    The human view is always derived from the structured
    :class:`CommandEnvelope` — never assembled independently — so the two
    representations can never silently drift apart (§6.7's requirement).
    Callers that need warnings/errors routed to stderr and everything else
    to stdout (the Unix convention this CLI already followed before Phase 6)
    should use :func:`emit` instead, which performs that split; this
    function returns one combined string for callers that don't need it
    (logging, tests, non-interactive consumers).
    """
    icon = _STATUS_ICON.get(envelope.status, "")
    lines: List[str] = [f"{icon} status: {envelope.status.upper()}"]

    if envelope.diagnostics:
        lines.append("")
        lines.append("diagnostics:")
        for diag in envelope.diagnostics:
            lines.append(_format_diag_line(diag))

    lines.extend(_non_diagnostic_lines(envelope))
    return "\n".join(lines)


def emit(
    envelope: CommandEnvelope,
    *,
    json_mode: bool,
    strict: bool = False,
    out: Optional[TextIO] = None,
    err: Optional[TextIO] = None,
) -> int:
    """Print *envelope* (JSON or human form) and return its exit code.

    JSON mode prints the full envelope as one JSON document to *out*.
    Human mode follows this CLI's pre-existing convention (matching
    ``DiagnosticReport.print_console``'s behavior): NOTE-severity
    diagnostics and the summary/metrics/artifacts/next-actions sections go
    to *out*; WARNING/ERROR/CRITICAL diagnostics go to *err*.
    """
    out = out or sys.stdout
    err = err or sys.stderr

    if json_mode:
        print(json.dumps(envelope.to_dict(), indent=2), file=out)
        return envelope.exit_code(strict=strict)

    icon = _STATUS_ICON.get(envelope.status, "")
    print(f"{icon} status: {envelope.status.upper()}", file=out)

    notes = [d for d in envelope.diagnostics if _severity_of(d) not in ("warning", "error", "critical")]
    warn_err = [d for d in envelope.diagnostics if _severity_of(d) in ("warning", "error", "critical")]

    if notes:
        print(file=out)
        print("diagnostics:", file=out)
        for diag in notes:
            print(_format_diag_line(diag), file=out)

    if warn_err:
        print(file=err)
        for diag in warn_err:
            print(_format_diag_line(diag), file=err)

    for line in _non_diagnostic_lines(envelope):
        print(line, file=out)

    if strict and envelope.status == "warn":
        print(
            f"\n[--strict] {sum(1 for d in envelope.diagnostics if _severity_of(d) == 'warning')} "
            "warning(s) treated as errors because --strict is set.",
            file=err,
        )

    return envelope.exit_code(strict=strict)
