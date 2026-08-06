#!/usr/bin/env python3
"""``forge.golden_comparison_result.v1`` — per-event pass/fail against a
:class:`~forge.verify.golden_model.GoldenModelProvider`'s output, plus the
provider identity/hash used to produce it.

This is genuinely new persistence, not a wrapper around something that
already existed on disk: ``run_golden_model()`` already
computes ``provider_id``/``provider_version``/``output_hash`` on its
returned :class:`~forge.verify.golden_model.ExpectedDataset`, but that
identity lived only in the Python process running a plugin's
``gen_stimulus.py`` at stimulus-generation time — never written to disk,
never joined with a real simulation's checks. :func:`write_provider_provenance`
(in ``forge.verify.golden_model``) closes that gap by writing a small
JSON sidecar next to the generated stimulus; :func:`build_golden_comparison_result`
here joins that sidecar with a real :class:`~forge.verify.results.FlowResult`
(whose per-event ``CheckResult``\\ s are already real and structured —
reused as-is, not reshaped).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forge.core.artifact_schema import ArtifactSchema
from forge.verify.results import CheckResult, FlowResult

GOLDEN_COMPARISON_RESULT_SCHEMA = ArtifactSchema("forge.golden_comparison_result", "1.0")


@dataclass(frozen=True)
class GoldenEventComparison:
    """One dataset event's real per-check results against the golden
    model's live output — ``checks`` is reused straight from ``EventResult``,
    not reshaped."""
    event_id:  str
    passed:     bool
    checks:    "list[CheckResult]" = field(default_factory=list)

    def to_dict(self) -> "dict[str, Any]":
        return {
            "event_id": self.event_id,
            "passed": self.passed,
            "checks": [c.to_dict() for c in self.checks],
        }


@dataclass(frozen=True)
class GoldenComparisonResult:
    """The ``forge.golden_comparison_result.v1`` artifact."""
    schema:                 ArtifactSchema
    provider_id:             str
    provider_version:        str
    expected_output_hash:   "str | None"
    events:                 "list[GoldenEventComparison]" = field(default_factory=list)

    def to_dict(self) -> "dict[str, Any]":
        return {
            "schema": self.schema.to_dict(),
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "expected_output_hash": self.expected_output_hash,
            "events": [e.to_dict() for e in self.events],
        }


def build_golden_comparison_result(
    flow_result: FlowResult, sidecar_path: Path,
) -> GoldenComparisonResult:
    """Join a real :class:`FlowResult`'s per-event checks with the
    provider-identity sidecar :func:`~forge.verify.golden_model.write_provider_provenance`
    wrote at stimulus-generation time.

    Raises:
        FileNotFoundError: *sidecar_path* doesn't exist — the plugin's
            ``gen_stimulus.py`` must run (and call
            ``write_provider_provenance``) before this.
    """
    sidecar = json.loads(sidecar_path.read_text())
    events = [
        GoldenEventComparison(
            event_id=str(ev.event_id),
            passed=all(c.passed for c in ev.checks) if ev.checks else ev.success,
            checks=list(ev.checks),
        )
        for ev in flow_result.events
    ]
    return GoldenComparisonResult(
        schema=GOLDEN_COMPARISON_RESULT_SCHEMA,
        provider_id=sidecar["provider_id"],
        provider_version=sidecar["provider_version"],
        expected_output_hash=sidecar.get("output_hash"),
        events=events,
    )


def render_golden_comparison_markdown(payload: "dict[str, Any]") -> str:
    """Render a ``GoldenComparisonResult.to_dict()`` payload as Markdown
    for ``forge report`` — same schema-guard convention as
    ``forge.verify.results.render_results_markdown``."""
    schema = payload.get("schema") or {}
    if schema.get("name") != GOLDEN_COMPARISON_RESULT_SCHEMA.name:
        return (
            "# Golden-Model Comparison\n\n"
            f"Unrecognised golden-comparison schema: {schema.get('name')!r} "
            f"(expected {GOLDEN_COMPARISON_RESULT_SCHEMA.name!r}) — cannot render.\n"
        )
    if schema.get("version") != GOLDEN_COMPARISON_RESULT_SCHEMA.version:
        return (
            "# Golden-Model Comparison\n\n"
            f"Unsupported {GOLDEN_COMPARISON_RESULT_SCHEMA.name} schema version: "
            f"{schema.get('version')!r} (this renderer understands "
            f"{GOLDEN_COMPARISON_RESULT_SCHEMA.version!r}) — cannot render.\n"
        )

    events = payload.get("events") or []
    n_passed = sum(1 for e in events if e["passed"])
    lines = [
        "# Golden-Model Comparison", "",
        f"- **provider**: {payload.get('provider_id')} v{payload.get('provider_version')}",
        f"- **expected-output hash**: {payload.get('expected_output_hash')}",
        f"- **events**: {n_passed}/{len(events)} passed",
        "",
    ]
    if events:
        lines += ["| Event | Passed | Checks |", "|---|---|---|"]
        for e in events:
            status = "✅" if e["passed"] else "❌"
            n_checks_passed = sum(1 for c in e["checks"] if c["passed"])
            lines.append(f"| {e['event_id']} | {status} | {n_checks_passed}/{len(e['checks'])} |")
        lines.append("")
    return "\n".join(lines)
