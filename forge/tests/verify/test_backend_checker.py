"""Unit tests for forge.verify.backend_checker.

The checker stack was lifted verbatim out of XsimBackend into module-level
functions — these tests exercise the lifted functions directly against
real, small log files on disk (matching test_toolchain_versions.py's
fake-PATH/fake-script convention), not by mocking the checker itself.
"""

from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from forge.verify import backend_checker
from forge.verify.backend_base import ExecutionResult


class _FakeAdapter:
    """Minimal adapter stand-in exposing the three checker override hooks."""

    def __init__(self, custom_patterns: list[str] | None = None) -> None:
        self._custom_patterns = custom_patterns or []

    def custom_checker_patterns(self, cfg):
        return self._custom_patterns

    def checker_binary_path(self, cfg):
        return backend_checker.checker_binary_path(cfg)

    def checker_command_args(self, cfg, ctx, result):
        return backend_checker.checker_command_args(cfg, ctx, result)


def _cfg(**overrides):
    base = dict(checker_mode="log_scan", checker=None, consumer_root=Path("/tmp"))
    base.update(overrides)
    return SimpleNamespace(**base)


# ── run_checker: "none" and pass-through of a failed run ───────────────────

def test_run_checker_none_mode_returns_result_success_verbatim():
    adapter = _FakeAdapter()
    cfg = _cfg(checker_mode="none")
    failed = ExecutionResult(success=False, exit_code=1)
    passed = ExecutionResult(success=True, exit_code=0)
    assert backend_checker.run_checker(adapter, cfg, None, failed) is False
    assert backend_checker.run_checker(adapter, cfg, None, passed) is True


def test_run_checker_returns_false_when_backend_run_itself_failed():
    adapter = _FakeAdapter()
    cfg = _cfg(checker_mode="log_scan")
    result = ExecutionResult(success=False, exit_code=1)
    assert backend_checker.run_checker(adapter, cfg, None, result) is False


# ── run_checker: log_scan mode against real log files ──────────────────────

def test_run_checker_log_scan_passes_on_clean_log(tmp_path: Path):
    log = tmp_path / "simulate.log"
    log.write_text("TB PASS: everything completed\n")
    adapter = _FakeAdapter()
    cfg = _cfg(checker_mode="log_scan")
    result = ExecutionResult(success=True, exit_code=0, log_path=log)
    assert backend_checker.run_checker(adapter, cfg, None, result) is True


@pytest.mark.parametrize(
    "marker", ["$fatal", "ASSERTION FAILED", "TEST FAILED", "FAIL: mismatch"]
)
def test_run_checker_log_scan_fails_on_builtin_markers(tmp_path: Path, marker: str):
    log = tmp_path / "simulate.log"
    log.write_text(f"some output\n{marker}\nmore output\n")
    adapter = _FakeAdapter()
    cfg = _cfg(checker_mode="log_scan")
    result = ExecutionResult(success=True, exit_code=0, log_path=log)
    assert backend_checker.run_checker(adapter, cfg, None, result) is False


def test_run_checker_log_scan_honors_adapter_custom_patterns(tmp_path: Path):
    log = tmp_path / "simulate.log"
    log.write_text("PLUGIN_SPECIFIC_FAILURE_MARKER\n")
    adapter = _FakeAdapter(custom_patterns=["PLUGIN_SPECIFIC_FAILURE_MARKER"])
    cfg = _cfg(checker_mode="log_scan")
    result = ExecutionResult(success=True, exit_code=0, log_path=log)
    assert backend_checker.run_checker(adapter, cfg, None, result) is False


def test_run_checker_log_scan_passes_when_log_path_missing():
    adapter = _FakeAdapter()
    cfg = _cfg(checker_mode="log_scan")
    result = ExecutionResult(success=True, exit_code=0, log_path=None)
    assert backend_checker.run_checker(adapter, cfg, None, result) is True


# ── run_checker: binary mode against a real fake checker script ────────────

