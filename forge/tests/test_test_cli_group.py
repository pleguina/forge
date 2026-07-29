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
    assert payload["metrics"] == {"events_run": 1, "events_passed": 1, "events_failed": 0}
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
    assert payload["metrics"] == {"events_run": 2, "events_passed": 2, "events_failed": 0}

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
    assert payload["metrics"] == {"events_run": 1, "events_passed": 0, "events_failed": 1}
    assert payload["diagnostics"][0]["code"] == "FWV013"


def test_run_requires_flow_argument(capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["test", "run", "design.verification.yml"])


def test_registered_as_top_level_command() -> None:
    parser = build_parser()
    parsed = parser.parse_args(["test", "check-only", "design.verification.yml"])
    assert parsed.func.__name__ == "cmd_check_only"
