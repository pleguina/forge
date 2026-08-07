#!/usr/bin/env python3
"""Unified typed exception hierarchy for forge.verification.

Every user-facing exception inherits from :class:`ForgeVerifyError`.
All concrete subclasses carry:

  * **message** — human-readable description (from the normal ``args[0]``)
  * **action**  — mandatory suggested next step for the user
  * **context** — optional ``dict`` of key-value debugging context

Hierarchy
---------
::

    ForgeVerifyError                          # base — always carries .action
    ├── DesignContractError                # A-series: contract parse / schema
    ├── FlowConfigError                    # A-series: verify.flow.yml parse / schema
    ├── SupportedMatrixViolation           # A-series: unsupported (kind, backend)
    ├── LayoutViolation                    # C-series: non-canonical layout
    ├── MissingArtifactError               # C-series: generated file absent
    ├── RTLIntrospectionError              # C-series: port extraction
    │   ├── RTLFileNotFoundError
    │   ├── ParserDependencyMissing
    │   ├── ParserModeFailed
    │   └── RegexFallbackUnsupported
    ├── StimulusContractError              # C-series: stimulus file
    ├── BackendValidationError             # C-series: pre-execution validation
    ├── ToolNotFoundError                  # D-series: simulator not on PATH
    ├── SimulatorExecutionError            # D-series: subprocess non-zero exit
    └── PluginBootstrapError              # bootstrap lifecycle

Design notes
------------
* Every subclass must supply a non-empty ``action`` string — it is enforced
  by :class:`ForgeVerifyError.__init_subclass__`.
* The ``context`` dict is for *machine-readable* key-value debugging details.
  Use it for paths, flow names, exit codes, and similar diagnostic data.
* These exceptions are NOT used inside unit tests themselves — they wrap
  user-facing errors at subsystem boundaries.
* The :mod:`rtl_introspection` module uses its own ``IntrospectionError``
  hierarchy for historical reasons; the parallel types here re-export those
  concepts for callers that catch at the ``ForgeVerifyError`` level.

Usage
-----
::

    from forge.verification.exceptions import MissingArtifactError

    if not tb_sv.exists():
        raise MissingArtifactError(
            f"Testbench not found: {tb_sv}",
            action=f"Run: forge verify generate {design_yml} --flow {flow_name}",
            context={"path": str(tb_sv), "flow": flow_name},
        )

    # Catching at any granularity:
    try:
        ...
    except ForgeVerifyError as exc:
        print(f"[{exc.__class__.__name__}] {exc}")
        print(f"  → {exc.action}")
"""
from __future__ import annotations

from typing import Any


# ── Base exception ─────────────────────────────────────────────────────────

