"""Coverage for `forge topgen init-plugin` (core/cli/groups/topgen.py).

Scaffolds the topgen side of a new plugin capsule. The real value claim
is "zero further edits needed" — the end-to-end test below proves that by
actually running gen-top and core verify-contract against the freshly
scaffolded files, not just checking they exist.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import pytest

from forge.core.cli.main import build_parser


class Result(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def _run(capsys: pytest.CaptureFixture[str], group: str, *args: str) -> Result:
    parser = build_parser()
    code = 0
    try:
        parsed = parser.parse_args([group, *args])
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return Result(code, captured.out, captured.err)


def _run_topgen(capsys: pytest.CaptureFixture[str], *args: str) -> Result:
    return _run(capsys, "topgen", *args)


def _run_core(capsys: pytest.CaptureFixture[str], *args: str) -> Result:
    return _run(capsys, "core", *args)


def test_dry_run_lists_without_writing(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    result = _run_topgen(
        capsys, "init-plugin", "demo_plugin",
        "--plugins-root", str(tmp_path), "--dry-run",
    )

    assert result.returncode == 0
    assert "Would create plugin skeleton for 'demo_plugin'" in result.stdout
    assert not (tmp_path / "demo_plugin").exists()


def test_real_run_creates_all_four_files(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    result = _run_topgen(
        capsys, "init-plugin", "demo_plugin", "--plugins-root", str(tmp_path),
    )

    assert result.returncode == 0
    forge_root = tmp_path / "demo_plugin" / "forge"
    assert (forge_root / "modules.yml").exists()
    assert (forge_root / "designs" / "design.yml").exists()
    assert (forge_root / "interfaces" / "demo_plugin.interface.yaml").exists()
    assert (tmp_path / "demo_plugin" / "algo" / "rtl" / "demo_plugin.v").exists()
    assert "created" in result.stdout


def test_second_run_skips_existing_files(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    _run_topgen(capsys, "init-plugin", "demo_plugin", "--plugins-root", str(tmp_path))

    modules_yml = tmp_path / "demo_plugin" / "forge" / "modules.yml"
    modules_yml.write_text(modules_yml.read_text() + "\n# hand-edited\n")

    result = _run_topgen(capsys, "init-plugin", "demo_plugin", "--plugins-root", str(tmp_path))

    assert result.returncode == 0
    assert "skipped" in result.stdout
    assert "# hand-edited" in modules_yml.read_text()


def test_scaffolded_plugin_gen_tops_with_zero_edits(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The actual claim this command exists to prove: a freshly scaffolded
    plugin generates real structural Verilog immediately, with no
    hand-editing — gen-top, then core verify-contract against the result."""
    _run_topgen(capsys, "init-plugin", "demo_plugin", "--plugins-root", str(tmp_path))
    forge_root = tmp_path / "demo_plugin" / "forge"

    gen_result = _run_topgen(
        capsys, "gen-top", str(forge_root / "designs" / "design.yml"),
        "--mode", "verilog",
        "--contracts-from", str(forge_root / "modules.yml"),
        "--output", str(tmp_path / "out" / "algo_top.v"),
        "--build-dir", str(tmp_path / "build"),
    )
    assert gen_result.returncode == 0, gen_result.stdout + gen_result.stderr
    assert (tmp_path / "out" / "algo_top.v").exists()
    algo_top_text = (tmp_path / "out" / "algo_top.v").read_text()
    assert "module algo_top" in algo_top_text
    assert "demo_plugin demo_plugin (" in algo_top_text

    ip_summary_result = _run_topgen(
        capsys, "ip-summary", str(forge_root / "designs" / "design.yml"),
        "--build-dir", str(tmp_path / "build2"),
        "--output", str(tmp_path / "ip_info.yaml"),
    )
    assert ip_summary_result.returncode == 0

    contract_result = _run_core(
        capsys, "verify-contract",
        "--ip-info", str(tmp_path / "ip_info.yaml"),
        "--contract", str(forge_root / "interfaces" / "demo_plugin.interface.yaml"),
    )
    assert contract_result.returncode == 0, contract_result.stdout + contract_result.stderr
    assert "PASS" in contract_result.stdout
