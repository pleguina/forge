#!/usr/bin/env python3
"""Structured diagnostics for fw_verify commands.

Provides :class:`Diagnostic` (a single classified finding) and
:class:`DiagnosticReport` (a collection of findings) as the uniform output
format for all framework health checks.

All framework validation functions (``doctor``, ``supported_path_validator``,
``prepare``, layout validation, stimulus validation) should emit
``Diagnostic`` objects instead of bare ``list[str]``.

Stable diagnostic codes
-----------------------
Codes allow another team to file a bug report or CI alert as "FWVxxxx" without
ambiguity.

  ======= ================================================================
  Code    Meaning
  ======= ================================================================
  FWV001  Unsupported (kind, backend) combination
  FWV002  Experimental flow without explicit opt-in
  FWV003  Invalid or non-canonical layout (kind-subdir, missing dir)
  FWV004  Missing required framework-generated artifact (TB, flow.yml, …)
  FWV005  RTL Verilog file not found
  FWV006  Parser dependency (pyverilog) not installed
  FWV007  Parser mode failed on given RTL
  FWV008  Regex fallback extracted zero ports — dangerous failure mode
  FWV009  Zero ports in existing port_map.yaml
  FWV010  Stimulus contract failure (missing task, $finish, …)
  FWV011  Backend pre-execution validation failure
  FWV012  Required simulator tool not on PATH
  FWV013  Simulator subprocess non-zero exit
  FWV014  Plugin bootstrap not importable or missing bootstrap()
  FWV015  Design contract parse or schema error
  FWV016  Stale generated artifact (older than source contract/RTL)
  FWV017  Missing stimulus file (warning — user generates this later)
  FWV018  Missing csim testbench binary
  FWV019  Bootstrap.py not found
  FWV020  gen_stimulus.py not found (warning for xsim flows)
  ======= ================================================================

Usage
-----
::

    from forge.verify.diagnostics import Diagnostic, DiagnosticReport, Severity

    report = DiagnosticReport()
    report.add(Diagnostic(
        code="FWV004",
        severity=Severity.ERROR,
        message=f"TB not found: {tb_sv}",
        action=f"Run: fw_verify generate {design_yml} --flow {flow_name}",
        path=str(tb_sv),
        flow=flow_name,
    ))
    report.add(Diagnostic(
        code="FWV006",
        severity=Severity.WARNING,
        message="pyverilog not installed — using regex fallback",
        action="pip install -e 'framework/verify/python/[parser]'",
    ))

    # Print to console
    report.print_console()

    # Export to JSON (for CI consumption)
    import json
    print(json.dumps(report.to_dict(), indent=2))

    # Check categories
    if not report.ok:
        sys.exit(1)

``ok`` is ``True`` when there are no ERROR or CRITICAL diagnostics.
``ok_strict`` is ``True`` when there are no ERRORs or WARNINGs.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, TextIO


# ── Severity ───────────────────────────────────────────────────────────────

class Severity(str, Enum):
    """Ordered severity levels for diagnostics.

    Values as strings so they serialise cleanly to JSON.
    """
    NOTE     = "note"      # informational — everything is fine
    WARNING  = "warning"   # non-blocking but should be addressed
    ERROR    = "error"     # blocks simulation / handoff
    CRITICAL = "critical"  # framework internal error (should not reach user)

    @property
    def sort_key(self) -> int:
        return {"note": 0, "warning": 1, "error": 2, "critical": 3}[self.value]

    def __lt__(self, other: "Severity") -> bool:  # for sorting
        return self.sort_key < other.sort_key


# ── Diagnostic ─────────────────────────────────────────────────────────────

@dataclass
class Diagnostic:
    """A single classified finding from a framework health check.

    Args:
        code:     Stable ``FWVxxxx`` code from the table above.
        severity: Severity level (NOTE / WARNING / ERROR / CRITICAL).
        message:  Human-readable description of the finding.
        action:   Suggested next step.  Empty for NOTEs.
        path:     Optional filesystem path relevant to the finding.
        flow:     Optional flow name relevant to the finding.
        context:  Optional additional key-value debug context.
    """

    code:     str
    severity: Severity
    message:  str
    action:   str = ""
    path:     str = ""
    flow:     str = ""
    context:  dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict."""
        d: dict[str, Any] = {
            "code":     self.code,
            "severity": self.severity.value,
            "message":  self.message,
        }
        if self.action:
            d["action"] = self.action
        if self.path:
            d["path"] = self.path
        if self.flow:
            d["flow"] = self.flow
        if self.context:
            d["context"] = self.context
        return d

    def format_console(self) -> str:
        """Return a human-readable console line for this diagnostic."""
        tag = {
            Severity.NOTE:     " OK  ",
            Severity.WARNING:  "WARN ",
            Severity.ERROR:    "ERR  ",
            Severity.CRITICAL: "CRIT ",
        }[self.severity]
        prefix = f"[{self.code}]" if self.code else ""
        flow_label = f"[{self.flow}] " if self.flow else ""
        line = f"  {tag} {prefix} {flow_label}{self.message}"
        if self.action:
            line += f"\n        → {self.action}"
        return line


# ── DiagnosticReport ──────────────────────────────────────────────────────

