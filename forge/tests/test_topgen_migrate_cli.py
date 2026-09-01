"""
CLI-level tests for `forge topgen migrate`. Pure-function
tests for the migration logic itself live in test_migrate.py — these tests
only exercise argument handling, --dry-run's "never writes" guarantee, and
exit codes, mirroring the _run_topgen/Result/_tree_snapshot conventions in
test_topgen_cli_commands.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

import pytest
import yaml

from forge.core.cli.main import build_parser


class Result(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def _tree_snapshot(root: Path) -> "frozenset[str]":
    if not root.exists():
        return frozenset()
    return frozenset(str(p.relative_to(root)) for p in root.rglob("*"))


def _run_topgen(capsys: pytest.CaptureFixture[str], *args: str) -> Result:
    parser = build_parser()
    code = 0
    try:
        parsed = parser.parse_args(["topgen", *args])
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return Result(code, captured.out, captured.err)


# ─────────────────────────────────────────────────────────────────────────────
# schema-version
# ─────────────────────────────────────────────────────────────────────────────

def test_schema_version_dry_run_never_writes(capsys, tmp_path):
    design = tmp_path / "design.yml"
    design.write_text("part: xcvu13p\nclock_period: 4.0\n")
    before = _tree_snapshot(tmp_path)

    result = _run_topgen(capsys, "migrate", "--kind", "schema-version", "--file", str(design), "--dry-run")

    assert result.returncode == 0
    assert "inserted schema version" in result.stdout
    assert _tree_snapshot(tmp_path) == before
    assert 'schema_version' not in design.read_text()


def test_schema_version_real_run_writes(capsys, tmp_path):
    design = tmp_path / "design.yml"
    design.write_text("part: xcvu13p\nclock_period: 4.0\n")

    result = _run_topgen(capsys, "migrate", "--kind", "schema-version", "--file", str(design))

    assert result.returncode == 0
    assert "wrote" in result.stdout
    assert 'schema_version: "1.0"' in design.read_text()


def test_schema_version_missing_file_exits_2(capsys, tmp_path):
    result = _run_topgen(
        capsys, "migrate", "--kind", "schema-version", "--file", str(tmp_path / "missing.yml"),
    )
    assert result.returncode == 2
    assert "not found" in result.stderr


def test_schema_version_missing_required_file_arg_exits_2(capsys):
    result = _run_topgen(capsys, "migrate", "--kind", "schema-version")
    assert result.returncode == 2
    assert "--file is required" in result.stderr


# ─────────────────────────────────────────────────────────────────────────────
# partition-to-coordinates
# ─────────────────────────────────────────────────────────────────────────────

_CONTRACT_TEXT = """\
ip_interface:
  module_name: m
  roles:
    raw_hit:
      raw_port: raw_hit
      direction: input
      width: 32
      partition: lower_pair
"""


def test_partition_to_coordinates_dry_run_never_writes(capsys, tmp_path):
    contract = tmp_path / "m.interface.yaml"
    contract.write_text(_CONTRACT_TEXT)

    result = _run_topgen(
        capsys, "migrate", "--kind", "partition-to-coordinates",
        "--contract", str(contract), "--axis", "label", "--dry-run",
    )

    assert result.returncode == 0
    assert "raw_hit" in result.stdout
    assert "partition: lower_pair" in contract.read_text()


def test_partition_to_coordinates_real_run_writes(capsys, tmp_path):
    contract = tmp_path / "m.interface.yaml"
    contract.write_text(_CONTRACT_TEXT)

    result = _run_topgen(
        capsys, "migrate", "--kind", "partition-to-coordinates",
        "--contract", str(contract), "--axis", "label",
    )

    assert result.returncode == 0
    text = contract.read_text()
    assert "coordinates: {label: lower_pair}" in text
    assert "partition:" not in text


def test_partition_to_coordinates_unknown_role_exits_2(capsys, tmp_path):
    contract = tmp_path / "m.interface.yaml"
    contract.write_text(_CONTRACT_TEXT)

    result = _run_topgen(
        capsys, "migrate", "--kind", "partition-to-coordinates",
        "--contract", str(contract), "--axis", "label", "--role", "nope",
    )

    assert result.returncode == 2
    assert "not found" in result.stderr


# ─────────────────────────────────────────────────────────────────────────────
# legacy-plugin-layout
# ─────────────────────────────────────────────────────────────────────────────

def test_legacy_plugin_layout_dry_run_never_writes(capsys, tmp_path):
    (tmp_path / "verify").mkdir()
    (tmp_path / "verify" / "bootstrap.py").write_text(
        "import sys\n# _FW_PYTHON\nsys.path.insert(0, \"framework/verify/python\")\n"
    )
    before = _tree_snapshot(tmp_path)

    result = _run_topgen(
        capsys, "migrate", "--kind", "legacy-plugin-layout", "--plugin-root", str(tmp_path), "--dry-run",
    )

    assert result.returncode == 0
    assert "Would move" in result.stdout
    assert _tree_snapshot(tmp_path) == before


def test_legacy_plugin_layout_real_run_moves_directory(capsys, tmp_path):
    (tmp_path / "verify").mkdir()
    (tmp_path / "verify" / "bootstrap.py").write_text(
        "import sys\n# _FW_PYTHON\nsys.path.insert(0, \"framework/verify/python\")\n"
    )

    result = _run_topgen(
        capsys, "migrate", "--kind", "legacy-plugin-layout", "--plugin-root", str(tmp_path),
    )

    assert result.returncode == 0
    assert not (tmp_path / "verify").exists()
    assert (tmp_path / "forge" / "verify" / "bootstrap.py").exists()


def test_legacy_plugin_layout_clean_tree_is_a_noop(capsys, tmp_path):
    result = _run_topgen(
        capsys, "migrate", "--kind", "legacy-plugin-layout", "--plugin-root", str(tmp_path),
    )
    assert result.returncode == 0
    assert "Nothing to migrate" in result.stdout


# ─────────────────────────────────────────────────────────────────────────────
# rename-verify-contract
# ─────────────────────────────────────────────────────────────────────────────

def test_rename_verify_contract_real_run(capsys, tmp_path):
    verify_dir = tmp_path / "forge" / "verify"
    verify_dir.mkdir(parents=True)
    (verify_dir / "verify.design.yml").write_text("plugin: demo\n")

    result = _run_topgen(
        capsys, "migrate", "--kind", "rename-verify-contract", "--plugin-root", str(tmp_path),
    )

    assert result.returncode == 0
    assert (verify_dir / "design.verification.yml").exists()
    assert not (verify_dir / "verify.design.yml").exists()
