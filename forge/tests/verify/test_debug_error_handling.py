from __future__ import annotations

import sys
from pathlib import Path

import pytest


_FW_PYTHON = Path(__file__).resolve().parents[1]
if str(_FW_PYTHON) not in sys.path:
    sys.path.insert(0, str(_FW_PYTHON))

import forge.verify.__main__ as cli


def test_debug_mode_prints_traceback_for_unexpected_error(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def _boom(_args: object) -> int:
        raise RuntimeError("unexpected boom")

    monkeypatch.setattr(cli, "_cmd_generate", _boom)
    monkeypatch.setattr(sys, "argv", ["fw_verify", "--debug", "generate", "dummy.design.verification.yml"])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert int(exc_info.value.code) == 2
    captured = capsys.readouterr()
    assert "Traceback" in captured.err
    assert "RuntimeError: unexpected boom" in captured.err