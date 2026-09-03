"""Coverage for `forge explain` — the command that makes every automated
decision inspectable (the plan's guiding principle 3.4).

The property worth defending hardest here is *honesty about the source*. An
explanation drawn from the canonical IR describes what the generators
actually did; one drawn from source discovery describes what FORGE would
infer if asked. Presenting the second as the first would make this command
confidently wrong, which is worse than not having it — so the source is
asserted alongside the content throughout.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from forge.core.cli.envelope import CommandEnvelope
from forge.core.cli.main import build_parser
from forge.project.explain import (
    KIND_ARTIFACT,
    KIND_CLOCK,
    KIND_CONNECTION,
    KIND_DIAGNOSTIC,
    KIND_HLS,
    KIND_MODULE,
    KIND_PORT,
    KIND_RESET,
    UnknownTarget,
    classify_target,
    explain,
)

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
    """The foreign fixture, adopted but not yet built."""
    from forge.project.adopt import plan_adoption

    destination = tmp_path / "simple_pipeline"
    shutil.copytree(FIXTURE, destination)
    plan_adoption(destination).write()
    return destination


@pytest.fixture
def built(adopted: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    """The same project, resolved and generated, so the IR exists."""
    from forge.project.explain import _IR_CACHE

    _IR_CACHE.clear()
    code, _out, err = _run(capsys, [
        "build", str(adopted / ".forge/project/design.yml"),
        "--contracts-from", str(adopted / ".forge/project/modules.yml"),
        "--consumer-root", str(adopted),
        "--output", str(adopted / ".forge/generated/algo_top.v"),
        "--apply",
    ])
    assert code == 0, err
    return adopted


@pytest.fixture(autouse=True)
def _clear_ir_cache():
    """Each test gets a fresh IR — the cache is keyed on the design path,
    and tmp_path reuse across a session would otherwise serve one test's
    resolved design to another."""
    from forge.project.explain import _IR_CACHE

    _IR_CACHE.clear()
    yield
    _IR_CACHE.clear()


# ── Target classification ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    "target,kind,subject",
    [
        ("ATG037", KIND_DIAGNOSTIC, "ATG037"),
        ("atg037", KIND_DIAGNOSTIC, "ATG037"),
        ("FWV001", KIND_DIAGNOSTIC, "FWV001"),
        ("shaper", KIND_MODULE, "shaper"),
        ("shaper.shaped", KIND_PORT, "shaper.shaped"),
        ("clock:clk", KIND_CLOCK, "clk"),
        ("reset:rst_n", KIND_RESET, "rst_n"),
        ("hls:regression", KIND_HLS, "regression"),
        ("connection:a.q->b.d", KIND_CONNECTION, "a.q->b.d"),
        ("artifact:out/algo_top.v", KIND_ARTIFACT, "out/algo_top.v"),
    ],
)
def test_targets_are_classified_by_prefix_then_shape(
    target: str, kind: str, subject: str,
) -> None:
    assert classify_target(target) == (kind, subject)


def test_an_explicit_prefix_beats_the_shape_heuristic() -> None:
    """A module genuinely called `clock` must stay reachable."""
    assert classify_target("module:clock") == (KIND_MODULE, "clock")
    assert classify_target("clock") == (KIND_MODULE, "clock")


def test_an_empty_target_is_a_user_input_error() -> None:
    with pytest.raises(UnknownTarget):
        classify_target("   ")


# ── Diagnostics ───────────────────────────────────────────────────────────

def test_a_diagnostic_code_is_explained_from_the_generated_catalogue(
    tmp_path: Path,
) -> None:
    """Not from a second copy kept here — a divergent copy is exactly the
    drift the catalogue exists to prevent."""
    from forge.docsgen.diagnostics_registry import DIAGNOSTICS

    explanation = explain("ATG037", tmp_path)

    assert explanation.kind == KIND_DIAGNOSTIC
    assert DIAGNOSTICS["ATG037"].description in explanation.summary
    assert explanation.source == "diagnostic catalogue"


def test_a_diagnostic_code_works_outside_any_project(tmp_path: Path) -> None:
    """A code pasted from a CI log is the most common way here."""
    explanation = explain("ATG046", tmp_path)

    assert explanation.kind == KIND_DIAGNOSTIC
    assert explanation.sections


def test_inside_a_project_a_code_also_reports_where_it_fired(adopted: Path) -> None:
    explanation = explain("ATG035", adopted)
    occurrences = next(s for s in explanation.sections if s.heading == "In this project")

    assert len(occurrences.lines) == 3
    assert all("draft" in line for line in occurrences.lines)


def test_an_unknown_code_is_refused_rather_than_invented(tmp_path: Path) -> None:
    with pytest.raises(UnknownTarget):
        explain("ATG999", tmp_path)


# ── Modules and ports ─────────────────────────────────────────────────────

def test_a_module_reports_its_source_ports_contract_and_connections(
    adopted: Path,
) -> None:
    explanation = explain("shaper", adopted)
    headings = [s.heading for s in explanation.sections]

    assert explanation.kind == KIND_MODULE
    assert "Source" in headings and "Ports" in headings
    assert "Contract" in headings and "Connections" in headings
    ports = next(s for s in explanation.sections if s.heading == "Ports")
    assert any("[15:0] shaped" in line for line in ports.lines)


def test_adoption_alone_is_enough_to_explain_from_the_ir(adopted: Path) -> None:
    """Resolution is a pure function of the project configuration, so an
    adopted project can be explained authoritatively without ever having
    been built — the IR is not a build artifact."""
    explanation = explain("shaper.shaped", adopted)
    resolved = next(s for s in explanation.sections if s.heading == "Resolved connection")

    assert explanation.source == "canonical IR"
    assert resolved.lines == ["packer.shaped"]


def test_a_design_that_does_not_resolve_falls_back_to_discovery(
    adopted: Path,
) -> None:
    """And says so, rather than presenting inference as resolution."""
    (adopted / ".forge/project/design.yml").write_text("this: is not a design\n")

    explanation = explain("shaper.shaped", adopted)
    inferred = next(s for s in explanation.sections if s.heading == "Inferred connection")

    assert explanation.source == "source discovery"
    assert "packer.shaped" in inferred.lines[0]
    assert "not yet resolved" in inferred.lines[1]


def test_a_port_explained_after_the_build_comes_from_the_ir(built: Path) -> None:
    explanation = explain("shaper.shaped", built)
    resolved = next(s for s in explanation.sections if s.heading == "Resolved connection")

    assert explanation.source == "canonical IR"
    assert resolved.lines == ["packer.shaped"]
    assert any(e.rule == "wiring_method" for e in explanation.evidence)


def test_a_port_reports_the_hdl_facts_its_own_source_declares(adopted: Path) -> None:
    explanation = explain("shaper.shaped", adopted)
    hdl = next(s for s in explanation.sections if s.heading == "HDL")

    assert "output [15:0] shaped" in hdl.lines[0]
    assert explanation.confidence in ("deterministic", "inferred")


def test_a_slim_contract_reports_what_it_does_not_declare(adopted: Path) -> None:
    """The absence of wiring_kind/protocol is a real, reportable answer."""
    explanation = explain("shaper.shaped", adopted)
    contract = next(s for s in explanation.sections if s.heading == "Contract")

    assert contract.lines[0] == "role: shaped"
    assert "still unset" in contract.lines[1]


def test_a_refusal_is_explained_as_carefully_as_a_decision(tmp_path: Path) -> None:
    """The plan's E231 case: what FORGE considered, and why it stopped."""
    from forge.project.adopt import plan_adoption

    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "classifier.v").write_text(
        "module classifier(input clk, output [63:0] muon_out);\nendmodule\n")
    (rtl / "formatter.v").write_text(
        "module formatter(input clk, input [63:0] muon_in);\nendmodule\n")
    (rtl / "debug_sink.v").write_text(
        "module debug_sink(input clk, input [63:0] muon);\nendmodule\n")
    plan_adoption(tmp_path).write()

    explanation = explain("classifier.muon_out", tmp_path)
    headings = [s.heading for s in explanation.sections]
    candidates = next(s for s in explanation.sections if s.heading == "Candidates it considered")

    assert "Why FORGE stopped" in headings
    assert "How to decide" in headings
    assert set(candidates.lines) == {"debug_sink.muon", "formatter.muon_in"}


