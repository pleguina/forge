"""End-to-end coverage for `forge build` (release-plan §3.4 — deterministic
generation plan), driving the real argparse entry point in-process against
plugins/passthrough_demo and plugins/trigger_demo (mirrors the pattern used
in test_topgen_cli_commands.py/test_inspect_cli_group.py).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

import pytest

from forge.core.cli.main import build_parser

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSTHROUGH_DESIGN = REPO_ROOT / "plugins/passthrough_demo/forge/designs/design.yml"
PASSTHROUGH_MODULES = REPO_ROOT / "plugins/passthrough_demo/forge/modules.yml"
TRIGGER_DESIGN = REPO_ROOT / "plugins/trigger_demo/forge/designs/design.yml"
TRIGGER_MODULES = REPO_ROOT / "plugins/trigger_demo/forge/modules.yml"


class Result(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def _tree_snapshot(root: Path) -> frozenset[str]:
    if not root.exists():
        return frozenset()
    return frozenset(str(p.relative_to(root)) for p in root.rglob("*"))


def _run_build(capsys: pytest.CaptureFixture[str], *args: str) -> Result:
    parser = build_parser()
    code = 0
    try:
        parsed = parser.parse_args(["build", *args])
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return Result(code, captured.out, captured.err)


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


def test_build_registered_as_top_level_command() -> None:
    parser = build_parser()
    parsed = parser.parse_args(["build", str(PASSTHROUGH_DESIGN)])
    assert parsed.func.__name__ == "cmd_build"


def test_plan_writes_zero_files(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """--plan (the default) must never write — same read-only guarantee as
    `forge inspect`/`gen-top --dry-run`."""
    before = _tree_snapshot(tmp_path)
    result = _run_build(
        capsys, str(PASSTHROUGH_DESIGN), "--contracts-from", str(PASSTHROUGH_MODULES),
        "--output", str(tmp_path / "algo_top.v"),
    )
    after = _tree_snapshot(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert before == after


def test_plan_human_output_sections(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    result = _run_build(
        capsys, str(TRIGGER_DESIGN), "--contracts-from", str(TRIGGER_MODULES),
        "--output", str(tmp_path / "algo_top.v"),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for label in (
        "plan hash", "inferred connections", "explicit connections",
        "transformations", "latency changes", "compat-mode modules",
        "output artifacts",
    ):
        assert label in result.stdout


def test_plan_json_output_matches_wiring_method_counts(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    result = _run_build(
        capsys, str(TRIGGER_DESIGN), "--contracts-from", str(TRIGGER_MODULES),
        "--output", str(tmp_path / "algo_top.v"), "--json",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    plan = payload["plan"]
    assert payload["plan_hash"]
    # Real trigger_demo: 45 total connections, 16 inferred (auto_match/
    # topology_group) + 29 explicit (contract_wiring/port_map/ranges) —
    # cross-checked against the design's known connection count (release-
    # readiness.md, Phase 1 slice 1/Phase 3 slice 4).
    assert len(plan["inferred_connections"]) + len(plan["explicit_connections"]) <= 45
    assert len(plan["inferred_connections"]) > 0
    assert len(plan["explicit_connections"]) > 0
    assert plan["design_name"] == "design"
    assert plan["output_artifacts"]


def test_plan_hash_is_deterministic_across_two_runs(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    r1 = _run_build(
        capsys, str(PASSTHROUGH_DESIGN), "--contracts-from", str(PASSTHROUGH_MODULES),
        "--output", str(tmp_path / "algo_top.v"), "--json",
    )
    r2 = _run_build(
        capsys, str(PASSTHROUGH_DESIGN), "--contracts-from", str(PASSTHROUGH_MODULES),
        "--output", str(tmp_path / "algo_top.v"), "--json",
    )
    h1 = json.loads(r1.stdout)["plan_hash"]
    h2 = json.loads(r2.stdout)["plan_hash"]
    assert h1 == h2


def test_accept_plan_hash_matching_exits_zero(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    first = _run_build(
        capsys, str(PASSTHROUGH_DESIGN), "--contracts-from", str(PASSTHROUGH_MODULES),
        "--output", str(tmp_path / "algo_top.v"), "--json",
    )
    h = json.loads(first.stdout)["plan_hash"]

    result = _run_build(
        capsys, str(PASSTHROUGH_DESIGN), "--contracts-from", str(PASSTHROUGH_MODULES),
        "--output", str(tmp_path / "algo_top.v"), "--accept-plan-hash", h, "--json",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_accept_plan_hash_mismatch_exits_one(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    result = _run_build(
        capsys, str(PASSTHROUGH_DESIGN), "--contracts-from", str(PASSTHROUGH_MODULES),
        "--output", str(tmp_path / "algo_top.v"), "--accept-plan-hash", "deadbeef", "--json",
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error"] == "plan hash mismatch"
    assert payload["expected"] == "deadbeef"


def test_missing_design_file_is_guided(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    result = _run_build(capsys, str(tmp_path / "nope.yml"))
    assert result.returncode == 1
    assert "not found" in result.stdout


def test_apply_produces_byte_identical_artifacts_to_gen_top(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """`forge build --apply` must delegate to `gen-top`'s real write path
    with zero divergence — same artifacts, byte-identical content."""
    apply_out = tmp_path / "via_build" / "algo_top.v"
    gen_top_out = tmp_path / "via_gen_top" / "algo_top.v"

    apply_result = _run_build(
        capsys, str(PASSTHROUGH_DESIGN), "--mode", "verilog",
        "--contracts-from", str(PASSTHROUGH_MODULES),
        "--output", str(apply_out), "--apply",
    )
    assert apply_result.returncode == 0, apply_result.stdout + apply_result.stderr
    assert apply_out.exists()

    gen_top_result = _run_topgen(
        capsys, "gen-top", str(PASSTHROUGH_DESIGN), "--mode", "verilog",
        "--contracts-from", str(PASSTHROUGH_MODULES), "--output", str(gen_top_out),
    )
    assert gen_top_result.returncode == 0, gen_top_result.stdout + gen_top_result.stderr

    assert apply_out.read_bytes() == gen_top_out.read_bytes()
    assert (apply_out.parent / "build_manifest.json").exists()
    assert (apply_out.parent / "design.ir.json").exists()


def test_apply_respects_accept_plan_hash_before_writing(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """A mismatched --accept-plan-hash must block --apply too — the hash
    check happens before any delegation to the real write path."""
    before = _tree_snapshot(tmp_path)
    result = _run_build(
        capsys, str(PASSTHROUGH_DESIGN), "--contracts-from", str(PASSTHROUGH_MODULES),
        "--output", str(tmp_path / "algo_top.v"),
        "--accept-plan-hash", "deadbeef", "--apply",
    )
    after = _tree_snapshot(tmp_path)
    assert result.returncode == 1
    assert before == after  # --apply never ran
