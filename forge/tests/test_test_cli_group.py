"""Real end-to-end coverage for `forge test` (release-plan Phase 6, §6.4) —
the new check-only/prepare/run command group wrapping verification
preparation and execution.

Mirrors `tests/verify/test_verify_run_xsim.py`'s fixture pattern: copies
plugins/passthrough_demo into an isolated tmp_path "consumer root" (nothing
tracked in git is ever touched), gen-top's it for real, then drives actual
xvlog -> xelab -> xsim runs through `forge test run` in-process. Skipped
automatically when Vivado's xsim toolchain isn't on PATH.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import NamedTuple

import pytest

from forge.core.cli.main import build_parser

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_SRC = REPO_ROOT / "plugins/passthrough_demo"

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


@pytest.fixture
def consumer_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    """An isolated, gen-top'd copy of plugins/passthrough_demo rooted at
    tmp_path — same fixture recipe as
    tests/verify/test_verify_run_xsim.py's own `consumer_root`."""
    dest_plugin = tmp_path / "plugins" / "passthrough_demo"
    shutil.copytree(PLUGIN_SRC, dest_plugin, ignore=shutil.ignore_patterns("__pycache__"))

    result = _run(
        capsys, "topgen", "gen-top",
        str(dest_plugin / "forge/designs/design.yml"),
        "--mode", "verilog",
        "--consumer-root", str(tmp_path),
        "--output", str(tmp_path / "gen-top/design_passthrough_demo/algo_top.v"),
        "--build-dir", str(tmp_path / "build/passthrough_demo"),
    )
    assert result.returncode == 0, result.stdout + result.stderr

    return tmp_path


def _design_verification_yml(consumer_root: Path) -> Path:
    return consumer_root / "plugins/passthrough_demo/forge/verify/design.verification.yml"