def test_an_unknown_module_lists_what_does_exist(adopted: Path) -> None:
    with pytest.raises(UnknownTarget) as excinfo:
        explain("nonexistent", adopted)

    assert "shaper" in str(excinfo.value)


def test_an_unknown_port_lists_the_modules_real_ports(adopted: Path) -> None:
    with pytest.raises(UnknownTarget) as excinfo:
        explain("shaper.nonexistent", adopted)

    assert "shaped" in str(excinfo.value)


# ── Connections ───────────────────────────────────────────────────────────

def test_a_connection_carries_the_irs_full_matching_evidence(built: Path) -> None:
    explanation = explain("connection:shaper.shaped->packer.shaped", built)
    why = next(s for s in explanation.sections if s.heading == "Why")

    assert explanation.kind == KIND_CONNECTION
    assert explanation.source == "canonical IR"
    assert any("width: 16 -> 16" in line for line in why.lines)


def test_a_connection_resolves_without_a_prior_build(adopted: Path) -> None:
    explanation = explain("connection:shaper.shaped->packer.shaped", adopted)

    assert explanation.source == "canonical IR"


def test_a_connection_on_an_unresolvable_design_says_what_to_run(
    adopted: Path,
) -> None:
    (adopted / ".forge/project/design.yml").write_text("this: is not a design\n")

    with pytest.raises(UnknownTarget) as excinfo:
        explain("connection:shaper.shaped->packer.shaped", adopted)

    assert "forge check" in str(excinfo.value)


def test_an_unknown_connection_shows_real_ids(built: Path) -> None:
    with pytest.raises(UnknownTarget) as excinfo:
        explain("connection:nope", built)

    assert "->" in str(excinfo.value)


