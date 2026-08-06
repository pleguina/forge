"""Cross-stage integration acceptance test.

Not a new feature: a single, real, end-to-end chain proving every stage's
output is genuinely consumable by the next, since each stage's own unit
tests only prove that stage in isolation:

    JSON dataset file
      -> format loader (real content-hash verification)
      -> project adapter (passthrough.identity-xml)
      -> CanonicalDataset
      -> stable stimulus memory (one real compile)
      -> multiple real event runs, selected by event_id -> event_index
      -> machine-readable FORGE_CHECK records (pass and fail cases)
      -> versioned results.json (schema-checked)
      -> JUnit XML (time populated)
      -> forge report's verification_results.md

Run against real passthrough_demo, on real xsim, with the real Verilator
backend as a second real run of the same chain (proving the whole chain
is backend-agnostic, not incidentally xsim-only).
"""

from __future__ import annotations

import json
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from forge.core.cli.main import build_parser

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_SRC = REPO_ROOT / "plugins/passthrough_demo"

XSIM_AVAILABLE = all(shutil.which(tool) for tool in ("xvlog", "xelab", "xsim"))
VERILATOR_AVAILABLE = shutil.which("verilator") is not None
skip_without_xsim = pytest.mark.skipif(not XSIM_AVAILABLE, reason="xsim not on PATH")
skip_without_verilator = pytest.mark.skipif(not VERILATOR_AVAILABLE, reason="verilator not on PATH")


class Result:
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> Result:
    parser = build_parser()
    code = 0
    try:
        parsed = parser.parse_args(list(args))
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return Result(code, captured.out, captured.err)


@pytest.fixture
def consumer_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    """A real, isolated, gen-top'd + generated copy of passthrough_demo —
    all four real flows (xsim, verilator, readmemh, readmemh_verilator)."""
    dest_plugin = tmp_path / "plugins" / "passthrough_demo"
    shutil.copytree(PLUGIN_SRC, dest_plugin, ignore=shutil.ignore_patterns("__pycache__"))

    gen_top = _run(
        capsys, "topgen", "gen-top",
        str(dest_plugin / "forge/designs/design.yml"),
        "--mode", "verilog",
        "--consumer-root", str(tmp_path),
        "--output", str(tmp_path / "gen-top/design_passthrough_demo/algo_top.v"),
        "--build-dir", str(tmp_path / "build/passthrough_demo"),
    )
    assert gen_top.returncode == 0, gen_top.stdout + gen_top.stderr

    design_yml = dest_plugin / "forge/verify/design.verification.yml"
    generated = _run(capsys, "verify", "generate", str(design_yml), "--consumer-root", str(tmp_path))
    assert generated.returncode == 0, generated.stdout + generated.stderr

    return tmp_path


def _break_golden_json_sibling(consumer_root: Path) -> Path:
    """A real, deliberately-broken JSON dataset sibling — one event's
    golden value changed, its declared content_hash recomputed to match
    (so this exercises the *DUT-vs-golden mismatch* failure path, not the
    unrelated hash-tamper-detection path already covered elsewhere)."""
    from forge.verify.dataset_format import compute_events_content_hash

    golden = (
        consumer_root
        / "plugins/passthrough_demo/forge/verify/schemas/data/passthrough_demo_golden.json"
    )
    payload = json.loads(golden.read_text())
    payload["events"][0]["golden"]["data_out"] = "0x00"  # real DUT still produces 0x3A
    payload["semantic"]["source_content_hash"] = compute_events_content_hash(payload["events"])
    tampered = golden.parent / "passthrough_demo_golden_tampered.json"
    tampered.write_text(json.dumps(payload))
    return tampered


