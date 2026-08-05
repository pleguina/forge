"""End-to-end coverage for `forge build`/`forge inspect`'s CLI workflows —
plan-hash determinism, provenance/staleness, and the visual explorer
(--dot/--explorer) — applied to THIS plugin's own real designs for the
first time (release-plan Phase 10, docs/internal/phase10/preflight.md
§11's 10.7C sub-slice).

The framework-level suites (forge/tests/test_build_cli_group.py,
forge/tests/test_inspect_cli_group.py) already prove these CLI workflows
work in general, against passthrough_demo/trigger_demo. This file proves
the same workflows against vision_pipeline_demo's own richest designs —
design_full_functional.yml (11 instances, 2 clock/reset domains, two real
async_fifo CDC crossings) and design_platform_wrapper.yml (12 instances,
3 clock/reset domains, a real mailbox_transfer + two real async_fifo
crossings) — which had never been driven through `forge build --json`/
`forge inspect --provenance/--dot/--explorer` before this slice. All
designs here resolve their ip_info entirely from `modules.yml`'s real
interface contracts (every module in this plugin's registry has one — no
component.xml/HLS-build scan is needed, matching the framework tests'
own passthrough_demo/trigger_demo convention).

Drives the real argparse entry point in-process, mirroring the pattern
established by test_build_cli_group.py/test_inspect_cli_group.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

import pytest

from forge.core.cli.main import build_parser

REPO_ROOT = Path(__file__).resolve().parents[6]
MODULES_YML = REPO_ROOT / "plugins/vision_pipeline_demo/forge/modules.yml"
FULL_FUNCTIONAL_DESIGN = (
    REPO_ROOT / "plugins/vision_pipeline_demo/forge/designs/design_full_functional.yml"
)
PLATFORM_WRAPPER_DESIGN = (
    REPO_ROOT / "plugins/vision_pipeline_demo/forge/designs/design_platform_wrapper.yml"
)
VERIFY_DESIGN = REPO_ROOT / "plugins/vision_pipeline_demo/forge/verify/design.verification.yml"


class Result(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def _run(capsys: pytest.CaptureFixture[str], command: str, *args: str) -> Result:
    parser = build_parser()
    code = 0
    try:
        parsed = parser.parse_args([command, *args])
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return Result(code, captured.out, captured.err)


def _run_build(capsys: pytest.CaptureFixture[str], *args: str) -> Result:
    return _run(capsys, "build", *args)


def _run_inspect(capsys: pytest.CaptureFixture[str], *args: str) -> Result:
    return _run(capsys, "inspect", *args)


def _tree_snapshot(root: Path) -> frozenset[str]:
    if not root.exists():
        return frozenset()
    return frozenset(str(p.relative_to(root)) for p in root.rglob("*"))


# ── forge build: plan-hash workflow ─────────────────────────────────────


def test_build_plan_hash_is_deterministic_for_full_functional_design(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    r1 = _run_build(
        capsys, str(FULL_FUNCTIONAL_DESIGN), "--contracts-from", str(MODULES_YML),
        "--output", str(tmp_path / "algo_top.v"), "--json",
    )
    r2 = _run_build(
        capsys, str(FULL_FUNCTIONAL_DESIGN), "--contracts-from", str(MODULES_YML),
        "--output", str(tmp_path / "algo_top.v"), "--json",
    )
    assert r1.returncode == 0, r1.stdout + r1.stderr
    p1, p2 = json.loads(r1.stdout), json.loads(r2.stdout)
    assert p1["status"] == "pass"
    assert p1["metrics"]["plan_hash"] == p2["metrics"]["plan_hash"]
    # Real design, not a stub: two async_fifo CDC crossings (prpack->pktz,
    # tspack->pktz) resolve with zero inferred connections — every one of
    # this design's 116 connections is an explicit port_map (10.7A's own
    # completion evidence, re-proven here through forge build's own plan
    # computation rather than assumed from forge analyze latency-check).
    counts = p1["metrics"]["counts"]
    assert counts["explicit_connections"] == 116
    assert counts["inferred_connections"] == 0
    assert counts["compat_mode_modules"] == 0


def test_build_accept_plan_hash_matching_and_mismatching_for_platform_wrapper_design(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    first = _run_build(
        capsys, str(PLATFORM_WRAPPER_DESIGN), "--contracts-from", str(MODULES_YML),
        "--output", str(tmp_path / "algo_top.v"), "--json",
    )
    assert first.returncode == 0, first.stdout + first.stderr
    h = json.loads(first.stdout)["metrics"]["plan_hash"]
    assert h

    matching = _run_build(
        capsys, str(PLATFORM_WRAPPER_DESIGN), "--contracts-from", str(MODULES_YML),
        "--output", str(tmp_path / "algo_top.v"), "--accept-plan-hash", h, "--json",
    )
    assert matching.returncode == 0, matching.stdout + matching.stderr

    mismatching = _run_build(
        capsys, str(PLATFORM_WRAPPER_DESIGN), "--contracts-from", str(MODULES_YML),
        "--output", str(tmp_path / "algo_top.v"), "--accept-plan-hash", "deadbeef", "--json",
    )
    assert mismatching.returncode == 1
    payload = json.loads(mismatching.stdout)
    assert payload["status"] == "fail"
    assert "Plan hash mismatch" in payload["diagnostics"][0]["message"]
    assert payload["metrics"]["expected_plan_hash"] == "deadbeef"
    assert payload["metrics"]["actual_plan_hash"] == h


def test_build_plan_only_never_writes_for_full_functional_design(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """The same read-only-by-default guarantee test_build_cli_group.py
    already proves for passthrough_demo, re-checked against this plugin's
    own real multi-CDC design for the first time."""
    before = _tree_snapshot(tmp_path)
    result = _run_build(
        capsys, str(FULL_FUNCTIONAL_DESIGN), "--contracts-from", str(MODULES_YML),
        "--output", str(tmp_path / "algo_top.v"),
    )
    after = _tree_snapshot(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert before == after


# ── forge inspect: provenance / explain-staleness workflow ─────────────


def test_inspect_provenance_and_explain_staleness_round_trip_for_full_functional_design(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    prov_path = tmp_path / "provenance.json"

    write_result = _run_inspect(
        capsys, str(FULL_FUNCTIONAL_DESIGN), "--contracts-from", str(MODULES_YML),
        "--provenance", str(prov_path),
    )
    assert write_result.returncode == 0, write_result.stdout + write_result.stderr
    assert prov_path.exists()
    manifest = json.loads(prov_path.read_text())
    assert manifest["ir_content_hash"]
    assert "design_full_functional.yml" in manifest["source_hashes"]

    fresh = _run_inspect(
        capsys, str(FULL_FUNCTIONAL_DESIGN), "--contracts-from", str(MODULES_YML),
        "--explain-staleness", str(prov_path), "--json",
    )
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr
    fresh_staleness = json.loads(fresh.stdout)["metrics"]["explain_staleness"]
    assert fresh_staleness["stale"] is False
    assert fresh_staleness["reasons"] == []

    stale = _run_inspect(
        capsys, str(FULL_FUNCTIONAL_DESIGN), "--contracts-from", str(MODULES_YML),
        "--build-dir", str(tmp_path / "some_build_dir"),
        "--explain-staleness", str(prov_path), "--json",
    )
    assert stale.returncode == 1
    stale_staleness = json.loads(stale.stdout)["metrics"]["explain_staleness"]
    assert stale_staleness["stale"] is True
    assert any("command options changed" in r for r in stale_staleness["reasons"])


# ── forge inspect: visual explorer (--dot/--explorer), first application
#    to this plugin (preflight.md §11's 10.7C sub-slice) ────────────────


def test_inspect_dot_for_full_functional_design_carries_real_latency_and_cdc_labels(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """Real content, not just exit 0: `winbld`'s design-level `latency:`
    override (18 cycles at FRAME_WIDTH=16, 10.7A's own previously-
    unexercised per-instance override mechanism — see preflight.md's
    10.7A scope note) must actually render, and both real async_fifo
    crossings (prpack->pktz, tspack->pktz — each also a reset-domain
    crossing, pixel->output) must carry their CDC kind and reset-crossing
    labels through to the rendered DOT."""
    dot_path = tmp_path / "topo.dot"
    result = _run_inspect(
        capsys, str(FULL_FUNCTIONAL_DESIGN), "--contracts-from", str(MODULES_YML),
        "--dot", str(dot_path),
    )
    assert result.returncode == 0, result.stdout + result.stderr

    dot_text = dot_path.read_text()
    winbld_line = next(
        line for line in dot_text.splitlines() if line.strip().startswith('"winbld" [')
    )
    assert "latency: 18c" in winbld_line
    assert "clk: ap_clk" in winbld_line

    for src, dst in (("prpack", "pktz"), ("tspack", "pktz")):
        edge_line = next(
            line for line in dot_text.splitlines()
            if line.strip().startswith(f'"{src}" -> "{dst}"')
        )
        assert "async_fifo" in edge_line
        assert "CDC" in edge_line
        assert "reset-domain-crossing" in edge_line

    # Module-definition clustering (release-plan §8.1) groups by this
    # design's own instance names, not the modules.yml registry ref.
    assert "label=<module: tstats" in dot_text
    assert "label=<module: winbld" in dot_text


def test_inspect_dot_for_platform_wrapper_design_carries_real_three_domain_topology(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """The real 3-domain platform fixture (preflight.md §11's 10.7B
    sub-slice, applied through the visual explorer for the first time
    here): a real mailbox_transfer crossing (ctrl_mailbox -> threshcfg,
    control -> pixel) and the two async_fifo crossings into `pktz`
    (pixel -> output) must all render with their real clock domains and
    CDC kinds — not just the pixel-domain-only topology 10.7A already
    covered above."""
    dot_path = tmp_path / "topo.dot"
    result = _run_inspect(
        capsys, str(PLATFORM_WRAPPER_DESIGN), "--contracts-from", str(MODULES_YML),
        "--dot", str(dot_path),
    )
    assert result.returncode == 0, result.stdout + result.stderr

    dot_text = dot_path.read_text()
    ctrl_mailbox_line = next(
        line for line in dot_text.splitlines() if line.strip().startswith('"ctrl_mailbox" [')
    )
    assert "clk: clk_control" in ctrl_mailbox_line
    assert "latency: 1c" in ctrl_mailbox_line

    pktz_line = next(line for line in dot_text.splitlines() if line.strip().startswith('"pktz" ['))
    assert "clk: clk_output" in pktz_line
    assert "latency: 3c" in pktz_line

    mailbox_edge = next(
        line for line in dot_text.splitlines()
        if line.strip().startswith('"ctrl_mailbox" -> "threshcfg"')
    )
    assert "mailbox_transfer" in mailbox_edge
    assert "CDC" in mailbox_edge


def test_inspect_explorer_overlays_absent_without_verify_design_for_platform_wrapper_design(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """Regression guard mirroring test_inspect_explorer_overlays_absent_
    without_the_new_flags (forge/tests/test_inspect_cli_group.py), now
    checked for this plugin: --explorer without --verify-design/
    --results-json must carry no verification-flow overlay content."""
    explorer_path = tmp_path / "explorer.html"
    result = _run_inspect(
        capsys, str(PLATFORM_WRAPPER_DESIGN), "--contracts-from", str(MODULES_YML),
        "--explorer", str(explorer_path),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    explorer_html = explorer_path.read_text()
    assert '"verification_flow_entry_points":["pixel_normalizer_csim"]' not in explorer_html


def test_inspect_explorer_for_platform_wrapper_design_joins_real_verification_flow(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """The verification-flow-entry-point overlay (release-plan Phase 10,
    slice 10.0D) applied to this plugin's own design.verification.yml for
    the first time. Every `kind: full_chip_rtl` flow in this plugin
    declares `top_module: algo_top` (the generated wrapper itself, not a
    module ref), which never resolves to a real module — an honest empty
    join, not a bug (see forge.analyze.design_explorer.verification_join's
    own docstring). `pixel_normalizer_csim` (`kind: hls_csim`,
    `top_module: pixel_normalizer`) is this plugin's one flow whose entry
    point genuinely matches a real module's ip_info_key (`norm`'s own
    `ref: pixel_normalizer`) — real evidence the join logic works for a
    design this plugin actually ships, not a synthetic fixture."""
    results_json = tmp_path / "results.json"
    results_json.write_text(json.dumps({"flow_name": "pixel_normalizer_csim", "backend_id": "csim"}))
    explorer_path = tmp_path / "explorer.html"

    result = _run_inspect(
        capsys, str(PLATFORM_WRAPPER_DESIGN), "--contracts-from", str(MODULES_YML),
        "--verify-design", str(VERIFY_DESIGN), "--results-json", str(results_json),
        "--explorer", str(explorer_path),
    )
    assert result.returncode == 0, result.stdout + result.stderr

    explorer_html = explorer_path.read_text()
    assert '"verification_flow_entry_points":["pixel_normalizer_csim"]' in explorer_html