def _write_fake_checker(bin_dir: Path, exit_code: int) -> Path:
    path = bin_dir / "fake_checker"
    path.write_text(f"#!/bin/sh\nexit {exit_code}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def test_run_checker_binary_mode_pass(tmp_path: Path):
    checker_bin = _write_fake_checker(tmp_path, exit_code=0)
    checker_cfg = SimpleNamespace(
        binary=checker_bin, tool="fake_checker", args=(),
        observed_log=tmp_path / "obs.csv", latency_cycles=0, tolerance=0,
    )
    adapter = _FakeAdapter()
    cfg = _cfg(checker_mode="binary", checker=checker_cfg)
    result = ExecutionResult(success=True, exit_code=0)
    assert backend_checker.run_checker(adapter, cfg, SimpleNamespace(), result) is True


def test_run_checker_binary_mode_fail(tmp_path: Path):
    checker_bin = _write_fake_checker(tmp_path, exit_code=1)
    checker_cfg = SimpleNamespace(
        binary=checker_bin, tool="fake_checker", args=(),
        observed_log=tmp_path / "obs.csv", latency_cycles=0, tolerance=0,
    )
    adapter = _FakeAdapter()
    cfg = _cfg(checker_mode="binary", checker=checker_cfg)
    result = ExecutionResult(success=True, exit_code=0)
    assert backend_checker.run_checker(adapter, cfg, SimpleNamespace(), result) is False


def test_run_checker_binary_mode_skips_when_binary_missing(tmp_path: Path):
    checker_cfg = SimpleNamespace(
        binary=tmp_path / "does_not_exist", tool="fake_checker", args=(),
        observed_log=tmp_path / "obs.csv", latency_cycles=0, tolerance=0,
    )
    adapter = _FakeAdapter()
    cfg = _cfg(checker_mode="binary", checker=checker_cfg)
    result = ExecutionResult(success=True, exit_code=0)
    # A missing checker binary is treated as "skip", not a failure.
    assert backend_checker.run_checker(adapter, cfg, SimpleNamespace(), result) is True


def test_run_checker_binary_mode_no_checker_config_is_a_pass():
    adapter = _FakeAdapter()
    cfg = _cfg(checker_mode="binary", checker=None)
    result = ExecutionResult(success=True, exit_code=0)
    assert backend_checker.run_checker(adapter, cfg, SimpleNamespace(), result) is True


# ── checker_binary_path ─────────────────────────────────────────────────────

def test_checker_binary_path_returns_none_without_checker_config():
    cfg = _cfg(checker=None)
    assert backend_checker.checker_binary_path(cfg) is None


def test_checker_binary_path_prefers_explicit_binary_field(tmp_path: Path):
    explicit = tmp_path / "explicit_binary"
    checker_cfg = SimpleNamespace(binary=explicit, tool="", args=())
    cfg = _cfg(checker=checker_cfg)
    assert backend_checker.checker_binary_path(cfg) == explicit


def test_checker_binary_path_resolves_tool_via_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    tool_bin = _write_fake_checker(tmp_path, exit_code=0)
    tool_bin.rename(tmp_path / "my_checker_tool")
    tool_bin = tmp_path / "my_checker_tool"
    tool_bin.chmod(tool_bin.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", str(tmp_path))

    checker_cfg = SimpleNamespace(binary=None, tool="my_checker_tool", args=())
    cfg = _cfg(checker=checker_cfg)
    resolved = backend_checker.checker_binary_path(cfg)
    assert resolved == tool_bin


# ── checker_command_args ────────────────────────────────────────────────────

def test_checker_command_args_default_contract_when_no_args_declared():
    checker_cfg = SimpleNamespace(
        args=(), observed_log=Path("/obs.csv"), latency_cycles=3, tolerance=1,
    )
    cfg = _cfg(checker=checker_cfg)
    ctx = SimpleNamespace(xml_input=Path("/dataset.xml"), event_id=2)
    result = ExecutionResult(success=True, exit_code=0)

    args = backend_checker.checker_command_args(cfg, ctx, result)

    assert args == [
        "--obs", "/obs.csv",
        "--xml", "/dataset.xml",
        "--event-id", "2",
        "--latency", "3",
        "--tolerance", "1",
    ]


def test_checker_command_args_returns_empty_without_checker_config():
    cfg = _cfg(checker=None)
    ctx = SimpleNamespace()
    result = ExecutionResult(success=True, exit_code=0)
    assert backend_checker.checker_command_args(cfg, ctx, result) == []


def test_checker_command_args_formats_custom_args_template():
    checker_cfg = SimpleNamespace(
        args=("--in={dataset_xml}", "--event={event_id}"),
        observed_log=Path("/obs.csv"), latency_cycles=0, tolerance=0,
        pass_condition="zero_mismatches",
    )
    cfg = _cfg(checker=checker_cfg, consumer_root=Path("/repo"), flow_file=Path("/repo/flow/verify.flow.yml"))
    ctx = SimpleNamespace(xml_input=Path("/dataset.xml"), event_id=5, work_dir=Path("/work"))
    result = ExecutionResult(success=True, exit_code=0)

    args = backend_checker.checker_command_args(cfg, ctx, result)

    assert args == ["--in=/dataset.xml", "--event=5"]