@skip_without_xsim
def test_check_only_generates_validates_and_checks_layout(
    capsys: pytest.CaptureFixture[str], consumer_root: Path,
) -> None:
    result = _run(
        capsys, "test", "check-only", str(_design_verification_yml(consumer_root)),
        "--flow", "passthrough_xsim", "--consumer-root", str(consumer_root), "--json",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["metrics"]["mode"] == "check-only"


@skip_without_xsim
def test_prepare_generates_validates_and_checks_layout(
    capsys: pytest.CaptureFixture[str], consumer_root: Path,
) -> None:
    result = _run(
        capsys, "test", "prepare", str(_design_verification_yml(consumer_root)),
        "--flow", "passthrough_xsim", "--consumer-root", str(consumer_root), "--json",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["metrics"]["mode"] == "prepare"


@skip_without_xsim
def test_run_single_event_passes_with_real_xsim(
    capsys: pytest.CaptureFixture[str], consumer_root: Path,
) -> None:
    result = _run(
        capsys, "test", "run", str(_design_verification_yml(consumer_root)),
        "--flow", "passthrough_xsim", "--plugin", "passthrough_demo",
        "--consumer-root", str(consumer_root), "--event-id", "0", "--json",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    metrics = payload["metrics"]
    assert metrics["events_run"] == 1
    assert metrics["events_passed"] == 1
    assert metrics["events_failed"] == 0
    # Phase 7 slice 7.2: a real, non-fabricated aggregate duration.
    assert metrics["duration_s"] > 0
    assert payload["diagnostics"] == []


@skip_without_xsim
def test_run_all_events_produces_two_distinct_per_event_results(
    capsys: pytest.CaptureFixture[str], consumer_root: Path,
) -> None:
    """passthrough_demo's real golden dataset has 2 events (id 0 and 1,
    confirmed by inspection of schemas/data/passthrough_demo_golden.xml)
    — `--all-events` here means "run each individually" (unlike `forge
    verify run --all-events`, which feeds every event through *one*
    simulation), so this must run xsim twice and report 2 distinct
    per-event results, both real passes."""
    junit_path = consumer_root / "junit.xml"

    result = _run(
        capsys, "test", "run", str(_design_verification_yml(consumer_root)),
        "--flow", "passthrough_xsim", "--plugin", "passthrough_demo",
        "--consumer-root", str(consumer_root), "--all-events",
        "--junit-xml", str(junit_path), "--json",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    metrics = payload["metrics"]
    assert metrics["events_run"] == 2
    assert metrics["events_passed"] == 2
    assert metrics["events_failed"] == 0
    assert metrics["duration_s"] > 0

    # Real per-event simulation logs for both distinct events actually exist.
    xsim_work = (
        consumer_root
        / "plugins/passthrough_demo/forge/verify/passthrough_xsim/xsim_work/per_event"
    )
    assert (xsim_work / "0" / "simulate.log").exists()
    assert (xsim_work / "1" / "simulate.log").exists()

    # A valid JUnit XML with 2 <testcase> elements.
    assert junit_path.exists()
    import xml.etree.ElementTree as ET

    tree = ET.parse(junit_path)
    testsuite = tree.getroot()
    assert testsuite.tag == "testsuite"
    assert testsuite.get("tests") == "2"
    assert testsuite.get("failures") == "0"
    testcases = testsuite.findall("testcase")
    assert len(testcases) == 2
    assert {tc.get("name") for tc in testcases} == {"event_0", "event_1"}
    assert str(junit_path) in payload["artifacts"]
    # Phase 7 slice 7.2: JUnit's previously-unused `time` attribute is now
    # populated from each EventResult's real duration_s — real, non-zero,
    # non-fabricated numbers, not just present-but-empty.
    for tc in testcases:
        assert float(tc.get("time")) > 0

    # Regression guard for a real bug found while building this slice:
    # `forge verify prepare`'s own stimulus check never *generates*
    # stimulus_current.svh (that's a separate, plugin-owned step) — so
    # without `forge test run` regenerating it per event, a second event
    # would silently re-simulate the first event's stale golden data and
    # still report a false pass. The final stimulus_current.svh here must
    # reflect the *last* event actually run (event 1), not be stale.
    stimulus_svh = (
        consumer_root
        / "plugins/passthrough_demo/forge/verify/passthrough_xsim/stimulus_current.svh"
    ).read_text()
    assert "event=1" in stimulus_svh


@skip_without_xsim
def test_run_results_json_has_real_backend_id_and_schema(
    capsys: pytest.CaptureFixture[str], consumer_root: Path,
) -> None:
    """Phase 7 slice 7.2: --results-json writes a real, versioned FlowResult
    — schema tag, real backend_id/duration_s per event, real waveform
    artifact path (populated by slice 7.0's ExecutionResult.waveform_path)."""
    results_path = consumer_root / "results.json"

    result = _run(
        capsys, "test", "run", str(_design_verification_yml(consumer_root)),
        "--flow", "passthrough_xsim", "--plugin", "passthrough_demo",
        "--consumer-root", str(consumer_root), "--event-id", "0",
        "--results-json", str(results_path), "--json",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert str(results_path) in payload["artifacts"]

    assert results_path.exists()
    flow_result = json.loads(results_path.read_text())

    assert flow_result["schema"] == {"name": "forge.verification_results", "version": "1.0"}
    assert flow_result["backend_id"] == "xsim"
    assert flow_result["success"] is True
    assert flow_result["duration_s"] > 0

    events = flow_result["events"]
    assert len(events) == 1
    ev = events[0]
    assert ev["event_id"] == "0"
    assert ev["backend_id"] == "xsim"
    assert ev["success"] is True
    assert ev["duration_s"] > 0
    assert ev["diagnostics"] == []

    waveform_refs = [a for a in ev["artifacts"] if a["kind"] == "waveform"]
    assert len(waveform_refs) == 1
    assert Path(waveform_refs[0]["path"]).exists()
    assert waveform_refs[0]["stage"] == "simulate"


@skip_without_xsim
def test_run_compile_failure_results_json_reports_compile_stage_diagnostic(
    capsys: pytest.CaptureFixture[str], consumer_root: Path,
) -> None:
    """A real compile failure must produce a results.json diagnostic tagged
    with the real ``compile`` stage — FWV021, not the old hardcoded FWV013
    (the direct fix for `_tail_of_log` always reading `simulate_log`)."""
    tb_sv = (
        consumer_root
        / "plugins/passthrough_demo/forge/verify/passthrough_xsim/tb_algo_top.sv"
    )
    original = tb_sv.read_text()
    tb_sv.write_text(original + "\nthis is not valid systemverilog {{{\n")

    results_path = consumer_root / "results.json"
    result = _run(
        capsys, "test", "run", str(_design_verification_yml(consumer_root)),
        "--flow", "passthrough_xsim", "--plugin", "passthrough_demo",
        "--consumer-root", str(consumer_root), "--event-id", "0",
        "--results-json", str(results_path), "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["diagnostics"][0]["code"] == "FWV021"
    assert payload["diagnostics"][0]["context"]["stage"] == "compile"

    flow_result = json.loads(results_path.read_text())
    ev = flow_result["events"][0]
    assert ev["success"] is False
    assert ev["diagnostics"][0]["code"] == "FWV021"
    assert ev["diagnostics"][0]["context"]["stage"] == "compile"
    # The compile log itself is real and tagged with the real stage.
    compile_logs = [
        a for a in ev["artifacts"]
        if a["stage"] == "compile"
    ]
    assert len(compile_logs) == 1
    assert Path(compile_logs[0]["path"]).name == "xvlog.log"


@skip_without_xsim
def test_run_results_json_check_records_real_pass(
    capsys: pytest.CaptureFixture[str], consumer_root: Path,
) -> None:
    """Phase 7 slice 7.3: a real passing event's CheckResult has real,
    matching expected/observed values read from a real FORGE_CHECK| log
    line — not inferred from the absence of a failure."""
    results_path = consumer_root / "results.json"
    result = _run(
        capsys, "test", "run", str(_design_verification_yml(consumer_root)),
        "--flow", "passthrough_xsim", "--plugin", "passthrough_demo",
        "--consumer-root", str(consumer_root), "--event-id", "0",
        "--results-json", str(results_path), "--json",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    flow_result = json.loads(results_path.read_text())
    ev = flow_result["events"][0]
    checks = ev["checks"]
    assert len(checks) == 2  # data_out_check, data_out_valid_check

    by_label = {c["label"]: c for c in checks}
    data_out = by_label["data_out_check"]
    assert data_out["check_id"] == "0:data_out_check"
    assert data_out["signal"] == "pt_data_out"
    assert data_out["passed"] is True
    assert data_out["expected"] == data_out["observed"] == "0x3a"
    assert data_out["width"] == 8

    valid = by_label["data_out_valid_check"]
    assert valid["passed"] is True
    assert valid["expected"] == valid["observed"] == "0x1"


@skip_without_xsim
def test_run_results_json_check_records_real_failure(
    capsys: pytest.CaptureFixture[str], consumer_root: Path,
) -> None:
    """A deliberately-broken expected value (matching the negative-control
    convention already established) must produce a genuinely mismatched
    CheckResult with passed=False — proving the parser reads the real
    observed value, not a fabricated one, on the failure path too.

    Phase 7 slice 7.4a: gen_stimulus.py now reads its golden events for
    real from the XML dataset file (no more hardcoded Python dict) — so
    the way to break event 0's expected value here is editing that real
    XML file in this test's own tmp copy, exactly as slice 7.4a's own test
    plan describes. `forge test run`'s per-event `_regenerate_stimulus_for_event`
    re-reads the same (edited) file on every event, so no module-cache
    trickery is needed."""
    golden_xml = (
        consumer_root
        / "plugins/passthrough_demo/forge/verify/schemas/data/passthrough_demo_golden.xml"
    )
    original_xml = golden_xml.read_text()
    golden_xml.write_text(
        original_xml.replace(
            '<golden data_out="0x3A" data_out_valid="1"/>',
            '<golden data_out="0x00" data_out_valid="1"/>',  # real DUT will still produce 0x3a
        )
    )
    assert "0x00" in golden_xml.read_text()  # sanity: the replace actually matched

    results_path = consumer_root / "results.json"
    result = _run(
        capsys, "test", "run", str(_design_verification_yml(consumer_root)),
        "--flow", "passthrough_xsim", "--plugin", "passthrough_demo",
        "--consumer-root", str(consumer_root), "--event-id", "0",
        "--results-json", str(results_path), "--json",
    )

    assert result.returncode == 1
    flow_result = json.loads(results_path.read_text())
    ev = flow_result["events"][0]
    assert ev["success"] is False
    checks = ev["checks"]
    assert len(checks) == 1  # $fatal on the first mismatch aborts the second check
    data_out = checks[0]
    assert data_out["label"] == "data_out_check"
    assert data_out["passed"] is False
    assert data_out["expected"] == "0x00"
    assert data_out["observed"] == "0x3a"
    assert data_out["expected"] != data_out["observed"]


@skip_without_xsim
def test_run_event_list_runs_exactly_the_listed_events(
    capsys: pytest.CaptureFixture[str], consumer_root: Path,
) -> None:
    result = _run(
        capsys, "test", "run", str(_design_verification_yml(consumer_root)),
        "--flow", "passthrough_xsim", "--plugin", "passthrough_demo",
        "--consumer-root", str(consumer_root), "--event-list", "1", "--json",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["metrics"]["events_run"] == 1


@skip_without_xsim
def test_run_missing_rtl_reports_a_failed_event_not_a_false_pass(
    capsys: pytest.CaptureFixture[str], consumer_root: Path,
) -> None:
    """Same sanity check as test_verify_run_xsim.py's analogous test: a
    broken canonical layout must be caught as a real per-event failure,
    not silently accepted."""
    (consumer_root / "gen-top/design_passthrough_demo/algo_top.v").unlink()

    result = _run(
        capsys, "test", "run", str(_design_verification_yml(consumer_root)),
        "--flow", "passthrough_xsim", "--plugin", "passthrough_demo",
        "--consumer-root", str(consumer_root), "--event-id", "0", "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "fail"
    metrics = payload["metrics"]
    assert metrics["events_run"] == 1
    assert metrics["events_passed"] == 0
    assert metrics["events_failed"] == 1
    assert metrics["duration_s"] == 0.0
    # Phase 7 slice 7.2: missing DUT RTL is caught at preflight, before any
    # backend runs — the real stage-aware code is FWV011 (preflight), not
    # the old hardcoded-regardless-of-cause FWV013 (simulate-stage exit).
    assert payload["diagnostics"][0]["code"] == "FWV011"


@skip_without_xsim
def test_run_readmemh_mode_compiles_once_across_two_events(
    capsys: pytest.CaptureFixture[str], consumer_root: Path,
) -> None:
    """Phase 7 slice 7.5 acceptance bar: a real 2-event `--all-events` run
    on the real `passthrough_readmemh` flow (stimulus_mode: readmemh) must
    run xvlog/xelab exactly once, not twice — confirmed via the real
    stdout narration (the "[xsim 1/3] Compiling" progress line, which only
    ever prints on a real xvlog invocation, vs. the real
    "[xsim 1-2/3] Reusing existing compile+elaborate" skip line) — and
    both real events must still produce correct, distinct results."""
    results_path = consumer_root / "results.json"

    # No --json: json_mode swallows all narrative print()s (including the
    # real "[xsim 1/3] Compiling" / "Reusing existing compile+elaborate"
    # progress lines this test needs to observe) into a StringIO — human
    # mode is required to see them on real stdout. --results-json still
    # writes the structured file regardless of --json.
    result = _run(
        capsys, "test", "run", str(_design_verification_yml(consumer_root)),
        "--flow", "passthrough_readmemh", "--plugin", "passthrough_demo",
        "--consumer-root", str(consumer_root), "--all-events",
        "--results-json", str(results_path),
    )

    assert result.returncode == 0, result.stdout + result.stderr

    # The real, observable compile-count reduction: exactly one real
    # compile, one real reuse — not two compiles, and not zero.
    assert result.stdout.count("[xsim 1/3] Compiling") == 1
    assert result.stdout.count("[xsim 1-2/3] Reusing existing compile+elaborate") == 1
    assert result.stdout.count("[xsim 3/3] Simulating") == 2  # both events still really run

    flow_result = json.loads(results_path.read_text())
    assert flow_result["success"] is True
    events = sorted(flow_result["events"], key=lambda e: e["event_id"])
    assert len(events) == 2

    ev0, ev1 = events
    assert ev0["event_id"] == "0" and ev0["event_index"] == 0
    assert ev1["event_id"] == "1" and ev1["event_index"] == 1
    assert ev0["success"] is True and ev1["success"] is True

    # Real, distinct golden values per event (0x3A/valid=1 vs 0x00/valid=0
    # — passthrough_demo_golden.xml's real 2 events) — not the same
    # result twice, which would indicate event selection silently failed.
    ev0_checks = {c["label"]: c for c in ev0["checks"]}
    ev1_checks = {c["label"]: c for c in ev1["checks"]}
    assert ev0_checks["data_out_check"]["observed"] == "0x3a"
    assert ev1_checks["data_out_check"]["observed"] == "0x00"
    assert ev0_checks["data_out_check"]["observed"] == ev0_checks["data_out_check"]["expected"]
    assert ev1_checks["data_out_check"]["observed"] == ev1_checks["data_out_check"]["expected"]

    # Both events genuinely shared one compiled snapshot (same work dir),
    # never per-event subdirectories.
    shared_work_dir = (
        consumer_root
        / "plugins/passthrough_demo/forge/verify/passthrough_readmemh/xsim_work/readmemh_shared"
    )
    assert (shared_work_dir / "xvlog.log").exists()
    assert (shared_work_dir / "xelab.log").exists()
    assert not (
        consumer_root
        / "plugins/passthrough_demo/forge/verify/passthrough_readmemh/xsim_work/per_event"
    ).exists()


@skip_without_xsim
def test_run_svh_include_flows_unaffected_by_readmemh_mechanism(
    capsys: pytest.CaptureFixture[str], consumer_root: Path,
) -> None:
    """Regression guard: the default svh_include flow (passthrough_xsim)
    must still recompile per event, exactly as before slice 7.5 — the
    readmemh mechanism is opt-in via stimulus_mode, never a behavior
    change for flows that don't declare it."""
    result = _run(
        capsys, "test", "run", str(_design_verification_yml(consumer_root)),
        "--flow", "passthrough_xsim", "--plugin", "passthrough_demo",
        "--consumer-root", str(consumer_root), "--all-events",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("[xsim 1/3] Compiling") == 2  # real recompile, both events
    assert "Reusing existing compile+elaborate" not in result.stdout

    per_event = (
        consumer_root
        / "plugins/passthrough_demo/forge/verify/passthrough_xsim/xsim_work/per_event"
    )
    assert (per_event / "0" / "simulate.log").exists()
    assert (per_event / "1" / "simulate.log").exists()


def test_run_requires_flow_argument(capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["test", "run", "design.verification.yml"])


def test_registered_as_top_level_command() -> None:
    parser = build_parser()
    parsed = parser.parse_args(["test", "check-only", "design.verification.yml"])
    assert parsed.func.__name__ == "cmd_check_only"
