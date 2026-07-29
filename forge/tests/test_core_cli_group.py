"""End-to-end coverage for `forge core` (core/cli/groups/core.py, ~24%).

Same recipe as test_topgen_cli_commands.py: drive the real argparse entry
point in-process (via build_parser()) so pytest-cov credits the executed
lines, instead of only being exercised indirectly through integration
scripts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

import pytest

from forge.core.cli.main import build_parser

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSTHROUGH_CONTRACT = (
    REPO_ROOT / "plugins/passthrough_demo/forge/interfaces/passthrough.interface.yaml"
)

_IP_INFO_YAML = """\
m1:
  ports:
    - name: ap_clk
      direction: IN
      width: 1
    - name: ap_rst
      direction: IN
      width: 1
    - name: data_in
      direction: IN
      width: 8
    - name: data_out
      direction: OUT
      width: 8
"""

_PASSING_CONTRACT_YAML = """\
ip_interface:
  ip_info_key: m1
  module_name: m1
  roles:
    clock_primary:
      raw_port: ap_clk
      direction: input
      width: 1
    reset_primary:
      raw_port: ap_rst
      direction: input
      width: 1
    data_in:
      raw_port: data_in
      direction: input
      width: 8
    data_out:
      raw_port: data_out
      direction: output
      width: 8
