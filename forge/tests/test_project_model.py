"""Coverage for the shared project-state model: `ProjectPaths`, `Evidence`,
`Action`, `ForgeConfig` and `ProjectStatus`.

The plan puts these first for a reason — every new-user command is a view
over them — so these tests pin the properties the commands depend on rather
than the prose they render: that a chain of evidence is never reported as
stronger than its weakest link, that only proven facts are safe to write,
that both project layouts resolve, and that every blocking diagnostic
arrives with a step that clears it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.project.actions import Action, dedupe, render_all
from forge.project.config import (
    DEFAULT_PART,
    ClockSpec,
    ForgeConfig,
    ResetSpec,
    parse_frequency_to_period_ns,
)
from forge.project.evidence import (
    DETERMINISTIC,
    HEURISTIC,
    INFERRED,
    UNKNOWN,
    Evidence,
    is_safe_to_write,
    weakest_confidence,
)
from forge.project.paths import ProjectPaths, find_project_root
from forge.project.status import FAIL, NOT_CONFIGURED, PASS, evaluate

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "foreign" / "simple_pipeline"


# ── Evidence ──────────────────────────────────────────────────────────────

def test_a_conclusion_is_only_as_strong_as_its_weakest_step() -> None:
    chain = [
        Evidence("a.sv", "port_width", 32, DETERMINISTIC),
        Evidence("a.sv", "name_convention", "clk", HEURISTIC),
    ]

    assert weakest_confidence(chain) == HEURISTIC
    assert is_safe_to_write(chain) is False


def test_no_evidence_is_not_good_evidence() -> None:
    assert weakest_confidence([]) == UNKNOWN
    assert is_safe_to_write([]) is False


def test_only_proven_facts_are_safe_to_write() -> None:
    assert is_safe_to_write([Evidence("a.sv", "port_width", 32, DETERMINISTIC)]) is True
    assert is_safe_to_write([Evidence("a.sv", "guess", "x", INFERRED)]) is False


def test_evidence_rejects_an_unknown_confidence_level() -> None:
    with pytest.raises(ValueError):
        Evidence("a.sv", "rule", 1, "probably")


def test_evidence_round_trips() -> None:
    original = Evidence("a.sv", "port_width", 32, DETERMINISTIC)
    assert Evidence.from_dict(original.to_dict()) == original


# ── Action ────────────────────────────────────────────────────────────────

def test_an_action_degrades_to_the_string_the_envelope_carries() -> None:
    with_command = Action("build", "Generate the top level", command="forge build x.yml")
    without = Action("review", "Review the draft contracts")

    assert with_command.render() == "Generate the top level — run: forge build x.yml"
    assert without.render() == "Review the draft contracts"


def test_the_same_remedy_is_never_recommended_twice() -> None:
    actions = [Action("a", "first"), Action("a", "duplicate"), Action("b", "second")]

    assert [a.description for a in dedupe(actions)] == ["first", "second"]
    assert render_all(actions) == ["first", "second"]


# ── ProjectPaths ──────────────────────────────────────────────────────────

def test_the_forge_yml_layout_confines_everything_generated_to_one_directory() -> None:
    paths = ProjectPaths.for_root("/tmp/proj", source_roots=["rtl"])

    for generated in (
        paths.project_config_root, paths.contract_root, paths.generated_root,
        paths.ir_root, paths.report_root, paths.cache_root, paths.provenance_root,
    ):
        assert str(generated).startswith("/tmp/proj/.forge/")
    assert paths.source_roots == (Path("/tmp/proj/rtl"),)


def test_the_legacy_plugin_layout_still_resolves_to_the_files_init_creates() -> None:
    paths = ProjectPaths.for_legacy_plugin("/repo/plugins", "demo")

    assert paths.design_yml == Path("/repo/plugins/demo/forge/designs/design.yml")
    assert paths.modules_yml == Path("/repo/plugins/demo/forge/modules.yml")
    assert paths.contract_root == Path("/repo/plugins/demo/forge/interfaces")
    assert paths.generated_root == Path("/repo/gen-top/design_demo")


def test_paths_are_reported_relative_to_the_project_so_they_stay_clonable() -> None:
    paths = ProjectPaths.for_root("/tmp/proj")

    assert paths.relative("/tmp/proj/rtl/a.v") == "rtl/a.v"
    assert paths.relative("/elsewhere/b.v") == "/elsewhere/b.v"


def test_the_project_root_is_found_from_any_subdirectory(tmp_path: Path) -> None:
    (tmp_path / "forge.yml").write_text("project: x\n")
    deep = tmp_path / "rtl" / "core"
    deep.mkdir(parents=True)

    assert find_project_root(deep) == tmp_path.resolve()
    assert find_project_root(tmp_path.parent) != tmp_path.resolve()


def test_constructing_paths_touches_no_filesystem(tmp_path: Path) -> None:
    paths = ProjectPaths.for_root(tmp_path)

    assert not paths.state_root.exists()
    paths.ensure()
    assert paths.contract_root.is_dir()


# ── ForgeConfig ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text,expected",
    [("360MHz", 2.777778), ("1GHz", 1.0), ("250 mhz", 4.0), ("500kHz", 2000.0)],
)
def test_frequencies_convert_to_a_clock_period(text: str, expected: float) -> None:
    assert parse_frequency_to_period_ns(text) == pytest.approx(expected)


def test_an_unreadable_frequency_is_refused_rather_than_defaulted() -> None:
    """A mistyped frequency that quietly became the default would be a timing
    bug that never announces itself."""
    with pytest.raises(ValueError):
        parse_frequency_to_period_ns("fast")


def test_frequency_wins_over_a_stale_period() -> None:
    clock = ClockSpec(port="clk", frequency="500MHz", period_ns=99.0)
    assert clock.resolved_period_ns() == pytest.approx(2.0)


def test_forge_yml_round_trips(tmp_path: Path) -> None:
    original = ForgeConfig(
        project="demo", root=tmp_path, rtl_globs=["rtl/*.v"], hls_globs=["hls/*.cpp"],
        top="demo_top", clock=ClockSpec("clk", frequency="360MHz"),
        reset=ResetSpec("rst_n", "low"), part="xcu250", dataset="data/events.xml",
    )
    original.save()

    loaded = ForgeConfig.load(tmp_path)

    assert loaded.project == "demo"
    assert loaded.rtl_globs == ["rtl/*.v"]
    assert loaded.top == "demo_top"
    assert loaded.clock.frequency == "360MHz"
    assert loaded.reset.active_low is True
    assert loaded.part == "xcu250"
    assert loaded.dataset == "data/events.xml"


def test_an_absent_verification_block_means_not_configured_not_broken(tmp_path: Path) -> None:
    ForgeConfig(project="demo", root=tmp_path).save()

    assert "verification" not in (tmp_path / "forge.yml").read_text()
    assert ForgeConfig.load(tmp_path).dataset is None


def test_a_missing_forge_yml_is_a_user_input_error(tmp_path: Path) -> None:
    """FileNotFoundError and ValueError are what
    `envelope.status_for_exception` classifies as exit 1 — the user's file is
    wrong — rather than exit 2, FORGE falling over."""
    with pytest.raises(FileNotFoundError):
        ForgeConfig.load(tmp_path)

    (tmp_path / "forge.yml").write_text("- not a mapping\n")
    with pytest.raises(ValueError):
        ForgeConfig.load(tmp_path)


def test_overlapping_globs_never_list_a_source_twice(tmp_path: Path) -> None:
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "a.v").write_text("module a(input clk);\nendmodule\n")
    config = ForgeConfig(
        project="demo", root=tmp_path, rtl_globs=["rtl/*.v", "rtl/a.v", "**/*.v"])

    assert config.resolve_sources("rtl") == [tmp_path / "rtl" / "a.v"]


# ── ProjectStatus ─────────────────────────────────────────────────────────

def test_a_directory_that_is_not_a_project_says_so_and_recommends_adopt(
    tmp_path: Path,
) -> None:
    status = evaluate(tmp_path)

    assert status.status == "fail"
    assert status.section("Project configuration").state == NOT_CONFIGURED
    assert status.recommended_action.id == "adopt-project"
    assert "forge adopt" in status.recommended_action.command


def test_a_broken_forge_yml_fails_without_pretending_to_check_anything_else(
    tmp_path: Path,
) -> None:
    (tmp_path / "forge.yml").write_text("sources: [not, a, mapping]\n")

    status = evaluate(tmp_path)

    assert status.status == "fail"
    assert [d.code for d in status.blockers] == ["ATG030"]
    assert len(status.sections) == 1


def test_every_blocking_diagnostic_arrives_with_a_step_that_clears_it(
    tmp_path: Path,
) -> None:
    """The plan's stated acceptance criterion for `forge check`."""
    from forge.project.adopt import plan_adoption

    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "classifier.v").write_text(
        "module classifier(input clk, output [63:0] candidate_out);\nendmodule\n")
    (rtl / "formatter.v").write_text(
        "module formatter(input clk, input [63:0] candidate_in);\nendmodule\n")
    (rtl / "sink.v").write_text(
        "module sink(input clk, input [63:0] candidate);\nendmodule\n")
    plan_adoption(tmp_path).write()

    status = evaluate(tmp_path)

    assert status.blockers, "expected the ambiguous consumer to block"
    for diagnostic in status.blockers:
        assert diagnostic.action, f"{diagnostic.code} has no next action"
    assert status.actions


