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

    from forge.verification.stimulus_helpers import StimulusEmitter, write_run_stimulus_svh

    em = StimulusEmitter()
    em.comment("Event 1: single input record")
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


def emit_check_record(
    check_id: str,
    label: str,
    signal: str,
    expected_hex: str,
    width: int,
    sv_exp: str,
    *,
    indent: str = "    ",
) -> str:
    """Return the unconditional ``FORGE_CHECK|...`` machine-readable log line.

    Printed once per check, on **both** outcomes (pass and fail) — the
    corrected design. Parsing only a fail-only ``FAIL: ...`` line
    cannot produce an ``observed`` value for a passing check, since no such
    line is ever printed when nothing fails; this line always carries both
    ``expected`` and ``observed``, regardless of outcome.

    ``observed`` uses SV's ``%h`` (zero-padded to the signal's own
    declared width, lowercase) so a passing check's ``observed`` string is
    byte-identical to ``expected_hex`` — never just "the same numeric
    value formatted differently" (which the corrected design's own pass
    test relies on: ``expected == observed`` for a passing check).

    ``sv_exp`` is the already-computed SV literal (e.g. ``"8'h3A"``) used
    for the live ``passed`` comparison — reused, not re-derived from
    *expected_hex*.
    """
    return (
        f'{indent}$display("FORGE_CHECK|check_id={check_id}|label={label}|'
        f'signal={signal}|expected={expected_hex}|observed=0x%h|width={width}|'
        f'passed=%0d", {signal}, ({signal} === {sv_exp}));'
    )


