"""End-to-end coverage for `forge build` (deterministic generation plan),
driving the real argparse entry point in-process against
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
    """`--json`'s `{"plan": ..., "plan_hash": ...}`
    full-plan-dump shape becomes one CommandEnvelope — `metrics.counts`
    carries the same connection/transformation counts this test used to
    read off the raw plan lists, and `artifacts` carries
    `plan.output_artifacts` directly."""
    result = _run_build(
        capsys, str(TRIGGER_DESIGN), "--contracts-from", str(TRIGGER_MODULES),
        "--output", str(tmp_path / "algo_top.v"), "--json",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    counts = payload["metrics"]["counts"]
    assert payload["metrics"]["plan_hash"]
    # Real trigger_demo: 45 total connections, 16 inferred (auto_match/
    # topology_group) + 29 explicit (contract_wiring/port_map/ranges) —
    # cross-checked against the design's known connection count.
    assert counts["inferred_connections"] + counts["explicit_connections"] <= 45
    assert counts["inferred_connections"] > 0
    assert counts["explicit_connections"] > 0
    assert payload["metrics"]["design_name"] == "design"
    assert payload["artifacts"]


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
    h1 = json.loads(r1.stdout)["metrics"]["plan_hash"]
    h2 = json.loads(r2.stdout)["metrics"]["plan_hash"]
    assert h1 == h2


def test_accept_plan_hash_matching_exits_zero(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    first = _run_build(
        capsys, str(PASSTHROUGH_DESIGN), "--contracts-from", str(PASSTHROUGH_MODULES),
        "--output", str(tmp_path / "algo_top.v"), "--json",
    )
    h = json.loads(first.stdout)["metrics"]["plan_hash"]

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
    assert payload["status"] == "fail"
    assert "Plan hash mismatch" in payload["diagnostics"][0]["message"]
    assert payload["metrics"]["expected_plan_hash"] == "deadbeef"
    assert payload["metrics"]["actual_plan_hash"]


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


def _write_two_clock_domain_design(tmp_path: Path, *, cdc_block: str = "") -> tuple[Path, Path]:
    """Same synthetic two-RTL-module, two-clock-domain design used by
    `tests/test_topgen_cli_commands.py`'s `gen-top --strict` tests (kept
    as a local copy rather than a cross-test-file import, to avoid
    depending on pytest's import-mode resolving a sibling test module by
    bare name) — a real strict-mode violation (undeclared clock/reset
    domain crossing) reusable here for `forge build --strict`'s
    plan-only-path coverage."""
    (tmp_path / "interfaces").mkdir()
    (tmp_path / "interfaces" / "src.interface.yaml").write_text(
        "ip_interface:\n"
        "  module_name: src\n"
        "  ip_info_key: src\n"
        "  source_type: rtl\n"
        "  roles:\n"
        "    clock_primary: {raw_port: clk_a, direction: input, width: 1}\n"
        "    reset_primary: {raw_port: rst, direction: input, width: 1}\n"
        "    dout: {raw_port: dout, direction: output, width: 8}\n"
    )
    (tmp_path / "interfaces" / "dst.interface.yaml").write_text(
        "ip_interface:\n"
        "  module_name: dst\n"
        "  ip_info_key: dst\n"
        "  source_type: rtl\n"
        "  roles:\n"
        "    clock_primary: {raw_port: clk_b, direction: input, width: 1}\n"
        "    reset_primary: {raw_port: rst, direction: input, width: 1}\n"
        "    din: {raw_port: din, direction: input, width: 8}\n"
    )
    modules_yml = tmp_path / "modules.yml"
    modules_yml.write_text(
        "registry_version: '1'\n"
        "modules:\n"
        "  - name: src\n"
        "    kind: rtl\n"
        "    top: src_top\n"
        "    src: [src.v]\n"
        "    interface_contract: interfaces/src.interface.yaml\n"
        "  - name: dst\n"
        "    kind: rtl\n"
        "    top: dst_top\n"
        "    src: [dst.v]\n"
        "    interface_contract: interfaces/dst.interface.yaml\n"
    )
    design_yml = tmp_path / "design.yml"
    design_yml.write_text(
        "part: xcvu13p\n"
        "clock_period: 4.0\n"
        "modules:\n"
        "  - name: src\n"
        "    top: src_top\n"
        "    src: [src.v]\n"
        "  - name: dst\n"
        "    top: dst_top\n"
        "    src: [dst.v]\n"
        "connections:\n"
        "  - from: src\n"
        "    to: dst\n"
        "    port_map: [[dout, din]]\n"
        f"    {cdc_block}\n"
    )
    return design_yml, modules_yml


def test_plan_flags_undeclared_cdc_as_a_plan_error_regardless_of_strict(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """Pre-existing behavior (unchanged by this slice): an undeclared
    clock-domain crossing is a `plan.unresolved_issues` ERROR — surfaced
    unconditionally, since `compute_gen_top_plan`'s CDC check always runs
    (`verify_cdc`), and `forge build`'s own final status has always
    treated any `unresolved_issues` error as a plan failure independent
    of `--strict` (only `gen-top --apply`'s *generation-time* checks are
    strict-gated). `--strict` is plan-only, so nothing is ever written
    either way."""
    design_yml, modules_yml = _write_two_clock_domain_design(tmp_path)
    before = _tree_snapshot(tmp_path)

    for strict_flag in ([], ["--strict"]):
        result = _run_build(
            capsys, str(design_yml), "--contracts-from", str(modules_yml),
            "--output", str(tmp_path / "out" / "algo_top.v"), "--json", *strict_flag,
        )
        assert result.returncode == 1, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        assert payload["status"] == "fail"
        assert any(
            "undeclared clock-domain crossing" in d["message"]
            for d in payload["diagnostics"]
        )

    assert _tree_snapshot(tmp_path) == before  # plan-only: nothing written


def test_strict_plan_only_passes_with_declared_cdc_adapter(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    design_yml, modules_yml = _write_two_clock_domain_design(
        tmp_path, cdc_block="cdc: {kind: 2ff_sync}",
    )

    result = _run_build(
        capsys, str(design_yml), "--contracts-from", str(modules_yml),
        "--output", str(tmp_path / "out" / "algo_top.v"), "--strict", "--json",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] in ("pass", "warn")


def _write_one_contract_one_compat_design(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A synthetic 2-module design where `src` has a real interface
    contract and `dst` has none (compat mode) — isolates `--strict`'s
    *new* checks (compat-mode modules, auto-match/port_map_ranges wiring)
    from the CDC/topology-group/cardinality checks above, which are
    already unconditional `plan.unresolved_issues` errors regardless of
    `--strict` (see the CDC test above). Mirrors
    `test_inspect_cli_group.py`'s identically-named fixture — a pre-built
    `--ip-info` is required alongside `--contracts-from` for the same
    reason documented there."""
    (tmp_path / "interfaces").mkdir()
    (tmp_path / "interfaces" / "src.interface.yaml").write_text(
        "ip_interface:\n"
        "  module_name: src\n"
        "  ip_info_key: src\n"
        "  source_type: rtl\n"
        "  roles:\n"
        "    clock_primary: {raw_port: clk, direction: input, width: 1}\n"
        "    reset_primary: {raw_port: rst, direction: input, width: 1}\n"
        "    dout: {raw_port: dout, direction: output, width: 8}\n"
    )
    modules_yml = tmp_path / "modules.yml"
    modules_yml.write_text(
        "registry_version: '1'\n"
        "modules:\n"
        "  - name: src\n"
        "    kind: rtl\n"
        "    top: src_top\n"
        "    src: [src.v]\n"
        "    interface_contract: interfaces/src.interface.yaml\n"
        "  - name: dst\n"
        "    kind: rtl\n"
        "    top: dst_top\n"
        "    src: [dst.v]\n"
    )
    ip_info_yml = tmp_path / "ip_info.yaml"
    ip_info_yml.write_text(
        "src:\n"
        "  vendor: test\n"
        "  library: test\n"
        "  name: src\n"
        "  entity: src_top\n"
        "  version: '1.0'\n"
        "  kind: rtl\n"
        "  ports:\n"
        "    - {name: clk, direction: INPUT, width: 1, type: std_logic}\n"
        "    - {name: rst, direction: INPUT, width: 1, type: std_logic}\n"
        "    - {name: dout, direction: OUTPUT, width: 8, type: std_logic_vector}\n"
        "dst:\n"
        "  vendor: test\n"
        "  library: test\n"
        "  name: dst\n"
        "  entity: dst_top\n"
        "  version: '1.0'\n"
        "  kind: rtl\n"
        "  ports:\n"
        "    - {name: clk, direction: INPUT, width: 1, type: std_logic}\n"
        "    - {name: rst, direction: INPUT, width: 1, type: std_logic}\n"
        "    - {name: din, direction: INPUT, width: 8, type: std_logic_vector}\n"
    )
    design_yml = tmp_path / "design.yml"
    design_yml.write_text(
        "part: xcvu13p\n"
        "clock_period: 4.0\n"
        "modules:\n"
        "  - name: src\n"
        "    top: src_top\n"
        "    src: [src.v]\n"
        "  - name: dst\n"
        "    top: dst_top\n"
        "    src: [dst.v]\n"
        "connections:\n"
        "  - from: src\n"
        "    to: dst\n"
        "    port_map: [[dout, din]]\n"
    )
    return design_yml, modules_yml, ip_info_yml