@skip_without_xsim
def test_full_chain_json_dataset_through_report_on_xsim(
    capsys: pytest.CaptureFixture[str], consumer_root: Path,
) -> None:
    """The full chain, real, on xsim: JSON dataset -> format loader
    (content-hash verified) -> passthrough.identity-xml adapter ->
    CanonicalDataset -> readmemh stimulus memory (one real compile) ->
    two real event runs by event_id -> event_index -> real FORGE_CHECK
    records -> versioned results.json -> JUnit (time populated) ->
    forge report's verification_results.md."""
    flow_dir = consumer_root / "plugins/passthrough_demo/forge/verify/passthrough_readmemh"
    golden_json = (
        consumer_root
        / "plugins/passthrough_demo/forge/verify/schemas/data/passthrough_demo_golden.json"
    )
    assert golden_json.exists()

    # ── Layer A: format loader, real content-hash verification ─────────
    from forge.verify.dataset_format import JsonDatasetLoader

    serialized = JsonDatasetLoader().load(golden_json)  # raises on hash mismatch — doesn't here
    assert serialized.metadata.event_ids == ["0", "1"]

    # ── Layer B: the real project adapter, explicit id, genuine no-op ──
    tools_dir = consumer_root / "plugins/passthrough_demo/forge/verify/tools"
    sys.path.insert(0, str(tools_dir))
    try:
        sys.modules.pop("bootstrap", None)
        sys.modules.pop("dataset_adapter", None)
        import bootstrap  # noqa: PLC0415
        bootstrap.bootstrap()

        from forge.verify.dataset_adapter import DatasetSource, get_dataset_adapter
        adapter = get_dataset_adapter("passthrough.identity-xml")
        canonical = adapter.materialize(DatasetSource(serialized=serialized), {})
        assert canonical.events == serialized.events

        # ── Write the readmemh stimulus from the JSON-sourced
        # CanonicalDataset for real (not the default XML path) ──────────
        sys.modules.pop("gen_stimulus", None)
        import gen_stimulus  # noqa: PLC0415
        index_to_id = gen_stimulus.generate_readmemh_stimulus(
            "passthrough_readmemh", flow_dir, dataset_path=golden_json,
        )
        assert index_to_id == {0: "0", 1: "1"}
    finally:
        sys.path.remove(str(tools_dir))
        for name in ("bootstrap", "gen_stimulus", "dataset_adapter"):
            sys.modules.pop(name, None)

    # ── Real run through `forge test run`: compile once, both events ───
    results_path = consumer_root / "results.json"
    junit_path = consumer_root / "junit.xml"
    design_yml = consumer_root / "plugins/passthrough_demo/forge/verify/design.verification.yml"

    run_result = _run(
        capsys, "test", "run", str(design_yml),
        "--flow", "passthrough_readmemh", "--plugin", "passthrough_demo",
        "--consumer-root", str(consumer_root), "--all-events",
        "--results-json", str(results_path), "--junit-xml", str(junit_path),
    )
    assert run_result.returncode == 0, run_result.stdout + run_result.stderr
    assert run_result.stdout.count("[xsim 1/3] Compiling") == 1
    assert run_result.stdout.count("[xsim 1-2/3] Reusing existing compile+elaborate") == 1

    # ── Versioned, schema-checked results.json ──────────────
    flow_result = json.loads(results_path.read_text())
    assert flow_result["schema"] == {"name": "forge.verification_results", "version": "1.0"}
    assert flow_result["backend_id"] == "xsim"
    assert flow_result["success"] is True
    events = sorted(flow_result["events"], key=lambda e: e["event_id"])
    assert [e["event_id"] for e in events] == ["0", "1"]
    assert [e["event_index"] for e in events] == [0, 1]

    # ── Real FORGE_CHECK records, both events, real values ──
    checks_0 = {c["label"]: c for c in events[0]["checks"]}
    checks_1 = {c["label"]: c for c in events[1]["checks"]}
    assert checks_0["data_out_check"]["expected"] == checks_0["data_out_check"]["observed"] == "0x3a"
    assert checks_1["data_out_check"]["expected"] == checks_1["data_out_check"]["observed"] == "0x00"
    assert all(c["passed"] for c in checks_0.values())
    assert all(c["passed"] for c in checks_1.values())

    # ── JUnit: time populated, real ─────────────────────────────────────
    testsuite = ET.parse(junit_path).getroot()
    testcases = testsuite.findall("testcase")
    assert len(testcases) == 2
    for tc in testcases:
        assert float(tc.get("time")) > 0

    # ── forge report reuses results.json, not recomputing anything ─────
    report_dir = consumer_root / "report"
    report_result = _run(
        capsys, "report",
        str(consumer_root / "plugins/passthrough_demo/forge/designs/design.yml"),
        "--contracts-from", str(consumer_root / "plugins/passthrough_demo/forge/modules.yml"),
        "--output", str(report_dir),
        "--results-json", str(results_path),
    )
    assert report_result.returncode == 0, report_result.stdout + report_result.stderr
    verification_md = (report_dir / "verification_results.md").read_text()
    assert "**backend**: xsim" in verification_md
    assert "PASS" in verification_md
    assert "| 0 | PASS" in verification_md
    assert "| 1 | PASS" in verification_md


