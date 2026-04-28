#!/usr/bin/env python3
"""Framework-owned stimulus helper library for gen_stimulus.py authors.

Responsibility boundary
-----------------------
This module provides building-block functions so that a plugin's
``gen_stimulus.py`` author can express **semantics** rather than
low-level SystemVerilog string construction.

The plugin still owns:
  * stimulus semantics (what data to drive, what to check)
  * event/dataset iteration
  * output file path (conventionally ``stimulus_current.svh`` in the flow dir)

The framework provides:
  * Scalar and vector drive helpers
  * Output check helper with FAIL-compatible message format
  * FAIL diagnostic line helper
  * Valid/ready style toggle helper
  * Clock-edge and multi-cycle wait helpers
  * Grouped event blocks (comment fencing for readability)
  * Complete ``run_stimulus()`` task assembler
  * File writing utility

Public API
----------
  StimulusEmitter           — builder for run_stimulus() task body
  emit_clocked_drive        — one cycle of signal assignment
  emit_output_check         — one combinational/registered check
  emit_fail_display         — FAIL display line for checker scanning
  write_run_stimulus_svh    — write the complete SVH file to disk

StimulusEmitter methods
-----------------------
  .comment(text)                    — add a single-line comment
  .blank()                          — add a blank separator line
  .raw(sv_line)                     — add a raw SV statement (verbatim)
  .drive(signal, value, width)      — non-blocking assignment: signal <= value
  .tick(cycles=1)                   — @(posedge ap_clk); #1;  (n times)
  .wait(cycles=1)                   — alias for .tick()
  .check(signal, expected, width, label) — if-check + FAIL display + $fatal
  .drive_valid(data_signal, data_value, data_width, valid_signal)
                                    — helper: set data + assert valid in one call
  .deassert_valid(valid_signal)     — drive valid to 0 after a data cycle
  .event_block(label)               — context manager: wraps lines in a labeled block

Example (in gen_stimulus.py)
-----------------------------
::

    from arc.verify.stimulus_helpers import StimulusEmitter, write_run_stimulus_svh

    em = StimulusEmitter()
    em.comment("Event 1: single muon track")
    em.drive("data_in", 0xDEAD, width=16)
    em.drive("data_valid", 1, width=1)
    em.tick()                       # advance one clock
    em.check("result", 42, width=8, label="result_event1")
    em.drive("data_valid", 0, width=1)

    write_run_stimulus_svh(em, Path("my_flow/stimulus_current.svh"))
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator


# ── Line-level helpers (functional, stateless) ─────────────────────────────

def emit_clocked_drive(
    signal: str,
    value: int | str,
    width: int = 1,
    *,
    indent: str = "    ",
) -> str:
    """Return a SV assignment line for driving *signal* to *value*.

    The assignment is non-blocking (``<=``) so it is safe inside a clocked
    ``always`` block or after ``@(posedge ap_clk)``.

    Args:
        signal: Signal name (must be in scope in ``run_stimulus``).
        value:  Integer value or SV literal string.
        width:  Bit width.  Used when *value* is an integer to produce a
                correctly-sized literal (e.g. ``16'h0042``).
        indent: Leading indentation.

    Returns:
        A single SV statement string (no trailing newline).
    """
    if isinstance(value, int):
        if width == 1:
            sv_val = f"1'b{value & 1}"
        else:
            hex_digits = (width + 3) // 4
            sv_val = f"{width}'h{value:0{hex_digits}X}"
    else:
        sv_val = str(value)
    return f"{indent}{signal} <= {sv_val};"


def emit_output_check(
    signal: str,
    expected: int | str,
    width: int = 1,
    label: str = "",
    *,
    indent: str = "    ",
) -> list[str]:
    """Return SV lines that check *signal* against *expected*.

    Emits an ``if`` block that calls ``$fatal`` (and prints a FAIL line
    compatible with the framework log-scanner) on mismatch.

    Args:
        signal:   Output signal name in scope.
        expected: Expected integer value or SV literal string.
        width:    Bit width (used to format the literal).
        label:    Human-readable checkpoint label for the FAIL message.
        indent:   Leading indentation.

    Returns:
        List of SV statement strings (one per line, no trailing newlines).
    """
    if isinstance(expected, int):
        if width == 1:
            sv_exp = f"1'b{expected & 1}"
        else:
            hex_digits = (width + 3) // 4
            sv_exp = f"{width}'h{expected:0{hex_digits}X}"
    else:
        sv_exp = str(expected)

    tag = label or signal
    return [
        f"{indent}if ({signal} !== {sv_exp}) begin",
        emit_fail_display(tag, signal, sv_exp, indent=indent + "  "),
        f'{indent}  $fatal(1, "Check failed: {tag}");',
        f"{indent}end",
    ]


def emit_fail_display(
    label: str,
    actual_signal: str,
    expected_sv: str,
    *,
    indent: str = "    ",
) -> str:
    """Return a SV ``$display`` line that produces a FAIL line.

    The format ``FAIL: <label> ...`` is recognised by the framework
    log-scanner in ``XsimBackend.run_checker()``.

    Args:
        label:          Checkpoint label.
        actual_signal:  Signal whose value to display.
        expected_sv:    SV literal of the expected value.
        indent:         Leading indentation.

    Returns:
        A single SV statement string.
    """
    return (
        f'{indent}$display("FAIL: {label} — expected {expected_sv}, '
        f'got %0h", {actual_signal});'
    )


# ── Builder class ──────────────────────────────────────────────────────────

@dataclass
class StimulusEmitter:
    """Accumulates SV statements for the body of a ``run_stimulus`` task.

    Usage::

        em = StimulusEmitter()
        em.comment("Drive inputs")
        em.drive("ap_in", 0xFF, width=8)
        em.tick()
        em.check("ap_out", 42, width=8, label="result_chk")
        svh_text = em.render()
    """
    _lines: list[str] = field(default_factory=list, init=False)
    indent: str = "    "

    def comment(self, text: str) -> "StimulusEmitter":
        """Append a single-line comment."""
        self._lines.append(f"{self.indent}// {text}")
        return self

    def blank(self) -> "StimulusEmitter":
        """Append a blank line."""
        self._lines.append("")
        return self

    def raw(self, sv_line: str) -> "StimulusEmitter":
        """Append a raw SV statement (no transformation)."""
        self._lines.append(sv_line)
        return self

    def drive(
        self,
        signal: str,
        value: int | str,
        width: int = 1,
    ) -> "StimulusEmitter":
        """Append a non-blocking assignment for *signal*."""
        self._lines.append(emit_clocked_drive(signal, value, width, indent=self.indent))
        return self

    def tick(self, cycles: int = 1) -> "StimulusEmitter":
        """Append one or more positive-edge clock waits."""
        for _ in range(cycles):
            self._lines.append(f"{self.indent}@(posedge ap_clk);")
        return self

    def wait(self, cycles: int = 1) -> "StimulusEmitter":
        """Alias for :meth:`tick` — advance *cycles* positive clock edges.

        Prefer ``tick()`` for clarity when ``tick`` reads naturally; use
        ``wait()`` when expressing "wait for N cycles" is more idiomatic.
        """
        return self.tick(cycles)

    def check(
        self,
        signal: str,
        expected: int | str,
        width: int = 1,
        label: str = "",
    ) -> "StimulusEmitter":
        """Append output-check lines for *signal* against *expected*.

        Generates an ``if`` block that emits ``FAIL: <label>`` (recognised
        by the framework log-scanner) and calls ``$fatal(1, ...)`` on mismatch.
        """
        self._lines.extend(
            emit_output_check(signal, expected, width, label, indent=self.indent)
        )
        return self

    def drive_valid(
        self,
        data_signal: str,
        data_value: int | str,
        data_width: int,
        valid_signal: str = "data_valid",
    ) -> "StimulusEmitter":
        """Drive *data_signal* and assert *valid_signal* in a single helper call.

        Equivalent to::

            em.drive(data_signal, data_value, data_width)
            em.drive(valid_signal, 1, 1)

        Args:
            data_signal:  DUT data input signal name.
            data_value:   Value to drive onto *data_signal*.
            data_width:   Bit width of *data_signal*.
            valid_signal: Valid strobe signal name (default: ``"data_valid"``).
        """
        self.drive(data_signal, data_value, data_width)
        self.drive(valid_signal, 1, 1)
        return self

    def deassert_valid(self, valid_signal: str = "data_valid") -> "StimulusEmitter":
        """Drive *valid_signal* to 0 (deassert after data is consumed).

        Args:
            valid_signal: Valid strobe signal name (default: ``"data_valid"``).
        """
        self.drive(valid_signal, 0, 1)
        return self

    @contextlib.contextmanager
    def event_block(self, label: str) -> Generator[None, None, None]:
        """Context manager that wraps the enclosed statements in a comment block.

        Usage::

            with em.event_block("Event 3: boundary case"):
                em.drive("in_data", 0xFFFF, width=16)
                em.tick()
                em.check("result", 0x0001, width=16, label="boundary")

        This produces::

            // ── Event 3: boundary case ──
            in_data <= 16'hFFFF;
            @(posedge ap_clk);
            if (result !== 16'h0001) begin ...
            // ── end: Event 3: boundary case ──

        Args:
            label: Human-readable label for the event block.
        """
        self.blank()
        self.comment(f"── {label} ──")
        yield
        self.comment(f"── end: {label} ──")
        self.blank()

    def render(self) -> str:
        """Return the accumulated statements as a single string."""
        return "\n".join(self._lines)

    def render_lines(self) -> list[str]:
        """Return the accumulated statements as a list of strings."""
        return list(self._lines)


# ── File writer ────────────────────────────────────────────────────────────

_SVH_HEADER = """\
// stimulus_current.svh — generated by plugin gen_stimulus.py
// DO NOT EDIT — regenerate by running gen_stimulus.py
//
// Framework contract:
//   * This file is `include'd into the testbench module body.
//   * Must declare exactly one task: ``task automatic run_stimulus();``
//   * The task is called once after reset + idle cycles.
//   * Signals ap_clk, ap_rst (and all DUT ports) are in scope.
"""

_SVH_TASK_OPEN  = "task automatic run_stimulus();"
_SVH_TASK_CLOSE = "endtask : run_stimulus"


def write_run_stimulus_svh(
    emitter: "StimulusEmitter | list[str] | str",
    out_path: Path,
    *,
    header_comment: str = "",
) -> None:
    """Write a complete ``stimulus_current.svh`` file.

    Wraps the emitter's lines inside ``task automatic run_stimulus(); ... endtask``.

    Args:
        emitter:        ``StimulusEmitter`` instance, a list of SV statement strings,
                        or a raw pre-rendered string.
        out_path:       Destination path (parent dir is created as needed).
        header_comment: Optional extra comment inserted after the standard header.
    """
    if isinstance(emitter, StimulusEmitter):
        body = emitter.render()
    elif isinstance(emitter, list):
        body = "\n".join(emitter)
    else:
        body = str(emitter)

    parts: list[str] = [_SVH_HEADER]
    if header_comment:
        parts.append(f"// {header_comment}")
        parts.append("")
    parts.append(_SVH_TASK_OPEN)
    parts.append(body)
    parts.append(_SVH_TASK_CLOSE)
    parts.append("")  # trailing newline

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("\n".join(parts))
