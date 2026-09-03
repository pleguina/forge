"""topgen.diagnostics — Structured diagnostics with stable ATGxxxx codes (B2).

Mirrors the design of ``forge.verification.diagnostics`` so that both frameworks emit
consistently structured findings that can be parsed by CI tools, IDEs, and
automated support systems.

Stable code table
-----------------
ATG001  Missing design contract (design.yml not found / not loadable)
ATG002  Ambiguous topology (multiple modules match the same port)
ATG003  Unsupported wiring type (rpc, compat_mode in strict context)
ATG004  Missing partition / no contract for module
ATG005  Open output port (no connection described)
ATG006  Stale ip_info.yaml (older than design.yml or IP sources)
ATG007  Stale generated artifacts (algo_top.v older than design.yml)
ATG008  Registry / modules.yml not found or invalid
ATG009  Non-strict topology (compat_mode or auto-match in use)
ATG010  Port range wiring in use (non-canonical)
ATG011  Hash mismatch (port_signature.json differs from generated port_map)
ATG012  gen-top --strict flag not set (risk of silent topology drift)
ATG013  Missing maturity_report.json (release gate not generated)
ATG014  Missing port_signature.json (cross-hash unavailable)
ATG015  Configuration schema error (required field missing or wrong type)
ATG016  RTL resource root unset (src: ${TOPGEN_RTL_RESOURCE_ROOT} used)
ATG017  Testbench generation warnings (auto-generated TB used as-is)
ATG018  HLS metrics JSON missing (release maturity data unavailable)
ATG019  Output format differs from topology A canonical (BD / VHDL only)
ATG020  General internal / unexpected error
ATG021  Unknown/unsupported cdc.kind value
ATG022  Missing or invalid kind-specific cdc field (min_spacing_cycles, depth)
ATG023  Undeclared clock-domain crossing
ATG024  Undeclared reset-domain crossing
ATG025  Invalid reset_domains.*.sync value
ATG026  async_fifo depth not a power of two
ATG030  Project configuration missing or unreadable (no/broken forge.yml)
ATG031  No source files match the project's source globs
ATG032  Duplicate module definition (one module name, two files)
ATG033  Discovered module source no longer exists
ATG034  No interface contract for a managed module
ATG035  Interface contract still a draft (semantics unreviewed)
ATG036  Contract/source drift (contract contradicts the module's own ports)
ATG037  Ambiguous interface consumer (more than one valid consumer)
ATG038  No connection resolved between any two managed modules
ATG039  No clock port found, or the declared clock does not exist
ATG040  Several clock candidates — the functional clock needs confirming
ATG041  Active-low reset, whose polarity generation does not model
ATG042  Module outside FORGE's supported envelope (e.g. multi-clock)
ATG043  HLS interfaces predicted but not reconciled against synthesis
ATG044  Verification not configured, or its dataset is missing
ATG045  Managed modules declare no latency
ATG046  Generated artifacts older than their inputs
ATG047  Module integrated as opaque (structure only, internals unmodelled)
ATG048  HLS predicted interface differs from the built IP

Public API
----------
Severity               enum — NOTE / WARNING / ERROR / CRITICAL
ATGDiagnostic          dataclass — one finding
ATGDiagnosticReport    collection — build and emit all findings

Usage example::

    from forge.core.diagnostics import ATGDiagnosticReport, Severity

    report = ATGDiagnosticReport()
    if not design_path.exists():
        report.error("ATG001", f"design.yml not found: {design_path}",
                     action="Create design.yml or correct the path", path=design_path)
    report.print_console()
    sys.exit(1 if not report.ok else 0)
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


# ── Severity ───────────────────────────────────────────────────────────────

class Severity(Enum):
    NOTE     = "NOTE"
    WARNING  = "WARNING"
    ERROR    = "ERROR"
    CRITICAL = "CRITICAL"

    def __ge__(self, other: "Severity") -> bool:
        _order = list(Severity)
        return _order.index(self) >= _order.index(other)

    def __gt__(self, other: "Severity") -> bool:
        _order = list(Severity)
        return _order.index(self) > _order.index(other)


# ── Single diagnostic ──────────────────────────────────────────────────────

@dataclass
class ATGDiagnostic:
    """One structured finding from forge.

    Attributes:
        code:     Stable ``ATGxxxx`` identifier.
        severity: :class:`Severity` level.
        message:  Short human-readable description of the finding.
        action:   Recommended remediation step (one sentence).
        path:     File path relevant to the finding (optional).
        module:   Module or instance name (optional).
        context:  Arbitrary extra key/value pairs for structured consumers.
    """
    code:     str
    severity: Severity
    message:  str
    action:   str
    path:     "Path | None"                        = field(default=None, repr=False)
    module:   "str | None"                         = field(default=None)
    context:  "dict[str, Any]"                     = field(default_factory=dict)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "code":     self.code,
            "severity": self.severity.value,
            "message":  self.message,
            "action":   self.action,
            "path":     str(self.path) if self.path else None,
            "module":   self.module,
            "context":  self.context,
        }

    def console_line(self) -> str:
        parts = [f"[{self.code}]", f"{self.severity.value}:", self.message]
        if self.module:
            parts.insert(2, f"({self.module})")
        return " ".join(parts)


# ── Report collection ──────────────────────────────────────────────────────

class ATGDiagnosticReport:
    """Collect, emit, and export diagnostics for one invocation of topgen.

    Severity hierarchy: CRITICAL > ERROR > WARNING > NOTE.

    The report is *passing* (``ok``) when there are no ERROR or CRITICAL items.
    In strict mode (``ok_strict``) the report is passing only when there are
    also no WARNINGs.
    """

    def __init__(self, context_label: str = "") -> None:
        self._entries: list[ATGDiagnostic] = []
        self._generated_at: float = time.time()
        self.context_label = context_label

    # ── Factory helpers ────────────────────────────────────────────────────

    def note(
        self,
        code: str,
        message: str,
        *,
        action: str = "",
        path: "Path | None" = None,
        module: "str | None" = None,
        **context: Any,
    ) -> None:
        self._add(Severity.NOTE, code, message, action=action, path=path,
                  module=module, context=context)

    def warn(
        self,
        code: str,
        message: str,
        *,
        action: str = "",
        path: "Path | None" = None,
        module: "str | None" = None,
        **context: Any,
    ) -> None:
        self._add(Severity.WARNING, code, message, action=action, path=path,
                  module=module, context=context)

    def error(
        self,
        code: str,
        message: str,
        *,
        action: str = "",
        path: "Path | None" = None,
        module: "str | None" = None,
        **context: Any,
    ) -> None:
        self._add(Severity.ERROR, code, message, action=action, path=path,
                  module=module, context=context)

    def critical(
        self,
        code: str,
        message: str,
        *,
        action: str = "",
        path: "Path | None" = None,
        module: "str | None" = None,
        **context: Any,
    ) -> None:
        self._add(Severity.CRITICAL, code, message, action=action, path=path,
                  module=module, context=context)

    def _add(
        self,
        severity: Severity,
        code: str,
        message: str,
        *,
        action: str,
        path: "Path | None",
        module: "str | None",
        context: dict,
    ) -> None:
        self._entries.append(
            ATGDiagnostic(
                code=code, severity=severity, message=message,
                action=action, path=path, module=module, context=context,
            )
        )

    # ── Aggregates ─────────────────────────────────────────────────────────

    @property
    def ok(self) -> bool:
        """True when no ERROR or CRITICAL diagnostics are present."""
        return not any(d.severity >= Severity.ERROR for d in self._entries)

    @property
    def ok_strict(self) -> bool:
        """True when no WARNING, ERROR, or CRITICAL diagnostics are present."""
        return not any(d.severity >= Severity.WARNING for d in self._entries)

    @property
    def errors(self) -> list[ATGDiagnostic]:
        return [d for d in self._entries if d.severity >= Severity.ERROR]

    @property
    def warnings(self) -> list[ATGDiagnostic]:
        return [d for d in self._entries if d.severity == Severity.WARNING]

    @property
    def notes(self) -> list[ATGDiagnostic]:
        return [d for d in self._entries if d.severity == Severity.NOTE]

    @property
    def all(self) -> list[ATGDiagnostic]:
        return list(self._entries)

    def summary_line(self) -> str:
        n_err  = len(self.errors)
        n_warn = len(self.warnings)
        n_note = len(self.notes)
        parts = []
        if n_err:
            parts.append(f"{n_err} error(s)")
        if n_warn:
            parts.append(f"{n_warn} warning(s)")
        if n_note:
            parts.append(f"{n_note} note(s)")
        if not parts:
            return "✅  All checks passed — no issues found"
        label = "❌" if n_err else "⚠️"
        return f"{label}  {', '.join(parts)}"

    # ── Output ─────────────────────────────────────────────────────────────

    def print_console(self, *, file=None) -> None:
        """Print all diagnostics to *file* (default: ``sys.stderr``)."""
        out = file or sys.stderr
        if self.context_label:
            print(f"  [{self.context_label}]", file=out)
        for d in self._entries:
            print(f"  {d.console_line()}", file=out)
            if d.action:
                print(f"    → {d.action}", file=out)
            if d.path:
                print(f"    at {d.path}", file=out)

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_label": self.context_label,
            "generated_at":  self._generated_at,
            "summary":        self.summary_line(),
            "counts": {
                "error":    len(self.errors),
                "warning":  len(self.warnings),
                "note":     len(self.notes),
            },
            "diagnostics": [d.to_dict() for d in self._entries],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)