class ForgeVerifyError(Exception):
    """Base class for all framework-raised, user-facing exceptions.

    Args:
        message: Human-readable description.
        action:  Mandatory suggested next step.  Must be non-empty.
        context: Optional machine-readable key-value pairs for debug output.
    """

    def __init__(
        self,
        message: str,
        *,
        action: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        if not action:
            raise ValueError(
                f"{type(self).__name__}: 'action' must be a non-empty string"
            )
        self.action:  str = action
        self.context: dict[str, Any] = context or {}

    def format_for_cli(self, *, debug: bool = False) -> str:
        """Return a formatted string suitable for CLI output.

        In normal mode: concise ``category: message`` + action hint.
        In debug mode: adds the exception chain and context dict.
        """
        category = type(self).__name__
        lines = [
            f"[{category}] {self}",
            f"  → {self.action}",
        ]
        if debug:
            if self.context:
                lines.append("  Context:")
                for k, v in self.context.items():
                    lines.append(f"    {k}: {v}")
            if self.__cause__ is not None:
                lines.append(f"  Caused by: {type(self.__cause__).__name__}: {self.__cause__}")
        return "\n".join(lines)


# ── A-series: Contract and matrix ─────────────────────────────────────────

class DesignContractError(ForgeVerifyError):
    """Raised when ``design.verification.yml`` fails to parse or validate.

    Corresponds to diagnostic code FWV001.
    """


class FlowConfigError(ForgeVerifyError):
    """Raised when ``verify.flow.yml`` fails to parse or validate.

    Corresponds to diagnostic code FWV001.
    """


class SupportedMatrixViolation(ForgeVerifyError):
    """Raised when a (kind, backend) combination is unsupported or experimental.

    Corresponds to diagnostic code FWV002.
    """


# ── C-series: Layout ──────────────────────────────────────────────────────

class LayoutViolation(ForgeVerifyError):
    """Raised when the plugin's verify directory violates canonical layout rules.

    Corresponds to diagnostic code FWV003.
    """


# ── C-series: Missing artifacts ───────────────────────────────────────────

class MissingArtifactError(ForgeVerifyError):
    """Raised when a framework-generated artifact is absent before simulation.

    Corresponds to diagnostic code FWV004.

    Typical cases:
      * ``verify.flow.yml`` not yet generated.
      * ``tb_<module>.sv`` not yet generated.
      * ``port_map.yaml`` not derived.
      * ``stimulus_current.svh`` not yet generated by plugin.
    """


# ── C-series: RTL introspection ───────────────────────────────────────────

class RTLIntrospectionError(ForgeVerifyError):
    """Base class for all port-extraction failures.

    Corresponds to diagnostic codes FWV005–FWV009.
    """


class RTLFileNotFoundError(RTLIntrospectionError):
    """The specified RTL Verilog file does not exist.

    Corresponds to diagnostic code FWV005.
    """


class ParserDependencyMissing(RTLIntrospectionError):
    """``pyverilog`` (parser extra) is not installed.

    Corresponds to diagnostic code FWV006.
    """


class ParserModeFailed(RTLIntrospectionError):
    """pyverilog is installed but failed to parse the given RTL.

    Corresponds to diagnostic code FWV007.
    """


class RegexFallbackUnsupported(RTLIntrospectionError):
    """Regex fallback ran but extracted zero ports — result is unusable.

    Corresponds to diagnostic code FWV008.
    """


# ── C-series: Stimulus ────────────────────────────────────────────────────

class StimulusContractError(ForgeVerifyError):
    """Raised when ``stimulus_current.svh`` is absent or fails contract checks.

    Corresponds to diagnostic code FWV010.
    """


# ── C-series: Backend validation ─────────────────────────────────────────

class BackendValidationError(ForgeVerifyError):
    """Raised by a backend adapter when pre-execution requirements are not met.

    Corresponds to diagnostic code FWV011.

    Examples: TB missing, DUT RTL missing, wave.tcl missing.
    """


# ── D-series: Tool availability ───────────────────────────────────────────

class ToolNotFoundError(ForgeVerifyError):
    """Raised when a required simulator tool is not on PATH.

    Corresponds to diagnostic code FWV012.
    """


class SimulatorExecutionError(ForgeVerifyError):
    """Raised when a simulator subprocess exits with a non-zero code.

    Corresponds to diagnostic code FWV013.

    The ``context`` dict carries:
      ``cmd``      — full command list
      ``exit_code`` — integer
      ``log_path`` — path to the captured log file (if any)
      ``cwd``      — working directory of the subprocess
    """


# ── Plugin bootstrap ──────────────────────────────────────────────────────

class PluginBootstrapError(ForgeVerifyError):
    """Raised when the plugin bootstrap lifecycle fails.

    Corresponds to diagnostic code FWV014.

    Common causes:
      * ``tools/bootstrap.py`` not found.
      * ``bootstrap()`` function is missing or raises an exception.
      * ``declare_plugin_bootstrap()`` was never called.
    """


# ── E-series: Dataset ───────────────────────────────────────────────────────

class DatasetContentHashError(ForgeVerifyError):
    """Raised when a dataset file's declared ``source_content_hash`` does
    not match the hash actually computed over its event content.

    The hash is always recomputed on load and never trusted from the input
    file — this is the real, reported error for a tampered or hand-edited
    dataset file, never a silent accept of whatever hash string happened
    to be present.
    """
