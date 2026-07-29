"""
Tests for forge.core.toolchain_versions (release-plan Phase 5 slice 5.1).

Toolchain-version parsing is exercised against a fake PATH entry (a
trivial shell script), not a real installed Xilinx/GHDL/Verilator
toolchain — this environment has none of those installed, matching the
honest "synthetic-only" precedent already established for Phase 4 slice
4's csynth.xml fixture (docs/development/release-readiness.md).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from forge.core.toolchain_versions import (
    DEFAULT_TOOLS,
    collect_toolchain_versions,
    tool_present,
    tool_version,
)


def test_tool_present_false_when_not_on_path(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert tool_present("ghdl") is False


def test_tool_present_true_when_on_path(monkeypatch, tmp_path):
    _write_fake_tool(tmp_path, "ghdl", "#!/bin/sh\necho 'GHDL 4.1.0'\n")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert tool_present("ghdl") is True


def _write_fake_tool(bin_dir: Path, name: str, script: str) -> None:
    path = bin_dir / name
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def test_tool_version_returns_none_when_not_on_path(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))  # empty PATH — nothing resolves
    assert tool_version("ghdl") is None


def test_tool_version_parses_real_version_output(monkeypatch, tmp_path):
    _write_fake_tool(
        tmp_path, "ghdl",
        "#!/bin/sh\necho 'GHDL 4.1.0 (Ubuntu 4.1.0+dfsg-1) [Dunoon edition]'\n",
    )
    monkeypatch.setenv("PATH", str(tmp_path))

    version = tool_version("ghdl")
    assert version == "GHDL 4.1.0 (Ubuntu 4.1.0+dfsg-1) [Dunoon edition]"


def test_tool_version_uses_dash_version_flag_for_xilinx_tools(monkeypatch, tmp_path):
    """xvlog/xelab/xsim expect `-version` (single dash), not `--version` —
    a fake script that only understands `-version` proves the right flag
    is actually being passed, not just any flag."""
    _write_fake_tool(
        tmp_path, "xsim",
        "#!/bin/sh\n"
        'if [ "$1" = "-version" ]; then echo "xsim v2023.2"; else exit 1; fi\n',
    )
    monkeypatch.setenv("PATH", str(tmp_path))

    assert tool_version("xsim") == "xsim v2023.2"


def test_tool_version_falls_back_to_unknown_on_bad_exit(monkeypatch, tmp_path):
    _write_fake_tool(tmp_path, "verilator", "#!/bin/sh\nexit 1\n")
    monkeypatch.setenv("PATH", str(tmp_path))

    assert tool_version("verilator") == "unknown"


def test_tool_version_falls_back_to_unknown_on_empty_output(monkeypatch, tmp_path):
    _write_fake_tool(tmp_path, "verilator", "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", str(tmp_path))

    assert tool_version("verilator") == "unknown"


def test_tool_version_never_raises_on_a_hanging_tool(monkeypatch, tmp_path):
    _write_fake_tool(tmp_path, "ghdl", "#!/bin/sh\nsleep 30\n")
    # Prepend (not replace) PATH here — the fake script's own `sleep` call
    # needs a real `sleep` binary resolvable to actually hang.
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))

    assert tool_version("ghdl", timeout=0.2) == "unknown"


def test_collect_toolchain_versions_omits_tools_not_on_path(monkeypatch, tmp_path):
    _write_fake_tool(tmp_path, "ghdl", "#!/bin/sh\necho 'GHDL 4.1.0'\n")
    monkeypatch.setenv("PATH", str(tmp_path))

    versions = collect_toolchain_versions()
    assert versions == {"ghdl": "GHDL 4.1.0"}
    for other_tool in set(DEFAULT_TOOLS) - {"ghdl"}:
        assert other_tool not in versions


def test_collect_toolchain_versions_real_environment_never_raises():
    """No assumption about what's actually installed here — just that
    calling this in the real dev environment (no Xilinx/GHDL/Verilator
    confirmed present, per Phase 4's own environment notes) never raises."""
    versions = collect_toolchain_versions()
    assert isinstance(versions, dict)
