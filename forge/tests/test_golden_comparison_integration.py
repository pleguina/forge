"""Real end-to-end integration check for release-plan Phase 10, slice
10.0C's forge.golden_comparison_result.v1 artifact: drives a real
`forge test run --golden-comparison-json` against vision_pipeline_demo's
already-validated quickstart xsim flow (reusing the cached HLS build and
generated algo_top.v from slice 10.1 — no fresh HLS synthesis).

Unit-level coverage of build_golden_comparison_result's join logic lives
in forge/tests/verify/test_golden_comparison_result.py; this file is the
one place that proves the whole real pipeline (gen_stimulus.py's sidecar
write -> forge test run -> the new CLI flag) actually works together.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from forge.core.cli.main import build_parser

REPO_ROOT = Path(__file__).resolve().parents[2]
VPD_DESIGN_VERIFICATION = REPO_ROOT / "plugins/vision_pipeline_demo/forge/verify/design.verification.yml"
VPD_HLS_BUILD_ROOT = REPO_ROOT / "build_hls_vision_pipeline_demo"
VPD_ALGO_TOP = REPO_ROOT / "gen-top/design_vision_pipeline_quickstart/algo_top.v"


def _run_test(capsys: pytest.CaptureFixture[str], *args: str):
    parser = build_parser()
    code = 0
    try:
        parsed = parser.parse_args(["test", *args])
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.skipif(not shutil.which("xsim"), reason="Vivado xsim not on PATH")
@pytest.mark.skipif(not VPD_HLS_BUILD_ROOT.exists(), reason="no cached vision_pipeline_demo HLS build")
@pytest.mark.skipif(not VPD_ALGO_TOP.exists(), reason="no generated vision_pipeline_demo algo_top.v")
def test_forge_test_run_writes_real_golden_comparison_artifact(tmp_path, capsys):
    results_json = tmp_path / "results.json"
    golden_comparison_json = tmp_path / "golden_comparison.json"

    code, out, err = _run_test(
        capsys, "run", str(VPD_DESIGN_VERIFICATION),
        "--flow", "quickstart_pipeline_xsim", "--plugin", "vision_pipeline_demo",
        "--consumer-root", str(REPO_ROOT), "--event-id", "0",
        "--results-json", str(results_json),
        "--golden-comparison-json", str(golden_comparison_json),
    )

    assert code == 0, f"forge test run failed:\n{out}\n{err}"
    assert golden_comparison_json.exists()

    payload = json.loads(golden_comparison_json.read_text())
    assert payload["schema"] == {"name": "forge.golden_comparison_result", "version": "1.0"}
    assert payload["provider_id"] == "vision_pipeline.quickstart_normalizer_threshold"
    assert len(payload["events"]) == 1
    assert payload["events"][0]["passed"] is True
    assert len(payload["events"][0]["checks"]) == 3


def test_forge_test_run_omits_golden_comparison_artifact_without_a_sidecar(tmp_path, capsys):
    """passthrough_demo's golden XML is still hand-typed — no real
    GoldenModelProvider, so gen_stimulus.py never writes a
    golden_model_provenance.json sidecar. --golden-comparison-json must
    be an honest no-op here, not an error."""
    design_verification = REPO_ROOT / "plugins/passthrough_demo/forge/verify/design.verification.yml"
    results_json = tmp_path / "results.json"
    golden_comparison_json = tmp_path / "golden_comparison.json"

    code, out, err = _run_test(
        capsys, "run", str(design_verification),
        "--flow", "passthrough_xsim", "--plugin", "passthrough_demo",
        "--consumer-root", str(REPO_ROOT), "--event-id", "0",
        "--results-json", str(results_json),
        "--golden-comparison-json", str(golden_comparison_json),
    )

    assert code == 0, f"forge test run failed:\n{out}\n{err}"
    assert not golden_comparison_json.exists()