def test_strict_flags_compat_mode_module_that_plain_plan_only_warns_on(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """The new `--strict` check this slice adds: a compat-mode module
    (matched via heuristics, no interface contract) is only a `status:
    "warn"` (exit 0) in plain plan mode, but a `status: "fail"` (exit 1)
    under `--strict` — proving `_strict_violations` is genuinely new
    behavior, not a restatement of an already-unconditional plan error
    (unlike the CDC case above)."""
    design_yml, modules_yml, ip_info_yml = _write_one_contract_one_compat_design(tmp_path)

    plain = _run_build(
        capsys, str(design_yml), "--contracts-from", str(modules_yml),
        "--ip-info", str(ip_info_yml),
        "--output", str(tmp_path / "out" / "algo_top.v"), "--json",
    )
    assert plain.returncode == 0, plain.stdout + plain.stderr
    plain_payload = json.loads(plain.stdout)
    assert plain_payload["status"] == "warn"
    assert plain_payload["metrics"]["counts"]["compat_mode_modules"] == 1

    strict = _run_build(
        capsys, str(design_yml), "--contracts-from", str(modules_yml),
        "--ip-info", str(ip_info_yml),
        "--output", str(tmp_path / "out" / "algo_top.v"), "--strict", "--json",
    )
    assert strict.returncode == 1, strict.stdout + strict.stderr
    strict_payload = json.loads(strict.stdout)
    assert strict_payload["status"] == "fail"
    assert any(
        d["severity"] == "error" and d.get("category") == "strict_mode"
        and "no interface contract" in d["message"]
        for d in strict_payload["diagnostics"]
    )


def test_dry_run_is_a_true_alias_for_plan_only(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """--dry-run must behave identically to omitting --apply (today's
    default) — same plan_hash, same exit code, nothing written."""
    before = _tree_snapshot(tmp_path)

    default_result = _run_build(
        capsys, str(PASSTHROUGH_DESIGN), "--contracts-from", str(PASSTHROUGH_MODULES),
        "--output", str(tmp_path / "algo_top.v"), "--json",
    )
    dry_run_result = _run_build(
        capsys, str(PASSTHROUGH_DESIGN), "--contracts-from", str(PASSTHROUGH_MODULES),
        "--output", str(tmp_path / "algo_top.v"), "--dry-run", "--json",
    )

    assert default_result.returncode == 0 == dry_run_result.returncode
    default_hash = json.loads(default_result.stdout)["metrics"]["plan_hash"]
    dry_run_hash = json.loads(dry_run_result.stdout)["metrics"]["plan_hash"]
    assert default_hash == dry_run_hash
    assert _tree_snapshot(tmp_path) == before


def test_dry_run_overrides_apply_when_both_given(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """--dry-run after --apply on the same command line wins (argparse's
    usual last-flag-for-a-shared-dest behavior) — nothing gets written."""
    before = _tree_snapshot(tmp_path)

    result = _run_build(
        capsys, str(PASSTHROUGH_DESIGN), "--contracts-from", str(PASSTHROUGH_MODULES),
        "--output", str(tmp_path / "algo_top.v"), "--apply", "--dry-run",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _tree_snapshot(tmp_path) == before


def test_provenance_and_explain_staleness_round_trip(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    prov_path = tmp_path / "provenance.json"

    write_result = _run_build(
        capsys, str(PASSTHROUGH_DESIGN), "--contracts-from", str(PASSTHROUGH_MODULES),
        "--output", str(tmp_path / "algo_top.v"),
        "--provenance", str(prov_path), "--json",
    )
    assert write_result.returncode == 0, write_result.stdout + write_result.stderr
    assert prov_path.exists()
    manifest = json.loads(prov_path.read_text())
    assert manifest["ir_content_hash"]
    payload = json.loads(write_result.stdout)
    assert str(prov_path) in payload["artifacts"]

    fresh = _run_build(
        capsys, str(PASSTHROUGH_DESIGN), "--contracts-from", str(PASSTHROUGH_MODULES),
        "--output", str(tmp_path / "algo_top.v"),
        "--explain-staleness", str(prov_path), "--json",
    )
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr
    fresh_staleness = json.loads(fresh.stdout)["metrics"]["explain_staleness"]
    assert fresh_staleness["stale"] is False


def test_explain_staleness_missing_file_is_guided(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    result = _run_build(
        capsys, str(PASSTHROUGH_DESIGN), "--contracts-from", str(PASSTHROUGH_MODULES),
        "--output", str(tmp_path / "algo_top.v"),
        "--explain-staleness", str(tmp_path / "does-not-exist.json"),
    )
    assert result.returncode == 1
    assert "not found" in result.stdout