def test_completion_rises_as_the_project_is_completed(tmp_path: Path) -> None:
    from forge.project.adopt import plan_adoption
    import shutil

    shutil.copytree(FIXTURE, tmp_path / "p")
    plan_adoption(tmp_path / "p").write()

    before = evaluate(tmp_path / "p")
    config = ForgeConfig.load(tmp_path / "p")
    dataset = tmp_path / "p" / "data" / "events.xml"
    dataset.parent.mkdir()
    dataset.write_text("<events/>\n")
    config.dataset = "data/events.xml"
    config.save()
    after = evaluate(tmp_path / "p")

    assert after.completion > before.completion


def test_the_recommended_step_is_a_runnable_command_before_a_judgement_call(
    tmp_path: Path,
) -> None:
    """Check order is dependency order, which is the wrong order to
    *recommend* work in: a draft contract does not block generation, so
    "generate the top level" must come before "review the contracts"."""
    from forge.project.adopt import plan_adoption
    import shutil

    shutil.copytree(FIXTURE, tmp_path / "p")
    plan_adoption(tmp_path / "p").write()

    status = evaluate(tmp_path / "p")

    assert status.recommended_action.id == "build-design"
    assert status.recommended_action.command.startswith("forge build ")


def test_a_generated_top_level_older_than_its_sources_is_reported_stale(
    tmp_path: Path,
) -> None:
    import os
    import shutil
    from forge.project.adopt import plan_adoption

    shutil.copytree(FIXTURE, tmp_path / "p")
    plan = plan_adoption(tmp_path / "p")
    plan.write()
    top = plan.paths.generated_root / "algo_top.v"
    top.parent.mkdir(parents=True, exist_ok=True)
    top.write_text("// generated\n")
    os.utime(top, (0, 0))

    status = evaluate(tmp_path / "p")

    assert status.section("Generated artifacts").state == "STALE"
    assert "ATG046" in [d.code for d in status.warnings]