def emit_output_check(
    signal: str,
    expected: int | str,
    width: int = 1,
    label: str = "",
    *,
    indent: str = "    ",
    event_id: "int | str" = "",
) -> list[str]:
    """Return SV lines that check *signal* against *expected*.

    Emits an unconditional ``FORGE_CHECK|...`` machine-readable record,
    then an ``if`` block that calls ``$fatal`` (and prints a
    human-readable FAIL line compatible with the framework log-scanner) on
    mismatch. The two are separate, deliberately: the human ``FAIL:`` line
    is unchanged for people reading logs directly; ``FORGE_CHECK|`` is the
    new machine-readable channel, printed on every outcome, not just fail.

    Args:
        signal:    Output signal name in scope.
        expected:  Expected integer value or SV literal string.
        width:     Bit width (used to format the literal).
        label:     Human-readable checkpoint label for the FAIL message.
        indent:    Leading indentation.
        event_id:  Event identifier this check belongs to, used to build a
                   deterministic ``check_id`` (``f"{event_id}:{label}"``).
                   Omit (default ``""``) for checks with no event context —
                   ``check_id`` then falls back to the label alone.

    Returns:
        List of SV statement strings (one per line, no trailing newlines).
    """
    tag = label or signal
    check_id = f"{event_id}:{tag}" if event_id != "" else tag

    if isinstance(expected, int):
        if width == 1:
            sv_exp = f"1'b{expected & 1}"
            expected_hex = f"0x{expected & 1:01x}"
        else:
            hex_digits = (width + 3) // 4
            sv_exp = f"{width}'h{expected:0{hex_digits}X}"
            expected_hex = f"0x{expected:0{hex_digits}x}"
    else:
        # Raw SV literal supplied by the caller — no framework-side integer
        # value to reformat, so the FORGE_CHECK record's `expected` field
        # carries the literal as-is (an honest gap: not currently exercised
        # by any real caller, which all pass integers).
        sv_exp = str(expected)
        expected_hex = str(expected)

    return [
        emit_check_record(check_id, tag, signal, expected_hex, width, sv_exp, indent=indent),
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


def emit_event_index_read(
    var_name: str = "event_index",
    *,
    indent: str = "    ",
) -> str:
    """Return a SV line that reads the ``+EVENT_INDEX=N`` runtime plusarg
    into *var_name*, defaulting to ``0`` when absent.

    The one piece of framework-generic boilerplate every ``stimulus_mode:
    readmemh`` plugin needs — reused verbatim rather than each
    plugin hand-rolling its own ``$value$plusargs`` call. ``EVENT_INDEX``
    (never ``EVENT_ID``) is deliberate: it addresses a fixed-shape memory
    array by internal, always-contiguous position — an arbitrary string
    ``event_id`` cannot be assumed numeric or contiguous at all.
    """
    return (
        f'{indent}if (!$value$plusargs("EVENT_INDEX=%d", {var_name})) '
        f"{var_name} = 0;"
    )


def emit_runtime_output_check(
    signal: str,
    expected_signal: str,
    width: int = 1,
    label: str = "",
    *,
    indent: str = "    ",
) -> list[str]:
    """Like :func:`emit_output_check`, but *expected_signal* is itself a
    runtime SV expression (typically a variable read out of a
    ``$readmemh`` memory word) rather than a compile-time-known Python
    value.

    Needed by the ``stimulus_mode: readmemh`` mechanism: one
    compiled testbench services many events, so the expected value varies
    per ``+EVENT_INDEX`` at *simulation* runtime, not at *generation*
    time — ``emit_output_check``'s Python-side hex formatting has nothing
    to format ahead of time here. The ``FORGE_CHECK|`` record's
    ``expected``/``observed`` fields are both filled from live signal
    values via ``%h``, not pre-computed text.

    ``check_id`` here is just *label* — the real per-event id is not known
    at generation time (a single compiled TB runs many events), but each
    simulation invocation writes its own ``simulate.log``, so the
    real event_id attribution happens externally, one log per event, at
    the same per-event granularity ``emit_output_check``'s checks get.
    """
    tag = label or signal
    check_record = (
        f'{indent}$display("FORGE_CHECK|check_id={tag}|label={tag}|'
        f'signal={signal}|expected=0x%h|observed=0x%h|width={width}|'
        f'passed=%0d", {expected_signal}, {signal}, ({signal} === {expected_signal}));'
    )
    return [
        check_record,
        f"{indent}if ({signal} !== {expected_signal}) begin",
        emit_fail_display(tag, signal, expected_signal, indent=indent + "  "),
        f'{indent}  $fatal(1, "Check failed: {tag}");',
        f"{indent}end",
    ]


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

    def tick(self, cycles: int = 1, clock: str = "ap_clk") -> "StimulusEmitter":
        """Append one or more positive-edge clock waits.

        ``clock``: defaults to
        ``ap_clk`` — every pre-existing single-clock-domain stimulus
        script is unaffected. Pass a different top-level clock net name
        (matching a ``simulation.extra_clocks`` entry the flow declares —
        see forge.verification.gen_sim.render_tb_sv) to wait on a *different*
        domain's own clock, e.g. when driving/checking a signal that
        lives in that domain rather than the primary one.
        """
        for _ in range(cycles):
            self._lines.append(f"{self.indent}@(posedge {clock});")
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
        event_id: "int | str" = "",
    ) -> "StimulusEmitter":
        """Append output-check lines for *signal* against *expected*.

        Generates an unconditional ``FORGE_CHECK|...`` machine-readable
        record plus an ``if`` block that emits ``FAIL: <label>``
        (recognised by the framework log-scanner) and calls ``$fatal(1,
        ...)`` on mismatch. Pass *event_id* so the record's ``check_id`` is
        ``f"{event_id}:{label}"`` — deterministic and attributable to the
        right event when this check is later parsed back per-event.
        """
        self._lines.extend(
            emit_output_check(signal, expected, width, label, indent=self.indent, event_id=event_id)
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


def write_readmemh_stimulus_svh(
    *,
    preamble_lines: "list[str]",
    task_body_lines: "list[str]",
    out_path: Path,
    header_comment: str = "",
) -> None:
    """Write a complete ``stimulus_current.svh`` for ``stimulus_mode:
    readmemh``.

    Unlike :func:`write_run_stimulus_svh` (which wraps its *entire* body
    inside the task), this mechanism genuinely needs module-scope
    declarations — the ``$readmemh``-loaded memory array and the
    ``initial`` block that loads it — *before* the task, not inside it
    (SV does not allow an ``initial`` block nested inside a task).

    ``validate_stimulus``'s contract only requires exactly one task
    signature/``endtask`` pair to appear somewhere in the file, never that
    the file contain *nothing else* — confirmed against every one of its
    9 regex checks — so a module-scope preamble ahead of the task still
    satisfies the contract unchanged.
    """
    parts: list[str] = [_SVH_HEADER]
    if header_comment:
        parts.append(f"// {header_comment}")
        parts.append("")
    parts.extend(preamble_lines)
    parts.append("")
    parts.append(_SVH_TASK_OPEN)
    parts.append("\n".join(task_body_lines))
    parts.append(_SVH_TASK_CLOSE)
    parts.append("")  # trailing newline

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("\n".join(parts))