"""


class Result(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def _run_core(capsys: pytest.CaptureFixture[str], *args: str) -> Result:
    parser = build_parser()
    code = 0
    try:
        parsed = parser.parse_args(["core", *args])
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return Result(code, captured.out, captured.err)


@pytest.fixture
def ip_info_and_contract(tmp_path: Path) -> tuple[Path, Path]:
    ip_info = tmp_path / "ip_info.yaml"
    ip_info.write_text(_IP_INFO_YAML)
    contract = tmp_path / "m1.interface.yaml"
    contract.write_text(_PASSING_CONTRACT_YAML)
    return ip_info, contract


def test_resources_default_text_format(capsys: pytest.CaptureFixture[str]) -> None:
    result = _run_core(capsys, "resources")

    assert result.returncode == 0
    assert "hls_templates=" in result.stdout
    assert "canonical_roles=" in result.stdout


def test_resources_json_format(capsys: pytest.CaptureFixture[str]) -> None:
    """Release-plan Phase 6 §6.7: `--format json` (a pre-existing flag,
    kept as a deprecated alias) and the new `--json` both now produce the
    shared CommandEnvelope shape."""
    result = _run_core(capsys, "resources", "--format", "json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"]
    resources = payload["metrics"]["resources"]
    assert set(resources) == {
        "hls_templates", "canonical_roles", "normalized_signal_families", "docs_root",
    }
    # hls_templates and canonical_roles are real installed package resources,
    # always present regardless of cwd.
    assert resources["hls_templates"]["exists"] is True
    assert resources["canonical_roles"]["exists"] is True


def test_resources_key_prints_a_real_path(capsys: pytest.CaptureFixture[str]) -> None:
    result = _run_core(capsys, "resources", "--key", "hls_templates")

    assert result.returncode == 0
    assert result.stdout.strip().endswith("hls/templates")


def test_resources_unknown_key_errors(capsys: pytest.CaptureFixture[str]) -> None:
    """Release-plan Phase 6 §6.7: an unknown `--key` is a usage error, so
    it now maps to exit code 2 (the sole remaining meaning of 2 under the
    reconciled exit-code policy) — previously 1, conflated with a real
    validation failure."""
    result = _run_core(capsys, "resources", "--key", "does-not-exist")

    assert result.returncode == 2
    assert "unknown resource key" in result.stderr


def test_verify_contract_missing_ip_info_file(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    result = _run_core(
        capsys, "verify-contract",
        "--ip-info", str(tmp_path / "missing.yaml"),
        "--contract", str(tmp_path / "missing2.yaml"),
    )

    assert result.returncode == 2
    assert "ip_info file not found" in result.stderr


def test_verify_contract_missing_contract_file(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, ip_info_and_contract: tuple[Path, Path]
) -> None:
    ip_info, _contract = ip_info_and_contract

    result = _run_core(
        capsys, "verify-contract",
        "--ip-info", str(ip_info),
        "--contract", str(tmp_path / "missing.yaml"),
    )

    assert result.returncode == 2
    assert "Contract file not found" in result.stderr


def test_verify_contract_requires_contract_or_all_contracts(
    capsys: pytest.CaptureFixture[str], ip_info_and_contract: tuple[Path, Path]
) -> None:
    ip_info, _contract = ip_info_and_contract

    result = _run_core(capsys, "verify-contract", "--ip-info", str(ip_info))

    assert result.returncode == 2
    assert "Provide --contract" in result.stderr


def test_verify_contract_single_contract_passes(
    capsys: pytest.CaptureFixture[str], ip_info_and_contract: tuple[Path, Path]
) -> None:
    ip_info, contract = ip_info_and_contract

    result = _run_core(
        capsys, "verify-contract", "--ip-info", str(ip_info), "--contract", str(contract),
    )

    assert result.returncode == 0
    assert "PASS" in result.stdout


def test_verify_contract_passes_against_real_passthrough_demo_fixture(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """plugins/passthrough_demo's real, checked-in interface contract,
    verified against the real ip_info.yaml key its RTL module generates
    ('pt' — the design.yml instance name, not the module_name)."""
    ip_info = tmp_path / "ip_info.yaml"
    ip_info.write_text(
        "pt:\n"
        "  ports:\n"
        "    - {name: ap_clk, direction: IN, width: 1}\n"
        "    - {name: ap_rst, direction: IN, width: 1}\n"
        "    - {name: data_in, direction: IN, width: 8}\n"
        "    - {name: data_in_valid, direction: IN, width: 1}\n"
        "    - {name: data_out, direction: OUT, width: 8}\n"
        "    - {name: data_out_valid, direction: OUT, width: 1}\n"
    )

    result = _run_core(
        capsys, "verify-contract",
        "--ip-info", str(ip_info), "--contract", str(PASSTHROUGH_CONTRACT),
    )

    assert result.returncode == 0
    assert "PASS" in result.stdout


def test_verify_contract_reports_ip_info_key_mismatch(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """plugins/passthrough_demo's contract declares ip_info_key: pt — verify
    that against an ip_info.yaml that doesn't have that key, the CLI reports
    the mismatch rather than silently passing or crashing."""
    ip_info = tmp_path / "ip_info.yaml"
    ip_info.write_text(_IP_INFO_YAML)  # only has key "m1", not "pt"

    result = _run_core(
        capsys, "verify-contract",
        "--ip-info", str(ip_info), "--contract", str(PASSTHROUGH_CONTRACT),
    )

    # Release-plan Phase 6 §6.7: a real contract-verification failure is
    # status "fail" -> exit 1, not 2 — exit code 2 is now reserved solely
    # for usage errors/unexpected exceptions (previously conflated: this
    # case used VerifyResult.exit_code()'s own errors-> 2 convention).
    assert result.returncode == 1
    assert "ip_info_key 'pt' not found" in result.stdout


def test_verify_contract_all_contracts_mode(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, ip_info_and_contract: tuple[Path, Path]
) -> None:
    ip_info, contract = ip_info_and_contract
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    (contracts_dir / "m1.interface.yaml").write_text(contract.read_text())

    result = _run_core(
        capsys, "verify-contract", "--ip-info", str(ip_info),
        "--all-contracts", str(contracts_dir),
    )

    assert result.returncode == 0
    assert "1 pass" in result.stdout


def test_verify_contract_all_contracts_finds_nothing(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, ip_info_and_contract: tuple[Path, Path]
) -> None:
    ip_info, _contract = ip_info_and_contract
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    result = _run_core(
        capsys, "verify-contract", "--ip-info", str(ip_info),
        "--all-contracts", str(empty_dir),
    )

    assert result.returncode == 1
    assert "No contracts found" in result.stderr
