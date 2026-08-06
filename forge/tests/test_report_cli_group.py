"""Real end-to-end coverage for `forge report` (release-plan Phase 6,
§6.5) — the orchestrator that bundles maturity/latency/verification/
provenance sections plus a dashboard into one output directory.

Runs against both real reference plugins directly (no gen-top needed for
the base report — maturity/latency-check only need design.yml). A
separate xsim-gated test proves the `--provenance`/`--junit-xml` reuse
paths against real artifacts from `forge inspect --provenance` and
`forge test run --junit-xml`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import NamedTuple

import pytest

from forge.core.cli.main import build_parser

DOT_AVAILABLE = shutil.which("dot") is not None

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSTHROUGH_DESIGN = REPO_ROOT / "plugins/passthrough_demo/forge/designs/design.yml"
PASSTHROUGH_MODULES = REPO_ROOT / "plugins/passthrough_demo/forge/modules.yml"
TRIGGER_DESIGN = REPO_ROOT / "plugins/trigger_demo/forge/designs/design.yml"
TRIGGER_MODULES = REPO_ROOT / "plugins/trigger_demo/forge/modules.yml"
VPD_DESIGN = REPO_ROOT / "plugins/vision_pipeline_demo/forge/designs/design.yml"
VPD_MODULES = REPO_ROOT / "plugins/vision_pipeline_demo/forge/modules.yml"
VPD_DESIGN_VERIFICATION = REPO_ROOT / "plugins/vision_pipeline_demo/forge/verify/design.verification.yml"
VPD_HLS_BUILD_ROOT = REPO_ROOT / "build_hls_vision_pipeline_demo"
VPD_FULL_FUNCTIONAL_DESIGN = REPO_ROOT / "plugins/vision_pipeline_demo/forge/designs/design_full_functional.yml"
VPD_FULL_FUNCTIONAL_PROBE_CSV = (
    REPO_ROOT / "plugins/vision_pipeline_demo/forge/verify/full_functional_xsim/xsim_work/algo_top_probe.csv"
)

XSIM_AVAILABLE = all(shutil.which(tool) for tool in ("xvlog", "xelab", "xsim"))
skip_without_xsim = pytest.mark.skipif(
    not XSIM_AVAILABLE, reason="Vivado xsim toolchain (xvlog/xelab/xsim) not on PATH"
)


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


_CLAIMED_ARTIFACTS = (
    "maturity.md", "latency_check.md", "verification_results.md",
    "dashboard.html", "summary.md", "topology.dot", "topology_explorer.html",
)


def test_report_bundle_on_passthrough_demo(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """passthrough_demo has no HLS build artifacts — the honest-absence
    path for the HLS summary section exercises here."""
    output_dir = tmp_path / "report"

    result = _run(
        capsys, "report", str(PASSTHROUGH_DESIGN),
        "--contracts-from", str(PASSTHROUGH_MODULES),
        "--output", str(output_dir), "--json",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] in ("pass", "warn")
    for name in _CLAIMED_ARTIFACTS:
        path = output_dir / name
        assert str(path) in payload["artifacts"]
        assert path.exists() and path.stat().st_size > 0, f"{name} missing or empty"

    assert any("hls" in d["message"].lower() for d in payload["diagnostics"])

    # topology.dot never requires `dot`; topology.svg degrades honestly
    # (present when `dot` is on PATH, a real next_action note otherwise —
    # never silently omitted either way).
    dashboard_html = (output_dir / "dashboard.html").read_text()
    if DOT_AVAILABLE:
        assert str(output_dir / "topology.svg") in payload["artifacts"]
        assert (output_dir / "topology.svg").stat().st_size > 0
        # dashboard.html embeds the SVG and links the interactive explorer
        # rather than leaving them as files a reader has to know to browse to.
        assert "data:image/svg+xml;base64," in dashboard_html
        assert 'href="topology_explorer.html"' in dashboard_html
    else:
        assert any("dot" in a.lower() for a in payload["next_actions"])
        assert any("topology.svg" in d["message"] for d in payload["diagnostics"])
        assert 'href="topology_explorer.html"' in dashboard_html

    maturity_md = (output_dir / "maturity.md").read_text()
    assert "Contract maturity" in maturity_md
    assert "contract-driven" in maturity_md


def test_report_bundle_on_trigger_demo(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    output_dir = tmp_path / "report"

    result = _run(
        capsys, "report", str(TRIGGER_DESIGN),
        "--contracts-from", str(TRIGGER_MODULES),
        "--output", str(output_dir), "--json",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    for name in _CLAIMED_ARTIFACTS:
        assert (output_dir / name).exists()
    assert payload["metrics"]["maturity"]["modules"]["total"] == 7


def test_report_missing_design_is_guided(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    result = _run(
        capsys, "report", str(tmp_path / "nope.yml"), "--output", str(tmp_path / "report"),
    )
    assert result.returncode == 1
    assert not (tmp_path / "report" / "maturity.md").exists()


def test_report_registered_as_top_level_command() -> None:
    parser = build_parser()
    parsed = parser.parse_args(["report", str(PASSTHROUGH_DESIGN)])
    assert parsed.func.__name__ == "cmd_report"


@skip_without_xsim
def test_report_reuses_real_provenance_and_junit_artifacts(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """End-to-end: gen-top a real scratch copy, run `forge test run
    --junit-xml`, write a provenance manifest via `forge inspect
    --provenance`, then confirm `forge report` reuses both (not
    recomputing verification results, purely presenting the provenance
    manifest's already-computed data)."""
    dest_plugin = tmp_path / "plugins" / "passthrough_demo"
    shutil.copytree(
        REPO_ROOT / "plugins/passthrough_demo", dest_plugin,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    design_yml = dest_plugin / "forge/designs/design.yml"
    modules_yml = dest_plugin / "forge/modules.yml"
    design_verification_yml = dest_plugin / "forge/verify/design.verification.yml"

    gen_top_result = _run(
        capsys, "topgen", "gen-top", str(design_yml),
        "--mode", "verilog", "--consumer-root", str(tmp_path),
        "--contracts-from", str(modules_yml),
        "--output", str(tmp_path / "gen-top/design_passthrough_demo/algo_top.v"),
        "--build-dir", str(tmp_path / "build/passthrough_demo"),
    )
    assert gen_top_result.returncode == 0, gen_top_result.stdout + gen_top_result.stderr

    junit_path = tmp_path / "junit.xml"
    test_run_result = _run(
        capsys, "test", "run", str(design_verification_yml),
        "--flow", "passthrough_xsim", "--plugin", "passthrough_demo",
        "--consumer-root", str(tmp_path), "--event-id", "0",
        "--junit-xml", str(junit_path),
    )
    assert test_run_result.returncode == 0, test_run_result.stdout + test_run_result.stderr
    assert junit_path.exists()

    provenance_path = tmp_path / "provenance.json"
    inspect_result = _run(
        capsys, "inspect", str(design_yml), "--contracts-from", str(modules_yml),
        "--provenance", str(provenance_path),
    )
    assert inspect_result.returncode == 0, inspect_result.stdout + inspect_result.stderr

    output_dir = tmp_path / "report"
    report_result = _run(
        capsys, "report", str(design_yml), "--contracts-from", str(modules_yml),
        "--output", str(output_dir),
        "--provenance", str(provenance_path), "--junit-xml", str(junit_path),
        "--json",
    )

    assert report_result.returncode == 0, report_result.stdout + report_result.stderr
    payload = json.loads(report_result.stdout)
    assert str(output_dir / "provenance.md") in payload["artifacts"]
    assert not any("no --provenance" in d["message"] for d in payload["diagnostics"])
    assert not any("No verification results yet" in a for a in payload["next_actions"])

    verification_md = (output_dir / "verification_results.md").read_text()
    assert "event_0" in verification_md
    assert "PASS" in verification_md

    provenance_md = (output_dir / "provenance.md").read_text()
    assert "IR content hash" in provenance_md


@skip_without_xsim
def test_report_prefers_results_json_over_junit_xml(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """Phase 7 slice 7.2: `forge report --results-json` reuses the richer
    versioned FlowResult (backend id, real duration, waveform path) rather
    than JUnit — and takes precedence when both are given, since it's a
    strict superset of what JUnit's schema can express."""
    dest_plugin = tmp_path / "plugins" / "passthrough_demo"
    shutil.copytree(
        REPO_ROOT / "plugins/passthrough_demo", dest_plugin,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    design_yml = dest_plugin / "forge/designs/design.yml"
    modules_yml = dest_plugin / "forge/modules.yml"
    design_verification_yml = dest_plugin / "forge/verify/design.verification.yml"

    gen_top_result = _run(
        capsys, "topgen", "gen-top", str(design_yml),
        "--mode", "verilog", "--consumer-root", str(tmp_path),
        "--contracts-from", str(modules_yml),
        "--output", str(tmp_path / "gen-top/design_passthrough_demo/algo_top.v"),
        "--build-dir", str(tmp_path / "build/passthrough_demo"),
    )
    assert gen_top_result.returncode == 0, gen_top_result.stdout + gen_top_result.stderr

    junit_path = tmp_path / "junit.xml"
    results_json_path = tmp_path / "results.json"
    test_run_result = _run(
        capsys, "test", "run", str(design_verification_yml),
        "--flow", "passthrough_xsim", "--plugin", "passthrough_demo",
        "--consumer-root", str(tmp_path), "--event-id", "0",
        "--junit-xml", str(junit_path), "--results-json", str(results_json_path),
    )
    assert test_run_result.returncode == 0, test_run_result.stdout + test_run_result.stderr
    assert results_json_path.exists()

    output_dir = tmp_path / "report"
    report_result = _run(
        capsys, "report", str(design_yml), "--contracts-from", str(modules_yml),
        "--output", str(output_dir),
        "--junit-xml", str(junit_path), "--results-json", str(results_json_path),
        "--json",
    )

    assert report_result.returncode == 0, report_result.stdout + report_result.stderr
    verification_md = (output_dir / "verification_results.md").read_text()
    # Only render_results_markdown (not JUnit's renderer) emits a
    # **backend** line — its presence proves --results-json was the one
    # actually consumed, not just present alongside --junit-xml.
    assert "**backend**: xsim" in verification_md
    assert "PASS" in verification_md


@skip_without_xsim
@pytest.mark.skipif(not VPD_HLS_BUILD_ROOT.exists(), reason="no cached vision_pipeline_demo HLS build")
def test_report_throughput_cdc_and_golden_comparison_sections(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """release-plan Phase 10, slice 10.0C: end-to-end against
    vision_pipeline_demo's already-validated quickstart flow (reusing its
    cached HLS build, no fresh synthesis) — forge topgen validate
    --cdc-result-json, forge test run --golden-comparison-json, and
    forge report's three new sections, all together."""
    cdc_result_path = tmp_path / "cdc_result.json"
    validate_result = _run(
        capsys, "topgen", "validate", str(VPD_DESIGN), "--cdc-result-json", str(cdc_result_path),
    )
    assert validate_result.returncode == 0, validate_result.stdout + validate_result.stderr
    assert cdc_result_path.exists()

    golden_comparison_path = tmp_path / "golden_comparison.json"
    test_run_result = _run(
        capsys, "test", "run", str(VPD_DESIGN_VERIFICATION),
        "--flow", "quickstart_pipeline_xsim", "--plugin", "vision_pipeline_demo",
        "--consumer-root", str(REPO_ROOT), "--event-id", "0",
        "--golden-comparison-json", str(golden_comparison_path),
    )
    assert test_run_result.returncode == 0, test_run_result.stdout + test_run_result.stderr
    assert golden_comparison_path.exists()

    output_dir = tmp_path / "report"
    report_result = _run(
        capsys, "report", str(VPD_DESIGN),
        "--contracts-from", str(VPD_MODULES),
        "--hls-build-root", str(VPD_HLS_BUILD_ROOT),
        "--module-width", "pixel_normalizer:8",
        "--cdc-result-json", str(cdc_result_path),
        "--golden-comparison-json", str(golden_comparison_path),
        "--output", str(output_dir), "--json",
    )

    assert report_result.returncode == 0, report_result.stdout + report_result.stderr
    payload = json.loads(report_result.stdout)
    assert str(output_dir / "throughput.md") in payload["artifacts"]
    assert str(output_dir / "cdc_verification.md") in payload["artifacts"]
    assert str(output_dir / "golden_comparison.md") in payload["artifacts"]
    assert payload["metrics"]["throughput_analyses"] == 1
    assert payload["metrics"]["cdc_crossings"] == 0
    assert payload["metrics"]["golden_comparison_events"] == 1

    throughput_md = (output_dir / "throughput.md").read_text()
    assert "pixel_normalizer" in throughput_md
    assert "bottleneck" in throughput_md

    cdc_md = (output_dir / "cdc_verification.md").read_text()
    assert "No clock/reset-domain crossings" in cdc_md

    golden_md = (output_dir / "golden_comparison.md").read_text()
    assert "vision_pipeline.quickstart_normalizer_threshold" in golden_md
    assert "1/1 passed" in golden_md


@skip_without_xsim
@pytest.mark.skipif(not VPD_HLS_BUILD_ROOT.exists(), reason="no cached vision_pipeline_demo HLS build")
@pytest.mark.skipif(
    not VPD_FULL_FUNCTIONAL_PROBE_CSV.exists(),
    reason="no cached full_functional_xsim probe CSV (run `forge verify run "
    ".../full_functional_xsim/verify.flow.yml --probe-log` first)",
)
def test_report_release_acceptance_for_full_functional_design(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """release-plan Phase 10, slice 10.7A: `forge report`'s release-
    acceptance bundle (preflight.md §13) against the real full-functional
    design (16x16/2x2-tile, single shared normalizer, both CDC
    crossings) -- real CDC verification (2 real async_fifo crossings)
    plus real runtime throughput (real probe CSV from a prior
    ``forge verify run --probe-log`` against full_functional_xsim).

    Deliberately does NOT pass --golden-comparison-json: this design's
    own real content-checked, dual-clock-domain scoreboard (see
    gen_stimulus_full_functional.py) uses a hand-authored SystemVerilog
    fork/join checker, not the standard per-event XML in/out comparison
    ``forge test run --golden-comparison-json`` produces -- driving that
    standard path against this flow overwrites its own custom
    stimulus_current.svh with a generic single-event driver incompatible
    with this design's port structure (found running this exact
    combination; same scope boundary packetizer_xsim's own real,
    passing checker already established in slice 10.5 for a
    structurally identical reason -- see
    test_report_omits_new_sections_honestly_without_their_flags for the
    honest-absence path this exercises for a real (not synthetic) design).
    """
    cdc_result_path = tmp_path / "cdc_result.json"
    validate_result = _run(
        capsys, "topgen", "validate", str(VPD_FULL_FUNCTIONAL_DESIGN),
        "--cdc-result-json", str(cdc_result_path),
    )
    assert validate_result.returncode == 0, validate_result.stdout + validate_result.stderr
    assert cdc_result_path.exists()

    output_dir = tmp_path / "report"
    report_result = _run(
        capsys, "report", str(VPD_FULL_FUNCTIONAL_DESIGN),
        "--contracts-from", str(VPD_MODULES),
        "--hls-build-root", str(VPD_HLS_BUILD_ROOT),
        "--module-width", "pixel_normalizer:8",
        "--module-width", "sobel_hls:12",
        "--module-width", "tile_stats_hls:24",
        "--probe-csv", str(VPD_FULL_FUNCTIONAL_PROBE_CSV),
        "--probe-format", "wide",
        "--fifo-probe", "prpack->pktz:pr_full:pr_empty:pr_occupancy:pr_overflow",
        "--fifo-probe", "tspack->pktz:ts_full:ts_empty:ts_occupancy:ts_overflow",
        "--cdc-result-json", str(cdc_result_path),
        "--output", str(output_dir), "--json",
    )

    assert report_result.returncode == 0, report_result.stdout + report_result.stderr
    payload = json.loads(report_result.stdout)
    assert str(output_dir / "throughput.md") in payload["artifacts"]
    assert str(output_dir / "cdc_verification.md") in payload["artifacts"]
    assert payload["metrics"]["cdc_crossings"] == 2

    cdc_md = (output_dir / "cdc_verification.md").read_text()
    assert "prpack->pktz" in cdc_md
    assert "tspack->pktz" in cdc_md
    assert "crossings checked**: 2 (0 failing)" in cdc_md

    throughput_md = (output_dir / "throughput.md").read_text()
    assert "tile_stats_hls" in throughput_md
    assert "prpack->pktz" in throughput_md
    assert "tspack->pktz" in throughput_md

    # Golden-comparison section honestly absent (see docstring).
    assert not (output_dir / "golden_comparison.md").exists()
    assert any(
        "golden-model comparison report omitted" in d["message"] for d in payload["diagnostics"]
    )


def test_report_omits_new_sections_honestly_without_their_flags(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    output_dir = tmp_path / "report"
    result = _run(
        capsys, "report", str(PASSTHROUGH_DESIGN),
        "--contracts-from", str(PASSTHROUGH_MODULES),
        "--output", str(output_dir), "--json",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert not (output_dir / "throughput.md").exists()
    assert not (output_dir / "cdc_verification.md").exists()
    assert not (output_dir / "golden_comparison.md").exists()
    assert any("throughput report omitted" in d["message"] for d in payload["diagnostics"])
    assert any("CDC verification" in d["message"] for d in payload["diagnostics"])
    assert any("golden-model comparison report omitted" in d["message"] for d in payload["diagnostics"])


def test_report_topology_overlays_are_absent_without_the_new_flags(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """Regression guard (release-plan Phase 10, slice 10.0D): topology
    rendering without --verify-design/--results-json must be unaffected —
    build_design_graph's own `or {}` overlay defaults already guarantee
    this, this confirms it holds through the new call sites too."""
    output_dir = tmp_path / "report"
    result = _run(
        capsys, "report", str(TRIGGER_DESIGN),
        "--contracts-from", str(TRIGGER_MODULES),
        "--output", str(output_dir), "--json",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    explorer_html = (output_dir / "topology_explorer.html").read_text()
    assert '"verification_flow_entry_points":["hit_decoder_xsim"]' not in explorer_html


def test_report_topology_carries_real_latency_and_verification_overlays(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """release-plan Phase 10, slice 10.0D: forge report --verify-design
    --results-json must make the topology explorer/DOT carry real
    latency values (trigger_logic's real declared `latency: {kind:
    fixed, cycles: 3}`) and a real verification-flow-entry-point join
    (hit_decoder_xsim's declared entry point, module 'dec') — not just
    exit 0. results.json is a minimal, directly-constructed
    {flow_name, backend_id} payload, the same convention
    test_design_explorer_overlays.py's own join_flow_entry_points tests
    already use — the join only reads flow_name, no real simulation run
    is needed to exercise it for real."""
    results_json = tmp_path / "results.json"
    results_json.write_text(json.dumps({"flow_name": "hit_decoder_xsim", "backend_id": "xsim"}))

    output_dir = tmp_path / "report"
    result = _run(
        capsys, "report", str(TRIGGER_DESIGN),
        "--contracts-from", str(TRIGGER_MODULES),
        "--verify-design", str(TRIGGER_DESIGN.parent.parent / "verify/design.verification.yml"),
        "--results-json", str(results_json),
        "--output", str(output_dir), "--json",
    )
    assert result.returncode == 0, result.stdout + result.stderr

    dot_text = (output_dir / "topology.dot").read_text()
    assert 'label="trig clk: ap_clk maturity: mixed latency: 3c"' in dot_text

    explorer_html = (output_dir / "topology_explorer.html").read_text()
    assert '"verification_flow_entry_points":["hit_decoder_xsim"]' in explorer_html
