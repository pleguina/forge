#!/usr/bin/env python3
"""Stimulus file contract validator — framework-owned.

Responsibility boundary
-----------------------
This module validates ``stimulus_current.svh`` before the simulator is
launched, moving errors from simulator compile/run logs into framework
diagnostics where they are more actionable.

What is validated
-----------------
1. File exists on disk.
2. File is non-empty.
3. File declares exactly one ``task automatic run_stimulus();`` signature.
4. ``endtask`` or ``endtask : run_stimulus`` is present (task is closed).
5. No ``$finish`` calls (would abort the TB unconditionally).
6. ``ap_clk`` is referenced at least once (clock sanity; warning).
7. No duplicate task definitions (more than one ``task`` keyword is an error).
8. At least one check/assert/fail pattern present (warning if absent).
9. At least one input-driving statement present (warning if absent).

Framework-owned constants (shared with TB template generator)
-------------------------------------------------------------
These are the ONLY legal values.  Any change here must be reflected in the
TB template in gen_sim.py — do not duplicate them anywhere else.

  STIMULUS_FILE           = "stimulus_current.svh"
  STIMULUS_TASK_NAME      = "run_stimulus"
  STIMULUS_TASK_SIGNATURE = "task automatic run_stimulus();"
  FAILURE_PREFIX          = "FAIL:"
  STIMULUS_INCLUDE_DIRECTIVE = '`include "stimulus_current.svh"'

Public API
----------
  StimulusContractResult        dataclass — errors/warnings/notes
  validate_stimulus(svh_path)   → StimulusContractResult
  validate_stimulus_for_flow    → StimulusContractResult  (uses cfg)

Usage
-----
::

    from arc.verify.stimulus_contract import validate_stimulus
    result = validate_stimulus(Path("my_flow/stimulus_current.svh"))
    if not result.ok:
        print(result.format_errors())
        sys.exit(1)
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Framework-owned immutable constants ────────────────────────────────────
# These define the TB/stimulus interface contract.  All framework code and
# all generated TB templates must reference these constants — never hardcode
# the strings directly in a new call site.

STIMULUS_FILE             = "stimulus_current.svh"
STIMULUS_TASK_NAME        = "run_stimulus"
STIMULUS_TASK_SIGNATURE   = "task automatic run_stimulus();"
STIMULUS_TASK_ENDTASK     = "endtask : run_stimulus"
FAILURE_PREFIX            = "FAIL:"
STIMULUS_INCLUDE_DIRECTIVE = '`include "stimulus_current.svh"'

# Signals the TB guarantees to declare (always in scope for the stimulus task)
TB_GUARANTEED_SIGNALS: frozenset[str] = frozenset({
    "ap_clk",
    "ap_rst",
})

# Patterns the framework log-scanner recognises as failure markers
BUILTIN_FAILURE_MARKERS: tuple[str, ...] = (
    "$fatal",
    "ASSERTION FAILED",
    "TEST FAILED",
    FAILURE_PREFIX,
)


# ── Regex patterns ─────────────────────────────────────────────────────────

_RE_TASK_SIG     = re.compile(r"\btask\s+automatic\s+run_stimulus\s*\(\s*\)\s*;")
_RE_ENDTASK      = re.compile(r"\bendtask\b")
_RE_FINISH       = re.compile(r"\$finish\b")
_RE_CLK_REF      = re.compile(r"\bap_clk\b")
_RE_FAIL_PAT     = re.compile(r"FAIL:|assert\b|\$fatal\b|\$error\b", re.IGNORECASE)
_RE_DRIVE_STMT   = re.compile(r"\b\w+\s*<=|#\d+\s*;|@\s*\(")


# ── Result model ───────────────────────────────────────────────────────────

@dataclass
class StimulusContractResult:
    """Result of stimulus contract validation."""
    errors:   list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes:    list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def format_errors(self) -> str:
        lines = [
            "[stimulus_contract] FAILED — stimulus_current.svh is not contract-compliant:"
        ]
        for e in self.errors:
            lines.append(f"  ERROR: {e}")
        for w in self.warnings:
            lines.append(f"  WARNING: {w}")
        return "\n".join(lines)

    def print_summary(self) -> None:
        for n in self.notes:
            print(f"[stimulus_contract] {n}")
        for w in self.warnings:
            print(f"[stimulus_contract] WARNING: {w}", file=sys.stderr)
        if self.ok:
            print("[stimulus_contract] Stimulus contract checks passed.")
        else:
            print(self.format_errors(), file=sys.stderr)


# ── Validation entry points ────────────────────────────────────────────────

def validate_stimulus(svh_path: Path) -> StimulusContractResult:
    """Validate *svh_path* against the stimulus file contract.

    Checks performed (all 9):
      1. File exists.
      2. File is non-empty.
      3. Exactly one ``task automatic run_stimulus();`` signature.
      4. ``endtask`` is present (task body closed).
      5. No ``$finish`` call (aborts TB unconditionally).
      6. ``ap_clk`` is referenced (warning if absent).
      7. No duplicate task definitions (more than one ``task`` keyword).
      8. At least one check/assert/fail pattern (warning if absent).
      9. At least one input-driving statement (warning if absent).

    Args:
        svh_path: Path to ``stimulus_current.svh``.

    Returns:
        ``StimulusContractResult``.  Callers should check ``.ok`` before
        proceeding to simulation.
    """
    result = StimulusContractResult()
    svh_path = Path(svh_path)

    # 1. Existence
    if not svh_path.exists():
        result.errors.append(
            f"stimulus_current.svh not found: {svh_path}\n"
            f"  → Run the plugin stimulus generator for this flow"
        )
        return result  # No point continuing if file is absent

    # 2. Non-empty
    text = svh_path.read_text(errors="replace")
    if not text.strip():
        result.errors.append(
            f"stimulus_current.svh is empty: {svh_path}\n"
            f"  → Regenerate from plugin gen_stimulus.py"
        )
        return result

    # 3. Task signature (exactly one)
    task_sig_matches = _RE_TASK_SIG.findall(text)
    if len(task_sig_matches) == 0:
        result.errors.append(
            f"Missing required signature in {svh_path.name}:\n"
            f"  Expected exactly: {STIMULUS_TASK_SIGNATURE}\n"
            f"  The task must be declared with exactly this signature so the\n"
            f"  framework testbench can call it portably."
        )

    # 4. endtask
    if not _RE_ENDTASK.search(text):
        result.errors.append(
            f"No 'endtask' found in {svh_path.name} — task body is not closed."
        )

    # 5. $finish calls are forbidden
    if _RE_FINISH.search(text):
        result.errors.append(
            f"$finish found in {svh_path.name}.\n"
            f"  $finish aborts the entire simulation unconditionally.\n"
            f"  Use $fatal(1, \"...\") for assertion failures instead.\n"
            f"  Use FAIL: display messages for soft check failures."
        )

    # 6. ap_clk reference (warning only — some flows may drive combinationally)
    if not _RE_CLK_REF.search(text):
        result.warnings.append(
            f"ap_clk not referenced in {svh_path.name}.\n"
            f"  Verify that stimulus timing is intentionally combinational.\n"
            f"  For clocked stimulus, add: @(posedge ap_clk);"
        )

    # 7. No duplicate task definitions (count full signature matches, ignoring comments)
    # Strip comment lines before counting to avoid matching the signature in header docs.
    non_comment_lines = [
        line for line in text.splitlines() if not line.lstrip().startswith("//")
    ]
    non_comment_text = "\n".join(non_comment_lines)
    all_task_sig_matches = _RE_TASK_SIG.findall(non_comment_text)
    if len(all_task_sig_matches) > 1:
        result.errors.append(
            f"Multiple 'task automatic run_stimulus();' declarations found in "
            f"{svh_path.name} ({len(all_task_sig_matches)} occurrences).\n"
            f"  Only one task definition is allowed per SVH file."
        )

    # 8. At least one check/assert/fail pattern (warn only — stub stim is common)
    if not _RE_FAIL_PAT.search(text):
        result.warnings.append(
            f"No check/assert/fail pattern found in {svh_path.name}.\n"
            f"  Stimulus without any checks will always pass regardless of DUT output.\n"
            f"  Add: if (signal !== expected) $fatal(1, \"FAIL: ...\");\n"
            f"  Or use: fw_verify.stimulus_helpers.StimulusEmitter.check()"
        )

    # 9. At least one input-driving statement (warn only)
    if not _RE_DRIVE_STMT.search(text):
        result.warnings.append(
            f"No input-driving statements detected in {svh_path.name}.\n"
            f"  The stimulus task appears to be empty or contain only comments.\n"
            f"  Add signal assignments: signal <= value;"
        )

    if result.ok:
        result.notes.append(f"Stimulus contract OK: {svh_path.name}")

    return result


def validate_stimulus_for_flow(
    cfg: Any,
    *,
    flow_dir: Path | None = None,
) -> StimulusContractResult:
    """Validate the stimulus file for a loaded flow config.

    Derives the ``stimulus_current.svh`` path from *cfg.flow_file*
    (or *flow_dir* override), then delegates to :func:`validate_stimulus`.

    Args:
        cfg:      Any flow config object with a ``flow_file`` attribute.
        flow_dir: Override the flow directory (default: ``Path(cfg.flow_file).parent``).

    Returns:
        ``StimulusContractResult``.
    """
    if flow_dir is None:
        flow_dir = Path(cfg.flow_file).parent
    svh_path = Path(flow_dir) / STIMULUS_FILE
    return validate_stimulus(svh_path)

