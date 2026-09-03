"""Coverage for `forge fix` — the repairs a project's own sources prove.

The one property this suite exists to defend is the safety line, and it is
drawn at evidence rather than at confidence: a repair that *transcribes*
something a source already states is applied; a repair that *decides*
something is never applied, and is not offered behind a confirmation prompt
either. A regression here would not fail loudly — it would quietly write a
plausible guess into contract truth, which is the single failure mode the
whole adoption design exists to prevent.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from forge.core.cli.envelope import CommandEnvelope
from forge.core.cli.main import build_parser
from forge.project.evidence import DETERMINISTIC, HEURISTIC, INFERRED, Evidence
from forge.project.fix import Fix, plan_fixes

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "foreign" / "simple_pipeline"


def _run(capsys: pytest.CaptureFixture[str], argv: list):
    parser = build_parser()
    code = 0
    try:
        parsed = parser.parse_args(argv)
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def adopted(tmp_path: Path) -> Path:
    from forge.project.adopt import plan_adoption

    destination = tmp_path / "simple_pipeline"
    shutil.copytree(FIXTURE, destination)
    plan_adoption(destination).write()
    return destination


# ── The safety line ───────────────────────────────────────────────────────

def test_only_a_fix_the_sources_prove_counts_as_safe() -> None:
    proven = Fix("f", "t", evidence=[Evidence("a.sv", "port_width", 32, DETERMINISTIC)])
    inferred = Fix("f", "t", evidence=[Evidence("a.sv", "convention", "clk", INFERRED)])
    guessed = Fix("f", "t", evidence=[Evidence("a.sv", "name", "muon", HEURISTIC)])
    unevidenced = Fix("f", "t")

    assert proven.safe is True
    assert inferred.safe is False
    assert guessed.safe is False
    assert unevidenced.safe is False


def test_applying_an_unproven_fix_is_refused_even_when_asked_directly(
    tmp_path: Path,
) -> None:
    """A second line of defence behind the planner's own filtering — an
    unsafe fix reaching `apply()` at all is a bug, and writing it anyway
    would be the failure this module exists to prevent."""
    target = tmp_path / "contract.yaml"
    target.write_text("original\n")
    unsafe = Fix(
        "invent-family", "guess a semantic family", path=target,
        before="original\n", after="family: reconstructed_muon\n",
        evidence=[Evidence("a.sv", "port_name_resembles", "muon", HEURISTIC)],
    )

    with pytest.raises(ValueError, match="not proven"):
        unsafe.apply()
    assert target.read_text() == "original\n"


def test_an_unsafe_fix_is_never_even_offered(adopted: Path) -> None:
    plan = plan_fixes(adopted)

    assert all(fix.safe for fix in plan.safe_fixes)


# ── Contract drift ────────────────────────────────────────────────────────

def test_a_width_the_rtl_contradicts_is_corrected(adopted: Path) -> None:
    """The plan's worked example of a safe fix: the source holds the one
    right answer, so FORGE transcribes rather than chooses."""
    contract = adopted / ".forge/contracts/shaper.interface.yaml"
    contract.write_text(contract.read_text().replace(
        "    shaped:\n", "    shaped:\n      width: 31\n"))

    plan = plan_fixes(adopted, only=["correct-contract-drift"])
    assert len(plan.safe_fixes) == 1
    plan.apply()

    assert "width: 16" in contract.read_text()
    assert "width: 31" not in contract.read_text()


def test_correcting_a_width_keeps_the_rest_of_the_file_intact(
    adopted: Path,
) -> None:
    """A YAML round-trip would discard every comment in a file whose
    comments are most of its value to whoever maintains it."""
    contract = adopted / ".forge/contracts/shaper.interface.yaml"
    original = contract.read_text()
    contract.write_text(original.replace("    shaped:\n", "    shaped:\n      width: 31\n"))

    plan_fixes(adopted, only=["correct-contract-drift"]).apply()
    repaired = contract.read_text()

    assert "# Inferred integration contract skeleton" in repaired
    assert repaired.count("\n") == original.count("\n") + 1
    for role in ("shaped_valid:", "decimated:", "clock_primary:"):
        assert role in repaired


def test_a_role_bound_to_a_port_that_does_not_exist_is_left_alone(
    adopted: Path,
) -> None:
    """Either the port was renamed or the role was mis-bound, and choosing
    between those is a decision, not a transcription."""
    contract = adopted / ".forge/contracts/shaper.interface.yaml"
    contract.write_text(
        contract.read_text() + "\n    ghost:\n      raw_port: not_a_port\n")

    plan = plan_fixes(adopted)

    assert not any(f.id == "correct-contract-drift" for f in plan.safe_fixes)
    assert "not_a_port" in contract.read_text()


# ── Missing contracts ─────────────────────────────────────────────────────

def test_a_missing_contract_is_regenerated_from_the_modules_own_ports(
    adopted: Path,
) -> None:
    contract = adopted / ".forge/contracts/packer.interface.yaml"
    contract.unlink()

    plan_fixes(adopted, only=["generate-missing-contract"]).apply()

    assert contract.is_file()
    text = contract.read_text()
    assert "module_name: packer" in text
    # Emitted as a draft, so nothing in it claims to have been reviewed.
    assert "normalization_status: draft" in text
    # And the semantics FORGE cannot infer stay absent rather than invented.
    assert "wiring_kind:" not in text
    assert "family:" not in text


# ── Generated state ───────────────────────────────────────────────────────

def test_a_missing_state_gitignore_is_restored(adopted: Path) -> None:
    ignore = adopted / ".forge/.gitignore"
    ignore.unlink()

    plan_fixes(adopted, only=["restore-state-gitignore"]).apply()

    assert ignore.is_file()
    assert ignore.read_text().strip().endswith("*")


def test_a_stale_top_level_is_offered_as_a_command_not_a_rewrite(
    adopted: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Regeneration is `forge build`'s job; reimplementing it here would
    create a second generator that could disagree with the real one."""
    import os

    code, _out, err = _run(capsys, [
        "build", str(adopted / ".forge/project/design.yml"),
        "--contracts-from", str(adopted / ".forge/project/modules.yml"),
        "--consumer-root", str(adopted),
        "--output", str(adopted / ".forge/generated/algo_top.v"), "--apply",
    ])
    assert code == 0, err
    os.utime(adopted / ".forge/generated/algo_top.v", (0, 0))

    plan = plan_fixes(adopted, only=["regenerate-stale-artifacts"])

    assert len(plan.safe_fixes) == 1
    fix = plan.safe_fixes[0]
    assert fix.command is not None and fix.command.startswith("forge build ")
    assert fix.apply() is None


