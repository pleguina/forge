"""
End-to-end coverage for the `forge inspect` CLI command — the first
consumer of the canonical IR (forge.ir). Drives the real argparse entry
point in-process against plugins/passthrough_demo, mirroring the pattern
used in test_topgen_cli_commands.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

import pytest

from forge.core.cli.main import build_parser

REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN_YML = REPO_ROOT / "plugins/passthrough_demo/forge/designs/design.yml"
MODULES_YML = REPO_ROOT / "plugins/passthrough_demo/forge/modules.yml"
TRIGGER_DESIGN_YML = REPO_ROOT / "plugins/trigger_demo/forge/designs/design.yml"
TRIGGER_MODULES_YML = REPO_ROOT / "plugins/trigger_demo/forge/modules.yml"


class Result(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def _run_inspect(capsys: pytest.CaptureFixture[str], *args: str) -> Result:
    parser = build_parser()
    code = 0
    try:
        parsed = parser.parse_args(["inspect", *args])
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return Result(code, captured.out, captured.err)


def _tree_snapshot(root: Path) -> frozenset[str]:
    if not root.exists():
        return frozenset()
    return frozenset(str(p.relative_to(root)) for p in root.rglob("*"))


def test_inspect_human_output(capsys: pytest.CaptureFixture[str]) -> None:
    result = _run_inspect(capsys, str(DESIGN_YML), "--contracts-from", str(MODULES_YML))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "forge inspect" in result.stdout
    assert "content hash" in result.stdout
    assert "modules            : 1" in result.stdout


def test_inspect_json_output_is_well_formed(capsys: pytest.CaptureFixture[str]) -> None:
    result = _run_inspect(capsys, str(DESIGN_YML), "--contracts-from", str(MODULES_YML), "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "0.2.0"  # release-plan §3.3 kind-vocabulary expansion
    assert "content_hash" in payload
    assert len(payload["design"]["modules"]) == 1


def test_inspect_never_writes_without_emit_ir(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """forge inspect is read-only by default — the same invariant as
    topgen gen-top --dry-run, verified the same way (tree snapshot)."""
    tree_before = _tree_snapshot(tmp_path)

    import os
    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = _run_inspect(capsys, str(DESIGN_YML), "--contracts-from", str(MODULES_YML))
    finally:
        os.chdir(old_cwd)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _tree_snapshot(tmp_path) == tree_before


def test_inspect_emit_ir_writes_exactly_the_requested_file(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    out_path = tmp_path / "nested" / "design.ir.json"

    result = _run_inspect(
        capsys, str(DESIGN_YML), "--contracts-from", str(MODULES_YML),
        "--emit-ir", str(out_path),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert out_path.exists()
    payload = json.loads(out_path.read_text())
    assert payload["schema_version"] == "0.2.0"  # release-plan §3.3 kind-vocabulary expansion
    # Nothing else was written.
    assert _tree_snapshot(tmp_path) == frozenset({"nested", "nested/design.ir.json"})


def test_inspect_diff_against_itself_reports_no_changes(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    ir_path = tmp_path / "design.ir.json"
    _run_inspect(
        capsys, str(DESIGN_YML), "--contracts-from", str(MODULES_YML),
        "--emit-ir", str(ir_path),
    )

    result = _run_inspect(
        capsys, str(DESIGN_YML), "--contracts-from", str(MODULES_YML),
        "--diff", str(ir_path), "--json",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["hash_equal"] is True
    assert payload["instances"] == {"added": [], "removed": [], "changed": []}


def test_inspect_diff_round_trips_matching_evidence(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """Release-plan §3.5 (Slice 5): a connection carrying matching_evidence
    (trigger_demo's gather-pattern/wiring_kind/protocol/width-bearing
    connections) must survive --emit-ir -> --diff losslessly. Regression
    guard for the round-trip-drops-a-field bug class hit and fixed three
    times before this field existed (emission_order, top_ports, protocol)."""
    ir_path = tmp_path / "design.ir.json"
    _run_inspect(
        capsys, str(TRIGGER_DESIGN_YML), "--contracts-from", str(TRIGGER_MODULES_YML),
        "--emit-ir", str(ir_path),
    )
    emitted = json.loads(ir_path.read_text())
    conns_with_evidence = [
        c for c in emitted["design"]["connections"] if c.get("matching_evidence")
    ]
    assert conns_with_evidence  # the emitted IR actually has some to round-trip

    result = _run_inspect(
        capsys, str(TRIGGER_DESIGN_YML), "--contracts-from", str(TRIGGER_MODULES_YML),
        "--diff", str(ir_path), "--json",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["hash_equal"] is True
    assert payload["connections"] == {"added": [], "removed": [], "changed": []}


def test_inspect_provenance_writes_manifest(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    prov_path = tmp_path / "provenance.json"

    result = _run_inspect(
        capsys, str(DESIGN_YML), "--contracts-from", str(MODULES_YML),
        "--provenance", str(prov_path),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert prov_path.exists()
    payload = json.loads(prov_path.read_text())
    assert payload["ir_content_hash"]
    assert str(DESIGN_YML) in payload["source_hashes"]


def test_inspect_explain_staleness_fresh_then_stale(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    prov_path = tmp_path / "provenance.json"
    _run_inspect(
        capsys, str(DESIGN_YML), "--contracts-from", str(MODULES_YML),
        "--provenance", str(prov_path),
    )

    fresh = _run_inspect(
        capsys, str(DESIGN_YML), "--contracts-from", str(MODULES_YML),
        "--explain-staleness", str(prov_path), "--json",
    )
    assert fresh.returncode == 0
    fresh_payload = json.loads(fresh.stdout)
    assert fresh_payload["stale"] is False
    assert fresh_payload["reasons"] == []

    # Different --build-dir counts as a changed command option, even though
    # it has no effect here (contracts_from already resolves everything) —
    # this exercises the "command options changed" staleness reason.
    stale = _run_inspect(
        capsys, str(DESIGN_YML), "--contracts-from", str(MODULES_YML),
        "--build-dir", str(tmp_path / "some_build_dir"),
        "--explain-staleness", str(prov_path), "--json",
    )
    assert stale.returncode == 1
    stale_payload = json.loads(stale.stdout)
    assert stale_payload["stale"] is True
    assert any("command options changed" in r for r in stale_payload["reasons"])


def test_inspect_explain_staleness_missing_file_is_guided(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    result = _run_inspect(
        capsys, str(DESIGN_YML), "--contracts-from", str(MODULES_YML),
        "--explain-staleness", str(tmp_path / "does-not-exist.json"),
    )

    assert result.returncode == 1
    assert "not found" in result.stdout


def test_inspect_missing_design_is_guided(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    missing = tmp_path / "missing.design.yml"
    result = _run_inspect(capsys, str(missing))

    assert result.returncode == 1
    assert "Design file not found" in result.stdout
    assert "Traceback" not in result.stderr


def test_inspect_missing_diff_file_is_guided(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    result = _run_inspect(
        capsys, str(DESIGN_YML), "--contracts-from", str(MODULES_YML),
        "--diff", str(tmp_path / "does-not-exist.json"),
    )

    assert result.returncode == 1
    assert "not found" in result.stdout