# ── Clocks and resets ─────────────────────────────────────────────────────

def test_a_clock_reports_every_module_it_reaches_and_why(adopted: Path) -> None:
    explanation = explain("clock:clk", adopted)
    appears = next(s for s in explanation.sections if s.heading == "Appears on")

    assert sorted(appears.lines) == ["decimator.clk", "packer.clk", "shaper.clk"]
    assert explanation.confidence == "inferred"


def test_an_active_low_reset_states_the_limitation_it_runs_into(
    adopted: Path,
) -> None:
    explanation = explain("reset:rst_n", adopted)
    level = next(s for s in explanation.sections if s.heading == "Active level")

    assert "active low" in level.lines[0]
    assert "do not invert" in level.lines[1]
    # The active level is read off a name, so nothing here may claim to be
    # proven.
    assert explanation.confidence == "heuristic"


def test_an_unknown_clock_lists_the_real_candidates(adopted: Path) -> None:
    with pytest.raises(UnknownTarget) as excinfo:
        explain("clock:nonexistent", adopted)

    assert "clk" in str(excinfo.value)


# ── Artifacts ─────────────────────────────────────────────────────────────

def test_an_artifact_reports_what_wrote_it_and_whether_it_is_current(
    built: Path,
) -> None:
    explanation = explain("artifact:.forge/generated/algo_top.v", built)
    produced = next(s for s in explanation.sections if s.heading == "Produced by")
    currency = next(s for s in explanation.sections if s.heading == "Currency")

    assert produced.lines == ["forge build"]
    assert "current" in currency.lines[0]


def test_an_artifact_older_than_its_sources_is_reported_stale(built: Path) -> None:
    import os

    top = built / ".forge/generated/algo_top.v"
    os.utime(top, (0, 0))

    explanation = explain("artifact:.forge/generated/algo_top.v", built)
    currency = next(s for s in explanation.sections if s.heading == "Currency")

    assert "stale" in currency.lines[0]


def test_a_missing_artifact_is_a_user_input_error(built: Path) -> None:
    with pytest.raises(UnknownTarget):
        explain("artifact:.forge/generated/not-a-file.v", built)


# ── Presentation ──────────────────────────────────────────────────────────

def test_evidence_is_reported_once_however_many_sections_rest_on_it(
    built: Path,
) -> None:
    explanation = explain("shaper.shaped", built)
    rendered = [repr(e.to_dict()) for e in explanation.distinct_evidence]

    assert len(rendered) == len(set(rendered))


def test_a_section_with_nothing_to_say_is_omitted_entirely(adopted: Path) -> None:
    """A report full of empty headings reads as though FORGE knows things it
    does not."""
    explanation = explain("shaper", adopted)

    assert all(section.lines for section in explanation.sections)


# ── The CLI ───────────────────────────────────────────────────────────────

def test_explain_renders_and_exits_clean(
    capsys: pytest.CaptureFixture[str], adopted: Path,
) -> None:
    code, out, _err = _run(capsys, ["explain", "shaper.shaped", "--path", str(adopted)])

    assert code == 0
    assert "shaper.shaped" in out
    assert "Evidence" in out
    assert "Source: canonical IR" in out


def test_explain_json_round_trips_through_the_shared_envelope(
    capsys: pytest.CaptureFixture[str], adopted: Path,
) -> None:
    code, out, _err = _run(
        capsys, ["explain", "clock:clk", "--path", str(adopted), "--json"])
    payload = json.loads(out)

    assert code == 0
    envelope = CommandEnvelope.from_dict(payload)
    assert envelope.status == "pass"
    explanation = payload["metrics"]["explanation"]
    assert explanation["kind"] == KIND_CLOCK
    assert explanation["confidence"] in ("deterministic", "inferred", "heuristic", "unknown")


def test_an_unresolvable_target_exits_one_with_guidance(
    capsys: pytest.CaptureFixture[str], adopted: Path,
) -> None:
    code, _out, err = _run(capsys, ["explain", "nope", "--path", str(adopted)])

    assert code == 1
    assert "no module named" in err


def test_explaining_outside_a_project_says_to_adopt_first(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    code, _out, err = _run(capsys, ["explain", "anything", "--path", str(tmp_path)])

    assert code == 1
    assert "forge adopt" in err


def test_check_points_every_blocker_at_forge_explain(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    from forge.project.adopt import plan_adoption

    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "classifier.v").write_text(
        "module classifier(input clk, output [63:0] muon_out);\nendmodule\n")
    (rtl / "formatter.v").write_text(
        "module formatter(input clk, input [63:0] muon_in);\nendmodule\n")
    (rtl / "debug_sink.v").write_text(
        "module debug_sink(input clk, input [63:0] muon);\nendmodule\n")
    plan_adoption(tmp_path).write()

    code, out, _err = _run(capsys, ["check", str(tmp_path)])

    assert code == 1
    assert "Learn: forge explain ATG037" in out
