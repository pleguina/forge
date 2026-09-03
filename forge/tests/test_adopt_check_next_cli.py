"""Coverage for the three new-user commands — `forge adopt`, `forge check`,
`forge next` — plus the end-to-end path the plan makes its primary
acceptance test:

    a repository that was never laid out for FORGE
    -> forge adopt -> forge check -> forge build -> a real generated top level

with no hand-written module registry, design topology, interface contract or
verification manifest anywhere in it.

Every command here is driven through the real `build_parser()` argv path a
user would type, not by calling internals, so the envelope, exit codes and
help text are all exercised as shipped.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from forge.core.cli.envelope import ENVELOPE_SCHEMA_VERSION, CommandEnvelope
from forge.core.cli.main import build_parser

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
def foreign(tmp_path: Path) -> Path:
    """A pristine copy of the un-FORGE-shaped fixture repository."""
    destination = tmp_path / "simple_pipeline"
    shutil.copytree(FIXTURE, destination)
    return destination


# ── forge adopt ───────────────────────────────────────────────────────────

def test_adopt_creates_a_working_project_from_sources_alone(
    capsys: pytest.CaptureFixture[str], foreign: Path,
) -> None:
    code, out, _err = _run(capsys, ["adopt", str(foreign)])

    assert code == 0
    assert (foreign / "forge.yml").is_file()
    assert (foreign / ".forge" / "project" / "modules.yml").is_file()
    assert (foreign / ".forge" / "project" / "design.yml").is_file()
    for module in ("decimator", "shaper", "packer"):
        assert (foreign / ".forge" / "contracts" / f"{module}.interface.yaml").is_file()
    assert "Detected clocks" in out
    assert "Inferred topology" in out


def test_adopt_leaves_the_users_own_tree_untouched(
    capsys: pytest.CaptureFixture[str], foreign: Path,
) -> None:
    before = {
        p.relative_to(foreign): p.read_bytes()
        for p in sorted(foreign.rglob("*")) if p.is_file()
    }

    _run(capsys, ["adopt", str(foreign)])

    after = {
        p.relative_to(foreign): p.read_bytes()
        for p in sorted(foreign.rglob("*"))
        if p.is_file() and ".forge" not in p.parts and p.name != "forge.yml"
    }
    assert after == before


def test_adopt_dry_run_writes_nothing(
    capsys: pytest.CaptureFixture[str], foreign: Path,
) -> None:
    code, out, _err = _run(capsys, ["adopt", str(foreign), "--dry-run"])

    assert code == 0
    assert "[dry-run]" in out
    assert not (foreign / "forge.yml").exists()
    assert not (foreign / ".forge").exists()


def test_adopt_is_idempotent(
    capsys: pytest.CaptureFixture[str], foreign: Path,
) -> None:
    _run(capsys, ["adopt", str(foreign)])
    first = (foreign / ".forge" / "project" / "design.yml").read_text()

    _run(capsys, ["adopt", str(foreign)])
    second = (foreign / ".forge" / "project" / "design.yml").read_text()

    assert first == second


def test_re_adopting_never_overwrites_the_users_forge_yml(
    capsys: pytest.CaptureFixture[str], foreign: Path,
) -> None:
    """`forge.yml` is the one file in the project the user owns; a command
    people are told to re-run must not eat their edits."""
    _run(capsys, ["adopt", str(foreign)])
    edited = (foreign / "forge.yml").read_text() + "\n# my note\n"
    (foreign / "forge.yml").write_text(edited)

    _run(capsys, ["adopt", str(foreign)])
    assert (foreign / "forge.yml").read_text() == edited

    _run(capsys, ["adopt", str(foreign), "--force"])
    assert "# my note" not in (foreign / "forge.yml").read_text()


def test_adopt_reports_an_empty_directory_as_a_finding_not_a_crash(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    code, _out, err = _run(capsys, ["adopt", str(tmp_path)])

    assert code == 1
    assert "No RTL modules or HLS kernels found" in err
    assert not (tmp_path / "forge.yml").exists()


def test_adopt_json_carries_the_decisions_it_made(
    capsys: pytest.CaptureFixture[str], foreign: Path,
) -> None:
    code, out, _err = _run(capsys, ["adopt", str(foreign), "--json"])
    payload = json.loads(out)

    assert code == 0
    assert payload["schema_version"] == ENVELOPE_SCHEMA_VERSION
    CommandEnvelope.from_dict(payload)
    rules = {d["rule"] for d in payload["metrics"]["decisions"]}
    assert "single_clock_candidate" in rules
    assert "sole_matching_consumer" in rules
    assert all(d["confidence"] in ("deterministic", "inferred", "heuristic")
               for d in payload["metrics"]["decisions"])


def test_adopt_reports_ambiguity_without_resolving_it(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "classifier.v").write_text(
        "module classifier(input clk, output [63:0] candidate_out);\nendmodule\n")
    (rtl / "formatter.v").write_text(
        "module formatter(input clk, input [63:0] candidate_in);\nendmodule\n")
    (rtl / "sink.v").write_text(
        "module sink(input clk, input [63:0] candidate);\nendmodule\n")

    code, out, _err = _run(capsys, ["adopt", str(tmp_path)])
    design = (tmp_path / ".forge" / "project" / "design.yml").read_text()

    assert code == 0
    assert "Unresolved decisions: 1" in out
    assert "connections:" not in design


# ── forge check ───────────────────────────────────────────────────────────

def test_check_on_an_unadopted_directory_points_at_adopt(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    code, out, _err = _run(capsys, ["check", str(tmp_path)])

    assert code == 1
    assert "forge adopt" in out


def test_check_reports_every_section_and_a_completion_figure(
    capsys: pytest.CaptureFixture[str], foreign: Path,
) -> None:
    _run(capsys, ["adopt", str(foreign)])
    code, out, _err = _run(capsys, ["check", str(foreign)])

    assert code == 0
    for section in ("Sources", "Contracts", "Topology", "Clock/reset model",
                    "Verification", "Generated artifacts"):
        assert section in out
    assert "Project completion:" in out


def test_check_fails_on_an_undecided_consumer(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "classifier.v").write_text(
        "module classifier(input clk, output [63:0] candidate_out);\nendmodule\n")
    (rtl / "formatter.v").write_text(
        "module formatter(input clk, input [63:0] candidate_in);\nendmodule\n")
    (rtl / "sink.v").write_text(
        "module sink(input clk, input [63:0] candidate);\nendmodule\n")
    _run(capsys, ["adopt", str(tmp_path)])

    code, out, _err = _run(capsys, ["check", str(tmp_path)])

    assert code == 1
    assert "ATG037" in out
    assert "formatter.candidate_in" in out
    assert "sink.candidate" in out


def test_check_json_round_trips_through_the_shared_envelope(
    capsys: pytest.CaptureFixture[str], foreign: Path,
) -> None:
    _run(capsys, ["adopt", str(foreign)])
    code, out, _err = _run(capsys, ["check", str(foreign), "--json"])
    payload = json.loads(out)

    assert code == 0
    envelope = CommandEnvelope.from_dict(payload)
    assert envelope.schema_version == ENVELOPE_SCHEMA_VERSION
    assert envelope.status in ("pass", "warn", "fail")
    assert payload["metrics"]["sections"]
    assert envelope.next_actions


def test_check_strict_promotes_warnings_to_a_failure(
    capsys: pytest.CaptureFixture[str], foreign: Path,
) -> None:
    _run(capsys, ["adopt", str(foreign)])

    assert _run(capsys, ["check", str(foreign)])[0] == 0
    assert _run(capsys, ["check", str(foreign), "--strict"])[0] == 1


def test_check_finds_the_project_from_a_subdirectory(
    capsys: pytest.CaptureFixture[str], foreign: Path,
) -> None:
    _run(capsys, ["adopt", str(foreign)])

    code, out, _err = _run(capsys, ["check", str(foreign / "rtl")])

    assert code == 0
    assert str(foreign) in out


# ── forge next ────────────────────────────────────────────────────────────

def test_next_names_one_step_and_the_reason_for_it(
    capsys: pytest.CaptureFixture[str], foreign: Path,
) -> None:
    _run(capsys, ["adopt", str(foreign)])

    code, out, _err = _run(capsys, ["next", str(foreign)])

    assert code == 0
    assert "Recommended next step" in out
    assert "Reason" in out
    assert out.count("Recommended next step") == 1


def test_next_succeeds_even_when_the_project_is_blocked(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """Having a next step is the normal state of a project under
    construction; failing on it would make `forge next` useless in exactly
    the situation it exists for."""
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "classifier.v").write_text(
        "module classifier(input clk, output [63:0] candidate_out);\nendmodule\n")
    (rtl / "formatter.v").write_text(
        "module formatter(input clk, input [63:0] candidate_in);\nendmodule\n")
    (rtl / "sink.v").write_text(
        "module sink(input clk, input [63:0] candidate);\nendmodule\n")
    _run(capsys, ["adopt", str(tmp_path)])

    code, out, _err = _run(capsys, ["next", str(tmp_path)])

    assert code == 0
    assert "Recommended next step" in out


def test_next_and_check_never_disagree(
    capsys: pytest.CaptureFixture[str], foreign: Path,
) -> None:
    """Both are views of one `ProjectStatus`; the plan is explicit that they
    must not grow two rule systems."""
    _run(capsys, ["adopt", str(foreign)])
    _, check_out, _ = _run(capsys, ["check", str(foreign), "--json"])
    _, next_out, _ = _run(capsys, ["next", str(foreign), "--json"])

    check_payload = json.loads(check_out)
    next_payload = json.loads(next_out)

    assert check_payload["metrics"]["completion"] == next_payload["metrics"]["completion"]
    assert check_payload["next_actions"] == next_payload["next_actions"]
    assert (next_payload["metrics"]["recommended_action"]["description"]
            == check_payload["metrics"]["actions"][0]["description"])


# ── The whole new-user path ───────────────────────────────────────────────

def test_a_foreign_repository_reaches_a_generated_top_level_with_no_hand_written_yaml(
    capsys: pytest.CaptureFixture[str], foreign: Path,
) -> None:
    """The plan's primary acceptance test, as a test."""
    assert _run(capsys, ["adopt", str(foreign)])[0] == 0
    assert _run(capsys, ["check", str(foreign)])[0] == 0

    _, next_out, _ = _run(capsys, ["next", str(foreign), "--json"])
    command = json.loads(next_out)["metrics"]["recommended_action"]["command"]
    assert command.startswith("forge build ")

    # Run the very command `forge next` printed, from the project root, as a
    # user would — a recommendation that does not actually work is worse
    # than none.
    argv = command.split()[1:]
    argv = [
        str(foreign / token) if token.startswith(".forge/") else token
        for token in argv
    ]
    argv = [str(foreign) if token == "." else token for token in argv]
    assert _run(capsys, argv)[0] == 0

    top = foreign / ".forge" / "generated" / "algo_top.v"
    assert top.is_file()
    generated = top.read_text()
    for module in ("decimator", "shaper", "packer"):
        assert f"  {module} {module} (" in generated
    assert "input [15:0] decimator_sample_in" in generated
    assert "output [31:0] packer_packet_out" in generated

    _, out, _ = _run(capsys, ["check", str(foreign)])
    assert "Generated artifacts       PASS" in out


def test_the_adopted_project_validates_under_the_existing_validator(
    capsys: pytest.CaptureFixture[str], foreign: Path,
) -> None:
    """Adoption must produce ordinary FORGE configuration, not a parallel
    dialect only the new commands understand."""
    _run(capsys, ["adopt", str(foreign)])

    code, _out, _err = _run(
        capsys, ["topgen", "validate", str(foreign / ".forge/project/design.yml")])

    assert code == 0