def test_a_contract_naming_a_port_the_module_does_not_have_is_a_blocker(
    tmp_path: Path,
) -> None:
    import shutil
    from forge.project.adopt import plan_adoption

    shutil.copytree(FIXTURE, tmp_path / "p")
    plan = plan_adoption(tmp_path / "p")
    plan.write()
    contract = plan.paths.contract_root / "shaper.interface.yaml"
    contract.write_text(contract.read_text() + "\n    ghost:\n      raw_port: not_a_port\n")

    status = evaluate(tmp_path / "p")

    assert "ATG036" in [d.code for d in status.blockers]
    assert status.section("Contracts").state == FAIL


def test_a_project_with_nothing_outstanding_renders_without_an_action(
    tmp_path: Path, capsys,
) -> None:
    """Every real project this suite builds still has something outstanding
    (an active-low reset, an unconfigured flow), so the "nothing left to do"
    branch of both renderers would otherwise never be exercised — and a
    crash there would only ever be found by the one user who got there."""
    from forge.core.cli.groups import check as check_cmd
    from forge.core.cli.groups import next_step
    from forge.project.status import ProjectStatus, StatusSection

    status = ProjectStatus(root=tmp_path)
    status.sections = [StatusSection("Sources", PASS, "3 RTL, 0 HLS, 3 modules")]

    assert status.completion == 1.0
    assert status.maturity == "complete"
    assert status.recommended_action is None

    check_cmd._render(status)
    next_step._render(status)
    out = capsys.readouterr().out

    assert "Nothing outstanding" in out
    assert "Nothing — every check passes." in out


def test_a_dataset_with_no_flow_still_recommends_something(tmp_path: Path) -> None:
    import shutil
    from forge.project.adopt import plan_adoption

    shutil.copytree(FIXTURE, tmp_path / "p")
    plan_adoption(tmp_path / "p").write()
    config = ForgeConfig.load(tmp_path / "p")
    dataset = tmp_path / "p" / "data" / "events.xml"
    dataset.parent.mkdir()
    dataset.write_text("<events/>\n")
    config.dataset = "data/events.xml"
    config.save()

    status = evaluate(tmp_path / "p")

    assert status.section("Verification").state == "PARTIAL"
    assert any(a.id == "create-verification-flow" for a in status.actions)
