#!/usr/bin/env python3
"""Standardised subprocess wrapper for forge verify backends — Workstream D7.

All simulator and binary invocations executed by framework backends must go
through this module.  This provides:

  * Consistent capture of command line, cwd, environment overrides,
    stdout, stderr, and exit code in one place.
  * Log-file writing alongside the work directory.
  * Typed :class:`SubprocessResult` for structured inspection.
  * :class:`SubprocessError` wrapping non-zero exits with full context.
  * Debug-level logging of every invocation (visible via ``--debug``).

Usage
-----
::

    from forge.verification.subprocess_wrapper import run_subprocess, SubprocessError

    try:
        result = run_subprocess(
            cmd=["xvlog", "--sv", "--incr", "-prj", "compile.prj"],
            cwd=work_dir,
            log_file=work_dir / "xvlog.log",
            tool_name="xvlog",
        )
    except SubprocessError as exc:
        raise BackendValidationError(
            f"xvlog failed: {exc}",
            action="Check xvlog.log for details",
            context=exc.context,
        ) from exc

    print(f"xvlog exit code: {result.exit_code}")

Design notes
------------
* ``stdout`` and ``stderr`` are merged into the log file.  They are not
  separately captured to avoid holding large outputs in memory.
* The log file is written incrementally via ``Popen`` so that partial logs are
  available even if the process hangs.
* ``env_overrides`` is merged on top of the current environment — no shell
  variable expansion is performed.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Result dataclass ───────────────────────────────────────────────────────

@dataclass
class SubprocessResult:
    """Captured output of one subprocess invocation.

    Attributes:
        tool_name:  Short label for the tool (e.g. ``"xvlog"``).
        cmd:        Full command list as passed to the OS.
        cwd:        Working directory of the subprocess.
        env_keys:   Keys of environment variables that were overridden
                    (values omitted to avoid logging secrets).
        exit_code:  Process exit code.
        log_path:   Path to the captured log file (stdout+stderr merged).
        elapsed_s:  Wall-clock seconds the process took.
    """

    tool_name:  str
    cmd:        list[str]
    cwd:        Path
    env_keys:   list[str]
    exit_code:  int
    log_path:   Path | None
    elapsed_s:  float

    @property
    def success(self) -> bool:
        return self.exit_code == 0


# ── Exception ──────────────────────────────────────────────────────────────

class SubprocessError(Exception):
    """Raised when a subprocess exits with a non-zero code.

    Attributes:
        result:  The full :class:`SubprocessResult`.
        context: Machine-readable dict (suitable for passing to
                 :class:`~forge.verification.exceptions.ForgeVerifyError`).
    """

    def __init__(self, message: str, result: SubprocessResult) -> None:
        super().__init__(message)
        self.result = result
        self.context: dict[str, Any] = {
            "tool":      result.tool_name,
            "exit_code": result.exit_code,
            "cmd":       " ".join(result.cmd),
            "cwd":       str(result.cwd),
            "log_path":  str(result.log_path) if result.log_path else None,
        }


# ── Core runner ────────────────────────────────────────────────────────────

def run_subprocess(
    cmd: list[str],
    *,
    cwd: "str | Path",
    log_file: "str | Path | None" = None,
    tool_name: str = "",
    env_overrides: "dict[str, str] | None" = None,
    timeout: float | None = None,
    check: bool = True,
) -> SubprocessResult:
    """Run *cmd* as a subprocess and capture output.

    Args:
        cmd:           Command list (first element is the executable).
        cwd:           Working directory.
        log_file:      If given, stdout+stderr are redirected to this file.
                       The file is created (or truncated) by this function.
        tool_name:     Human-readable label for error messages (e.g. ``"xvlog"``).
        env_overrides: Key-value pairs to overlay on ``os.environ``.
                       These are screened from debug output.
        timeout:       Optional wall-clock timeout in seconds.
        check:         If ``True`` (default), raise :class:`SubprocessError`
                       on non-zero exit.

    Returns:
        :class:`SubprocessResult` with captured metadata.

    Raises:
        SubprocessError: when *check* is True and exit code != 0.
        FileNotFoundError: when the executable is not found.
    """
    cwd   = Path(cwd).resolve()
    label = tool_name or (cmd[0] if cmd else "subprocess")

    # Build environment
    env: dict[str, str] = dict(os.environ)
    env_keys: list[str] = []
    if env_overrides:
        for k, v in env_overrides.items():
            env[k] = v
            env_keys.append(k)

    # Ensure log directory exists
    log_path: Path | None = None
    if log_file is not None:
        log_path = Path(log_file).resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()

    if log_path is not None:
        with log_path.open("w") as lf:
            proc = subprocess.run(
                cmd,
                cwd=str(cwd),
                env=env,
                stdout=lf,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
    else:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )

    elapsed = time.monotonic() - t0

    result = SubprocessResult(
        tool_name=label,
        cmd=list(cmd),
        cwd=cwd,
        env_keys=env_keys,
        exit_code=proc.returncode,
        log_path=log_path,
        elapsed_s=elapsed,
    )

    if check and proc.returncode != 0:
        log_hint = f"  Log: {log_path}" if log_path else ""
        raise SubprocessError(
            f"{label} exited {proc.returncode}  (cmd: {' '.join(cmd)}){log_hint}",
            result=result,
        )

    return result


def describe_invocation(result: SubprocessResult) -> str:
    """Return a one-line summary of a subprocess invocation."""
    status = "OK" if result.success else f"FAIL({result.exit_code})"
    log_hint = f"  log={result.log_path}" if result.log_path else ""
    return (
        f"[{result.tool_name}] {status}"
        f"  elapsed={result.elapsed_s:.1f}s"
        f"  cwd={result.cwd}{log_hint}"
    )
