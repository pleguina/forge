"""End-to-end coverage for `forge framework` (core/cli/groups/framework.py, ~22%).

Same recipe as test_topgen_cli_commands.py: drive the real argparse entry
point in-process (via build_parser()) against the same minimal ABI /
endpoints / detector_io fixtures already used to unit-test
forge.integration.importer and forge.integration.io_resolver directly
(test_framework_import.py, test_detector_io_resolution.py) — this file
drives the CLI wrapper around them instead.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

import pytest

from forge.core.cli.main import build_parser
from test_detector_io_resolution import _ABI, _EPS, _DETECTOR_IO_YAML


class Result(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def _run_framework(capsys: pytest.CaptureFixture[str], *args: str) -> Result:
    parser = build_parser()
    code = 0
    try:
        parsed = parser.parse_args(["framework", *args])
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return Result(code, captured.out, captured.err)


@pytest.fixture
def abi_and_endpoints(tmp_path: Path) -> tuple[Path, Path]:
    abi_path = tmp_path / "payload_abi.json"
    abi_path.write_text(json.dumps(_ABI))
    ep_path = tmp_path / "payload_endpoints.json"
    ep_path.write_text(json.dumps(_EPS))
    return abi_path, ep_path


@pytest.fixture
def imported_framework(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, abi_and_endpoints: tuple[Path, Path]
) -> Path:
    abi_path, ep_path = abi_and_endpoints
    out_dir = tmp_path / "import_out"

    result = _run_framework(
        capsys, "import",
        "--provider", "blobfish",
        "--abi", str(abi_path),
        "--endpoints", str(ep_path),
        "--out", str(out_dir),
    )
    assert result.returncode == 0, result.stderr
    return out_dir


def test_import_writes_framework_import_json(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, abi_and_endpoints: tuple[Path, Path]
) -> None:
    abi_path, ep_path = abi_and_endpoints
    out_dir = tmp_path / "import_out"

    result = _run_framework(
        capsys, "import",
        "--provider", "blobfish", "--abi", str(abi_path),
        "--endpoints", str(ep_path), "--out", str(out_dir),
    )

    assert result.returncode == 0
    assert "Framework import OK" in result.stdout
    summary = json.loads((out_dir / "framework_import.json").read_text())
    assert summary["provider"] == "blobfish"
    assert summary["port_count"] == len(_ABI["ports"])


def test_import_missing_abi_file_is_guided(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, abi_and_endpoints: tuple[Path, Path]
) -> None:
    _abi_path, ep_path = abi_and_endpoints

    result = _run_framework(
        capsys, "import",
        "--provider", "blobfish",
        "--abi", str(tmp_path / "missing.json"),
        "--endpoints", str(ep_path),
        "--out", str(tmp_path / "out"),
    )

    assert result.returncode == 1
    assert "ERROR" in result.stderr


def test_io_resolve_writes_resolved_json(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, imported_framework: Path
) -> None:
    detector_io = tmp_path / "detector_io.yml"
    detector_io.write_text(_DETECTOR_IO_YAML)
    out_dir = tmp_path / "resolve_out"

    result = _run_framework(
        capsys, "io-resolve",
        "--framework", str(imported_framework),
        "--detector-io", str(detector_io),
        "--out", str(out_dir),
    )

    assert result.returncode == 0
    assert "Detector I/O resolved: 2 inputs, 1 outputs" in result.stdout
    assert (out_dir / "detector_io.resolved.json").exists()


def test_io_resolve_missing_framework_import_is_guided(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    detector_io = tmp_path / "detector_io.yml"
    detector_io.write_text(_DETECTOR_IO_YAML)

    result = _run_framework(
        capsys, "io-resolve",
        "--framework", str(tmp_path / "does-not-exist"),
        "--detector-io", str(detector_io),
        "--out", str(tmp_path / "out"),
    )

    assert result.returncode == 1
    assert "framework_import.json not found" in result.stderr


def test_emit_payload_with_resolved_io(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, imported_framework: Path
) -> None:
    detector_io = tmp_path / "detector_io.yml"
    detector_io.write_text(_DETECTOR_IO_YAML)
    resolve_out = tmp_path / "resolve_out"
    _run_framework(
        capsys, "io-resolve",
        "--framework", str(imported_framework),
        "--detector-io", str(detector_io),
        "--out", str(resolve_out),
    )

    payload_out = tmp_path / "payload.v"
    result = _run_framework(
        capsys, "emit-payload",
        "--framework", str(imported_framework),
        "--detector-io", str(resolve_out / "detector_io.resolved.json"),
        "--out", str(payload_out),
    )

    assert result.returncode == 0
    assert "Generated payload wrapper" in result.stdout
    assert payload_out.exists()


def test_emit_payload_without_detector_io_uses_dummy_stub(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, imported_framework: Path
) -> None:
    payload_out = tmp_path / "payload_dummy.v"

    result = _run_framework(
        capsys, "emit-payload",
        "--framework", str(imported_framework),
        "--out", str(payload_out),
    )

    assert result.returncode == 0
    assert payload_out.exists()


def test_emit_payload_missing_detector_io_file_is_guided(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, imported_framework: Path
) -> None:
    result = _run_framework(
        capsys, "emit-payload",
        "--framework", str(imported_framework),
        "--detector-io", str(tmp_path / "missing.json"),
        "--out", str(tmp_path / "payload.v"),
    )

    assert result.returncode == 1
    assert "detector_io.resolved.json not found" in result.stderr
