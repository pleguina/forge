#!/usr/bin/env python3
"""``forge.cdc_verification_result.v1`` — a structured, versioned wrapper
around :func:`forge.topgen.ip.cdc.report_all_crossings`'s structural
clock/reset-domain-crossing check (release-plan Phase 10, slice 10.0C —
preflight.md §8).

Scope, resolved explicitly (the preflight text alone under-specifies
this): the only real CDC verification in the repository today is
structural and design-time — did a wired connection between two modules
resolved to different clock/reset domains declare an approved ``cdc:``
adapter (or, for a reset crossing, a ``reset_domains.*.sync:
reset_sync`` entry)? There is no simulation-based behavioral CDC
checking anywhere yet (level-transfer correctness, no-loss/no-duplication
streaming, etc.) — those real behavioral verification flows
(``cdc_level``/``cdc_pulse``/``cdc_mailbox``/``cdc_stream``) are scoped to
a later slice (10.4) and don't exist yet. So every entry's
``property_checked`` here describes the structural declaration check,
never a behavioral property — this artifact is not claiming more than
``verify_cdc``/``report_all_crossings`` actually verify today.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from forge.core.artifact_schema import ArtifactSchema

CDC_VERIFICATION_RESULT_SCHEMA = ArtifactSchema("forge.cdc_verification_result", "1.0")


@dataclass(frozen=True)
class CdcCrossingResult:
    """One wired module-pair connection's structural CDC check.

    ``kind`` is the declared ``cdc.kind`` (already normalized to its
    canonical name, e.g. ``level_sync`` not ``2ff_sync``) for an approved
    crossing, or ``None`` when no ``cdc:`` was declared at all (whether
    or not that's actually a problem — same-domain connections have no
    kind and are still ``passed=True``).
    """
    connection:         str   # "{src_mod}->{dst_mod}"
    kind:               "str | None"
    source_clock_domain:      "str | None"
    destination_clock_domain: "str | None"
    source_reset_domain:      "str | None"
    destination_reset_domain: "str | None"
    property_checked:   str
    passed:              bool
    message:              str

    def to_dict(self) -> "dict[str, Any]":
        return {
            "connection": self.connection,
            "kind": self.kind,
            "source_clock_domain": self.source_clock_domain,
            "destination_clock_domain": self.destination_clock_domain,
            "source_reset_domain": self.source_reset_domain,
            "destination_reset_domain": self.destination_reset_domain,
            "property_checked": self.property_checked,
            "passed": self.passed,
            "message": self.message,
        }


@dataclass(frozen=True)
class CdcVerificationResult:
    """The ``forge.cdc_verification_result.v1`` artifact — one entry per
    wired module-pair connection in the design, not just the failing
    ones."""
    schema:      ArtifactSchema
    design_hash:  str
    crossings:   "list[CdcCrossingResult]" = field(default_factory=list)

    def to_dict(self) -> "dict[str, Any]":
        return {
            "schema": self.schema.to_dict(),
            "design_hash": self.design_hash,
            "crossings": [c.to_dict() for c in self.crossings],
        }


def build_cdc_verification_result(
    crossings: "list[dict[str, Any]]",
    design_hash: str,
) -> CdcVerificationResult:
    """Build the ``forge.cdc_verification_result.v1`` artifact from
    *crossings* — the plain-dict list
    :func:`forge.topgen.ip.cdc.report_all_crossings` returns.

    Takes already-computed plain dicts, not a live ``design_cfg``/
    ``match_report``/etc., and does not import ``forge.topgen`` itself —
    ``forge.topgen`` and ``forge.verify`` must never cross-import each
    other (``ci/import_direction_check.sh`` enforces this; they're
    independent subsystems composed by the CLI layer, not by each
    other). The CLI layer calls ``report_all_crossings(...)`` itself and
    passes its return value straight into this function.
    """
    return CdcVerificationResult(
        schema=CDC_VERIFICATION_RESULT_SCHEMA,
        design_hash=design_hash,
        crossings=[CdcCrossingResult(**c) for c in crossings],
    )


def render_cdc_verification_markdown(payload: "dict[str, Any]") -> str:
    """Render a ``CdcVerificationResult.to_dict()`` payload as Markdown for
    ``forge report`` — same schema-guard convention as
    ``forge.verify.results.render_results_markdown``."""
    schema = payload.get("schema") or {}
    if schema.get("name") != CDC_VERIFICATION_RESULT_SCHEMA.name:
        return (
            "# CDC Verification\n\n"
            f"Unrecognised CDC verification schema: {schema.get('name')!r} "
            f"(expected {CDC_VERIFICATION_RESULT_SCHEMA.name!r}) — cannot render.\n"
        )
    if schema.get("version") != CDC_VERIFICATION_RESULT_SCHEMA.version:
        return (
            "# CDC Verification\n\n"
            f"Unsupported {CDC_VERIFICATION_RESULT_SCHEMA.name} schema version: "
            f"{schema.get('version')!r} (this renderer understands "
            f"{CDC_VERIFICATION_RESULT_SCHEMA.version!r}) — cannot render.\n"
        )

    crossings = payload.get("crossings") or []
    lines = ["# CDC Verification", ""]
    if not crossings:
        lines.append("No clock/reset-domain crossings found in this design.\n")
        return "\n".join(lines)

    n_failed = sum(1 for c in crossings if not c["passed"])
    lines.append(f"- **crossings checked**: {len(crossings)} ({n_failed} failing)")
    lines += [
        "", "| Connection | Kind | Passed | Message |", "|---|---|---|---|",
    ]
    for c in crossings:
        status = "✅" if c["passed"] else "❌"
        lines.append(f"| {c['connection']} | {c['kind'] or '(none)'} | {status} | {c['message']} |")
    lines.append("")
    return "\n".join(lines)