class DiagnosticReport:
    """An ordered collection of :class:`Diagnostic` objects.

    Provides:
      * ``add()``            — append a single Diagnostic.
      * ``extend()``         — append multiple Diagnostics.
      * ``note()``/``warn()``/``error()``/``critical()`` — convenience constructors.
      * ``ok``               — True when no ERROR or CRITICAL entries exist.
      * ``ok_strict``        — True when no WARNING, ERROR, or CRITICAL entries exist.
      * ``print_console()``  — emit the full report to stdout/stderr.
      * ``to_dict()``        — JSON-serialisable summary.
      * ``to_json()``        — JSON string.
    """

    def __init__(self, label: str = "") -> None:
        self.label:       str              = label
        self._items:      list[Diagnostic] = []

    # ── Mutation ──────────────────────────────────────────────────────────

    def add(self, diag: Diagnostic) -> None:
        self._items.append(diag)

    def extend(self, diags: "list[Diagnostic]") -> None:
        self._items.extend(diags)

    def note(
        self,
        code: str,
        message: str,
        *,
        path: str = "",
        flow: str = "",
        context: "dict[str, Any] | None" = None,
    ) -> None:
        """Append an informational NOTE."""
        self._items.append(Diagnostic(
            code=code, severity=Severity.NOTE,
            message=message, path=path, flow=flow,
            context=context or {},
        ))

    def warn(
        self,
        code: str,
        message: str,
        *,
        action: str = "",
        path: str = "",
        flow: str = "",
        context: "dict[str, Any] | None" = None,
    ) -> None:
        """Append a WARNING."""
        self._items.append(Diagnostic(
            code=code, severity=Severity.WARNING,
            message=message, action=action, path=path, flow=flow,
            context=context or {},
        ))

    def error(
        self,
        code: str,
        message: str,
        *,
        action: str = "",
        path: str = "",
        flow: str = "",
        context: "dict[str, Any] | None" = None,
    ) -> None:
        """Append an ERROR."""
        self._items.append(Diagnostic(
            code=code, severity=Severity.ERROR,
            message=message, action=action, path=path, flow=flow,
            context=context or {},
        ))

    def critical(
        self,
        code: str,
        message: str,
        *,
        action: str = "",
        path: str = "",
        flow: str = "",
        context: "dict[str, Any] | None" = None,
    ) -> None:
        """Append a CRITICAL."""
        self._items.append(Diagnostic(
            code=code, severity=Severity.CRITICAL,
            message=message, action=action, path=path, flow=flow,
            context=context or {},
        ))

    # ── Computed properties ───────────────────────────────────────────────

    @property
    def items(self) -> list[Diagnostic]:
        return list(self._items)

    @property
    def notes(self) -> list[Diagnostic]:
        return [d for d in self._items if d.severity == Severity.NOTE]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self._items if d.severity == Severity.WARNING]

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self._items if d.severity in (Severity.ERROR, Severity.CRITICAL)]

    @property
    def ok(self) -> bool:
        """True when no ERROR or CRITICAL diagnostics are present."""
        return all(
            d.severity not in (Severity.ERROR, Severity.CRITICAL)
            for d in self._items
        )

    @property
    def ok_strict(self) -> bool:
        """True when no WARNING, ERROR, or CRITICAL diagnostics are present."""
        return all(d.severity == Severity.NOTE for d in self._items)

    # ── Output ────────────────────────────────────────────────────────────

    def print_console(
        self,
        *,
        out: TextIO | None = None,
        err: TextIO | None = None,
        show_notes: bool = True,
    ) -> None:
        """Print the report to console streams.

        Notes and warnings go to *out* (default: stdout).
        Errors go to *err* (default: stderr).
        """
        out = out or sys.stdout
        err = err or sys.stderr

        for d in self._items:
            line = d.format_console()
            if d.severity == Severity.ERROR or d.severity == Severity.CRITICAL:
                print(line, file=err)
            elif d.severity == Severity.WARNING:
                print(line, file=err)
            elif show_notes:
                print(line, file=out)

    def summary_line(self, *, strict: bool = False) -> str:
        """Return a one-line summary string."""
        n_err  = len(self.errors)
        n_warn = len(self.warnings)
        n_note = len(self.notes)
        status = "PASS" if (self.ok_strict if strict else self.ok) else "FAIL"
        label  = f" {self.label}" if self.label else ""
        return (
            f"[doctor{label}] {status}  "
            f"({n_note} OK, {n_warn} warnings, {n_err} errors)"
        )

    # ── Serialisation ─────────────────────────────────────────────────────

    def to_dict(self, *, strict: bool = False) -> dict[str, Any]:
        """Return a JSON-serialisable summary dict."""
        return {
            "label":          self.label,
            "status":         "pass" if (self.ok_strict if strict else self.ok) else "fail",
            "counts": {
                "notes":    len(self.notes),
                "warnings": len(self.warnings),
                "errors":   len(self.errors),
            },
            "diagnostics": [d.to_dict() for d in self._items],
        }

    def to_json(self, *, indent: int = 2, strict: bool = False) -> str:
        """Return the report as a JSON string."""
        return json.dumps(self.to_dict(strict=strict), indent=indent)
