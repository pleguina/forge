#!/usr/bin/env python3
"""Simulator-agnostic checker stack, shared by every backend adapter.

Lifted out of ``backend_xsim.py`` (Phase 7, slice 7.0): nothing here
references Vivado, xsim, or any other simulator-specific tool or file —
it operates only on ``ExecutionResult`` (a log path, an exit code) and
``cfg``/``ctx`` duck-typed attributes shared across backends
(``checker_mode``, ``checker``, ``consumer_root``).

Backends call these as module-level functions, passing themselves as
``adapter`` so plugin-overridable hooks (``custom_checker_patterns``,
``checker_binary_path``, ``checker_command_args``) still resolve through
normal Python method lookup on the adapter instance — a plugin subclass
overriding one of those hooks is picked up here exactly as it was when
the logic lived directly on ``XsimBackend``.
"""
from __future__ import annotations

import subprocess as _sp
import sys
from pathlib import Path
from typing import Any

from forge.verify.backend_base import ExecutionResult


def run_checker(adapter: Any, cfg: Any, ctx: Any, result: ExecutionResult) -> bool:
    """Determine pass/fail according to ``checker_mode`` in the flow config.

    ``"log_scan"`` (default)
        Scans the simulate log for standard fatal markers plus any
        plugin-supplied custom patterns (``adapter.custom_checker_patterns``).

    ``"binary"``
        Invokes an external checker binary declared in the ``checker:``
        section of ``verify.flow.yml``.  The binary is located via
        ``adapter.checker_binary_path``; its CLI arguments are assembled
        from ``adapter.checker_command_args``.

    ``"none"``
        Always passes (simulator exit code only).
    """
    checker_mode = getattr(cfg, "checker_mode", "log_scan")

    if checker_mode == "none":
        return result.success

    if not result.success:
        return False

    if checker_mode == "binary":
        return _run_binary_checker(adapter, cfg, ctx, result)

    # Default: log_scan
    if result.log_path and result.log_path.exists():
        text = result.log_path.read_text(errors="replace")
        builtin_markers = ("$fatal", "ASSERTION FAILED", "TEST FAILED", "FAIL:")
        all_markers = list(builtin_markers) + adapter.custom_checker_patterns(cfg)
        for marker in all_markers:
            if marker in text:
                print(f"[checker] Failure marker detected: {marker!r}")
                return False
    return True


def _run_binary_checker(adapter: Any, cfg: Any, ctx: Any, result: ExecutionResult) -> bool:
    """Run the external checker binary declared in ``checker:`` of verify.flow.yml."""
    checker_cfg = getattr(cfg, "checker", None)
    if checker_cfg is None:
        return True

    bin_path = adapter.checker_binary_path(cfg)
    if bin_path is None or not bin_path.exists():
        tool = getattr(checker_cfg, "tool", "<unknown>")
        print(f"[checker] Skipped — binary not found: {tool}", file=sys.stderr)
        return True

    cmd = [str(bin_path)] + adapter.checker_command_args(cfg, ctx, result)
    ret = _sp.run(cmd, check=False)
    if ret.returncode == 0:
        print("Scoreboard check: PASS")
        return True
    print("Scoreboard check: FAIL", file=sys.stderr)
    return False


def checker_binary_path(cfg: Any) -> "Path | None":
    """Default resolution of the external checker binary from ``cfg.checker``.

    Adapters expose this as an overridable instance method (see
    ``XsimBackend.checker_binary_path``); this module-level function is the
    shared default implementation.
    """
    import shutil  # noqa: PLC0415

    checker = getattr(cfg, "checker", None)
    if checker is None:
        return None
    binary = getattr(checker, "binary", None)
    if binary is not None:
        return Path(binary)
    tool = getattr(checker, "tool", "")
    if tool:
        candidate = Path(tool)
        if candidate.is_absolute() and candidate.exists():
            return candidate
        found = shutil.which(tool)
        if found:
            return Path(found)
        rel = Path(cfg.consumer_root) / tool
        if rel.exists():
            return rel
    return None


def checker_command_args(cfg: Any, ctx: Any, result: ExecutionResult) -> list[str]:
    """Default CLI-argument assembly for the external checker binary.

    Adapters expose this as an overridable instance method (see
    ``XsimBackend.checker_command_args``); this module-level function is
    the shared default implementation.
    """
    checker = getattr(cfg, "checker", None)
    if checker is None:
        return []
    args = list(getattr(checker, "args", ()) or [])
    if not args:
        obs = getattr(checker, "observed_log", None)
        xml_input = getattr(ctx, "xml_input", None) or getattr(cfg, "dataset_xml", None)
        # v1.0 framework-standard checker contract for XML-backed datasets.
        return [
            "--obs", str(obs),
            "--xml", str(xml_input),
            "--event-id", str(getattr(ctx, "event_id", 1)),
            "--latency", str(getattr(checker, "latency_cycles", 0)),
            "--tolerance", str(getattr(checker, "tolerance", 0)),
        ]
    format_map = {
        "observed_log": str(getattr(checker, "observed_log", "")),
        "dataset_xml": str(getattr(ctx, "xml_input", None) or getattr(cfg, "dataset_xml", "")),
        "event_id": str(getattr(ctx, "event_id", 1)),
        "latency_cycles": str(getattr(checker, "latency_cycles", 0)),
        "tolerance": str(getattr(checker, "tolerance", 0)),
        "pass_condition": str(getattr(checker, "pass_condition", "")),
        "consumer_root": str(getattr(cfg, "consumer_root", "")),
        "flow_dir": str(Path(cfg.flow_file).parent.resolve()),
        "work_dir": str(getattr(ctx, "work_dir", "") or result.backend_metadata.get("work_dir", "")),
    }
    return [str(a).format(**format_map) for a in args]