@skip_without_xsim
def test_full_chain_fail_case_json_driven_readmemh_stimulus_on_xsim(
    capsys: pytest.CaptureFixture[str], consumer_root: Path,
) -> None:
    """The same chain's fail path, driven specifically through the JSON
    dataset (layer A) + identity adapter (layer B) + readmemh stimulus
    generation call, with one real golden value deliberately broken — a
    real mismatch, caught by the checker (which runs for flows without a
    declared `checker:` section) and reported with a genuinely mismatched
    FORGE_CHECK record, not a false pass.

    Uses `forge verify run` directly (not `forge test run`) so the
    JSON-driven stimulus this test just generated is the one actually
    simulated — `forge test run`'s readmemh wiring always regenerates
    from the plugin's own default dataset path first, which would
    otherwise silently revert this test's own tampering."""
    flow_dir = consumer_root / "plugins/passthrough_demo/forge/verify/passthrough_readmemh"
    tampered_json = _break_golden_json_sibling(consumer_root)

    tools_dir = consumer_root / "plugins/passthrough_demo/forge/verify/tools"
    sys.path.insert(0, str(tools_dir))
    try:
        sys.modules.pop("bootstrap", None)
        import bootstrap  # noqa: PLC0415
        bootstrap.bootstrap()

        sys.modules.pop("gen_stimulus", None)
        import gen_stimulus  # noqa: PLC0415
        gen_stimulus.generate_readmemh_stimulus(
            "passthrough_readmemh", flow_dir, dataset_path=tampered_json,
        )
    finally:
        sys.path.remove(str(tools_dir))
        for name in ("bootstrap", "gen_stimulus", "dataset_adapter"):
            sys.modules.pop(name, None)

    flow_yml = flow_dir / "verify.flow.yml"
    run_result = _run(
        capsys, "verify", "run", str(flow_yml),
        "--plugin", "passthrough_demo", "--consumer-root", str(consumer_root),
    )

    assert run_result.returncode == 1
    combined = run_result.stdout + run_result.stderr
    assert "Scoreboard check: FAIL" in combined

    simulate_log = (flow_dir / "xsim_work/simulate.log").read_text()
    assert (
        "FORGE_CHECK|check_id=data_out_check|label=data_out_check|signal=pt_data_out|"
        "expected=0x00|observed=0x3a|width=8|passed=0"
    ) in simulate_log


@skip_without_verilator
def test_full_chain_same_readmemh_flow_on_verilator(
    capsys: pytest.CaptureFixture[str], consumer_root: Path,
) -> None:
    """The same chain, on the real Verilator backend
    (passthrough_readmemh_verilator) — proves the whole pipeline is
    backend-agnostic, not incidentally xsim-only: real compile once, real
    distinct per-event results, real backend_id, real results.json."""
    design_yml = consumer_root / "plugins/passthrough_demo/forge/verify/design.verification.yml"
    results_path = consumer_root / "results_verilator.json"

    run_result = _run(
        capsys, "test", "run", str(design_yml),
        "--flow", "passthrough_readmemh_verilator", "--plugin", "passthrough_demo",
        "--consumer-root", str(consumer_root), "--all-events",
        "--results-json", str(results_path),
    )
    assert run_result.returncode == 0, run_result.stdout + run_result.stderr
    assert run_result.stdout.count("[verilator 1/2] Compiling+Elaborating") == 1
    assert run_result.stdout.count("[verilator 1/2] Reusing existing build") == 1

    flow_result = json.loads(results_path.read_text())
    assert flow_result["schema"] == {"name": "forge.verification_results", "version": "1.0"}
    assert flow_result["backend_id"] == "verilator"
    assert flow_result["success"] is True
    events = sorted(flow_result["events"], key=lambda e: e["event_id"])
    assert [e["event_id"] for e in events] == ["0", "1"]

    checks_0 = {c["label"]: c for c in events[0]["checks"]}
    checks_1 = {c["label"]: c for c in events[1]["checks"]}
    assert checks_0["data_out_check"]["observed"] == "0x3a"
    assert checks_1["data_out_check"]["observed"] == "0x00"