# ── Planning behaviour ────────────────────────────────────────────────────

def test_a_clean_project_has_nothing_to_fix(adopted: Path) -> None:
    assert plan_fixes(adopted).safe_fixes == []


def test_fixing_is_idempotent(adopted: Path) -> None:
    (adopted / ".forge/.gitignore").unlink()
    (adopted / ".forge/contracts/packer.interface.yaml").unlink()

    first = plan_fixes(adopted)
    first.apply()

    assert len(first.applied) == 2
    assert plan_fixes(adopted).safe_fixes == []


def test_an_action_a_fix_discharges_drops_off_the_manual_list(
    adopted: Path,
) -> None:
    (adopted / ".forge/contracts/packer.interface.yaml").unlink()

    plan = plan_fixes(adopted)

    assert any(f.id == "generate-missing-contract" for f in plan.safe_fixes)
    assert not any(a.id == "generate-missing-contracts" for a in plan.manual)


def test_decisions_stay_on_the_manual_list(adopted: Path) -> None:
    """Reviewing a draft contract, confirming a reset polarity and declaring
    a latency are all judgement calls, and no fix may claim them."""
    plan = plan_fixes(adopted)
    manual = {a.id for a in plan.manual}

    assert "review-draft-contracts" in manual
    assert "confirm-reset-polarity" in manual
    assert "declare-latency" in manual


def test_planning_outside_a_project_reports_adopt_rather_than_crashing(
    tmp_path: Path,
) -> None:
    plan = plan_fixes(tmp_path)

    assert plan.safe_fixes == []
    assert any(a.id == "adopt-project" for a in plan.manual)


# ── The CLI ───────────────────────────────────────────────────────────────

def test_fix_previews_by_default_and_writes_nothing(
    capsys: pytest.CaptureFixture[str], adopted: Path,
) -> None:
    (adopted / ".forge/.gitignore").unlink()

    code, out, _err = _run(capsys, ["fix", str(adopted)])

    assert code == 0
    assert "Available: 1 automatic fix" in out
    assert "forge fix --apply" in out
    assert not (adopted / ".forge/.gitignore").exists()


def test_fix_apply_writes_and_reports_what_it_wrote(
    capsys: pytest.CaptureFixture[str], adopted: Path,
) -> None:
    (adopted / ".forge/.gitignore").unlink()

    code, out, _err = _run(capsys, ["fix", str(adopted), "--apply"])

    assert code == 0
    assert "Applied: 1 automatic fix" in out
    assert (adopted / ".forge/.gitignore").is_file()


def test_fix_shows_a_diff_and_the_evidence_behind_every_repair(
    capsys: pytest.CaptureFixture[str], adopted: Path,
) -> None:
    contract = adopted / ".forge/contracts/shaper.interface.yaml"
    contract.write_text(contract.read_text().replace(
        "    shaped:\n", "    shaped:\n      width: 31\n"))

    code, out, _err = _run(capsys, ["fix", str(adopted), "--only", "correct-contract-drift"])

    assert code == 0
    assert "-      width: 31" in out
    assert "+      width: 16" in out
    assert "width_declared_by_source" in out
    assert "deterministic" in out


def test_fix_separates_work_it_declines_from_decisions_it_cannot_make(
    capsys: pytest.CaptureFixture[str], adopted: Path,
) -> None:
    code, out, _err = _run(capsys, ["fix", str(adopted)])

    assert code == 0
    assert "Not fix's job — run these yourself:" in out
    assert "Needs you — these are decisions, not transcription:" in out


def test_fix_json_round_trips_through_the_shared_envelope(
    capsys: pytest.CaptureFixture[str], adopted: Path,
) -> None:
    (adopted / ".forge/.gitignore").unlink()

    code, out, _err = _run(capsys, ["fix", str(adopted), "--json"])
    payload = json.loads(out)

    assert code == 0
    envelope = CommandEnvelope.from_dict(payload)
    assert envelope.status == "pass"
    assert payload["metrics"]["dry_run"] is True
    assert payload["metrics"]["fixes"][0]["id"] == "restore-state-gitignore"
    assert payload["metrics"]["fixes"][0]["safe"] is True
    assert payload["metrics"]["fixes"][0]["diff"]


def test_fix_never_runs_a_generator_on_the_users_behalf(
    capsys: pytest.CaptureFixture[str], adopted: Path,
) -> None:
    """Silently invoking `forge build` from a command called "fix" would
    surprise anyone who expected a configuration repair."""
    code, _out, _err = _run(capsys, ["fix", str(adopted), "--apply"])

    assert code == 0
    assert not (adopted / ".forge/generated/algo_top.v").exists()
