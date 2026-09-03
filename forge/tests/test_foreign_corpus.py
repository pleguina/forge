"""The foreign-project corpus (plan §10, Phase E1) — adoption tested against
repositories that were not architected around FORGE.

Three projects, each covering something the others do not:

* **A, `simple_pipeline`** — a small DSP chain. Straight-line topology, one
  clock, an active-low reset. The baseline: this one must reach a real
  generated top level with no hand-written YAML at all.
* **B, `peripheral_subsystem`** — an ordinary peripheral repository.
  Several source directories, a Makefile and a Vivado TCL script, a
  VHDL/Verilog mix, conventional bus naming (`pclk`, `presetn`,
  `s_axi_aclk`), and a vendor IP with three functional clocks.
* **C, `daq_readout`** — a readout chain. An existing structural top with
  four instances of one module, fan-in with per-channel array naming,
  fan-out to two consumers, and a second clock domain.

The point of the corpus is that these were written to be *adopted*, not to
be adopted *easily*. Project B found four real bugs on its first run — bus
clock and reset names (`pclk`, `presetn`, `aresetn`) went unrecognised,
which cascaded into a three-clock vendor IP reading as single-clock and
being silently integrated instead of reported as outside FORGE's envelope.
Those regressions are pinned here.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from forge.core.cli.main import build_parser
from forge.project.adopt import plan_adoption
from forge.project.discovery import (
    AMBIGUOUS,
    KIND_CLOCK,
    KIND_CONNECTION,
    UNSUPPORTED,
    ProjectDiscovery,
)
from forge.project.status import evaluate

CORPUS = Path(__file__).resolve().parent / "fixtures" / "foreign"
PROJECTS = ("simple_pipeline", "peripheral_subsystem", "daq_readout")


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


def _copy(name: str, tmp_path: Path) -> Path:
    destination = tmp_path / name
    shutil.copytree(CORPUS / name, destination)
    return destination


# ── Properties every project in the corpus must have ──────────────────────

@pytest.mark.parametrize("name", PROJECTS)
def test_every_corpus_project_is_free_of_forge_configuration(name: str) -> None:
    """The moment a fixture is given a forge.yml it stops testing adoption
    and starts testing a project that was already adopted."""
    project = CORPUS / name

    assert not (project / "forge.yml").exists()
    assert not (project / ".forge").exists()
    assert not list(project.rglob("modules.yml"))
    assert not list(project.rglob("*.interface.yaml"))


@pytest.mark.parametrize("name", PROJECTS)
def test_every_corpus_project_adopts_without_hand_written_yaml(
    name: str, tmp_path: Path,
) -> None:
    project = _copy(name, tmp_path)

    plan = plan_adoption(project)
    plan.write()

    assert (project / "forge.yml").is_file()
    assert (project / ".forge/project/design.yml").is_file()
    assert (project / ".forge/project/modules.yml").is_file()
    assert plan.discovery.managed_units


@pytest.mark.parametrize("name", PROJECTS)
def test_every_corpus_project_leaves_the_users_tree_untouched(
    name: str, tmp_path: Path,
) -> None:
    project = _copy(name, tmp_path)
    before = {
        p.relative_to(project): p.read_bytes()
        for p in sorted(project.rglob("*")) if p.is_file()
    }

    plan_adoption(project).write()

    after = {
        p.relative_to(project): p.read_bytes()
        for p in sorted(project.rglob("*"))
        if p.is_file() and ".forge" not in p.parts and p.name != "forge.yml"
    }
    assert after == before


@pytest.mark.parametrize("name", PROJECTS)
def test_every_corpus_project_produces_a_design_the_validator_accepts(
    name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Adoption must emit ordinary FORGE configuration, not a dialect only
    the new commands understand."""
    project = _copy(name, tmp_path)
    plan_adoption(project).write()

    code, _out, _err = _run(
        capsys, ["topgen", "validate", str(project / ".forge/project/design.yml")])

    assert code == 0


@pytest.mark.parametrize("name", PROJECTS)
def test_adoption_is_deterministic(name: str, tmp_path: Path) -> None:
    """Byte-stable output across runs — the plan's reproducibility
    requirement, and what makes `.forge/` safe to regenerate."""
    first = _copy(name, tmp_path / "a")
    second = _copy(name, tmp_path / "b")

    plan_adoption(first, project_name="fixed").write()
    plan_adoption(second, project_name="fixed").write()

    for relative in ("forge.yml", ".forge/project/design.yml", ".forge/project/modules.yml"):
        assert (first / relative).read_text() == (second / relative).read_text()


# ── Project A: the baseline ───────────────────────────────────────────────

def test_project_a_reaches_a_generated_top_level(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    project = _copy("simple_pipeline", tmp_path)
    plan_adoption(project).write()

    code, _out, err = _run(capsys, [
        "build", str(project / ".forge/project/design.yml"),
        "--contracts-from", str(project / ".forge/project/modules.yml"),
        "--consumer-root", str(project),
        "--output", str(project / ".forge/generated/algo_top.v"), "--apply",
    ])

    assert code == 0, err
    generated = (project / ".forge/generated/algo_top.v").read_text()
    for module in ("decimator", "shaper", "packer"):
        assert f"  {module} {module} (" in generated


# ── Project B: an ordinary peripheral repository ──────────────────────────

@pytest.fixture
def peripheral(tmp_path: Path) -> Path:
    return _copy("peripheral_subsystem", tmp_path)


def test_sources_are_found_across_several_directories(peripheral: Path) -> None:
    result = ProjectDiscovery().scan(peripheral)
    names = {unit.name for unit in result.units}

    assert names == {"regfile", "spi_master", "status_mux", "axi_bridge"}


def test_each_source_directory_is_named_rather_than_globbed_wholesale(
    peripheral: Path,
) -> None:
    """A repository-wide `**` would silently pull an unrelated file added
    later into the design."""
    plan = plan_adoption(peripheral)

    assert sorted(plan.config.rtl_globs) == [
        "src/common/*.vhd", "src/rtl/*.v", "src/vendor/*.v",
    ]


def test_a_vhdl_entity_and_a_verilog_module_land_in_one_registry(
    peripheral: Path,
) -> None:
    plan = plan_adoption(peripheral)
    plan.write()
    registry = (peripheral / ".forge/project/modules.yml").read_text()

    assert "rtl_lang: vhdl" in registry
    assert "rtl_lang: verilog" in registry
    assert "name: status_mux" in registry


def test_build_files_are_not_mistaken_for_design_sources(peripheral: Path) -> None:
    result = ProjectDiscovery().scan(peripheral)
    build = {f.path.name for f in result.files_by_role("build")}

    assert {"Makefile", "build.tcl"} <= build
    assert not any(f.path.name == "Makefile" for f in result.files_by_role("rtl"))


def test_bus_convention_clock_and_reset_names_are_recognised(
    peripheral: Path,
) -> None:
    """The regression Project B found on its first run: `pclk` and `presetn`
    have no `clk`/`rst` as a separate `_`-delimited part, so a whole-part
    comparison saw no clock or reset anywhere in the project."""
    result = ProjectDiscovery().scan(peripheral)

    assert "pclk" in {c.name for c in result.clocks}
    assert "presetn" in {r.name for r in result.resets}
    assert "s_axi_aclk" in {c.name for c in result.clocks}


def test_an_axi_style_reset_is_read_as_active_low(peripheral: Path) -> None:
    result = ProjectDiscovery().scan(peripheral)
    resets = {r.name: r.active_low for r in result.resets}

    assert resets["presetn"] is True
    assert resets["s_axi_aresetn"] is True


def test_a_multi_clock_vendor_ip_is_excluded_and_reported(
    peripheral: Path,
) -> None:
    """The cascade the naming bug caused: with only one of its three clocks
    recognised, `axi_bridge` read as single-clock and was integrated
    silently. It must be reported and left out instead."""
    plan = plan_adoption(peripheral)
    plan.write()

    assert "axi_bridge" not in {u.name for u in plan.discovery.managed_units}
    assert plan.skipped_modules == ["axi_bridge"]
    assert not (peripheral / ".forge/contracts/axi_bridge.interface.yaml").exists()

    unsupported = plan.discovery.unsupported
    assert len(unsupported) == 1
    assert unsupported[0].classification == UNSUPPORTED
    assert "3 clock inputs" in unsupported[0].question
    assert len(unsupported[0].options) == 3


def test_an_excluded_modules_clocks_do_not_configure_the_design(
    peripheral: Path,
) -> None:
    """`s_axi_aclk` and `m_axi_aclk` exist only on the module FORGE just
    excluded, so neither may become the managed design's clock, and neither
    may raise a question about it."""
    plan = plan_adoption(peripheral)

    assert plan.config.clock.port == "pclk"
    assert plan.config.reset.port == "presetn"
    assert plan.config.reset.active == "low"
    assert not plan.discovery.ambiguities_of(KIND_CLOCK)


def test_the_peripheral_chain_is_wired_and_builds(
    peripheral: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    plan = plan_adoption(peripheral)
    plan.write()
    edges = {(c.producer, c.consumer) for c in plan.discovery.connections}

    assert ("regfile.ctrl_word", "spi_master.ctrl_word") in edges
    assert ("spi_master.status_word", "status_mux.status_word") in edges

    code, _out, err = _run(capsys, [
        "build", str(peripheral / ".forge/project/design.yml"),
        "--contracts-from", str(peripheral / ".forge/project/modules.yml"),
        "--consumer-root", str(peripheral),
        "--output", str(peripheral / ".forge/generated/algo_top.v"), "--apply",
    ])

    assert code == 0, err
    generated = (peripheral / ".forge/generated/algo_top.v").read_text()
    assert "axi_bridge" not in generated


# ── Project C: a readout chain ────────────────────────────────────────────

@pytest.fixture
def daq(tmp_path: Path) -> Path:
    return _copy("daq_readout", tmp_path)


def test_the_existing_structural_top_is_recognised_and_replaced(daq: Path) -> None:
    result = ProjectDiscovery().scan(daq)

    assert result.structural_top == "readout_top"
    assert "readout_top" not in {u.name for u in result.managed_units}
    assert len(result.managed_units) == 5


def test_instance_counts_are_read_off_the_existing_top(daq: Path) -> None:
    """How many instances a design contains is a fact its own top level
    proves — the alternative, defaulting everything to 1, silently drops
    three quarters of this design."""
    plan = plan_adoption(daq)
    plan.write()

    assert plan.discovery.instance_counts["channel_decoder"] == 4
    assert plan.discovery.instance_counts["hit_aggregator"] == 1

    design = (daq / ".forge/project/design.yml").read_text()
    assert "  ref: channel_decoder\n  instances: 4\n" in design

    proven = [
        e for e in plan.decisions if e.rule == "instances_counted_in_top_level"
    ]
    assert proven and all(e.confidence == "deterministic" for e in proven)


def test_a_per_channel_array_binding_is_never_invented(daq: Path) -> None:
    """`ch0_hit`..`ch3_hit` fed by four decoders' scalar `hit` is an array
    binding — a modelling decision. Inventing it would wire one channel's
    data to all four."""
    result = ProjectDiscovery().scan(daq)
    consumers = {c.consumer for c in result.connections}

    assert not any(consumer.startswith("hit_aggregator.ch") for consumer in consumers)
    for index in range(4):
        assert f"hit_aggregator.ch{index}_hit" in result.external_inputs


def test_fan_out_to_two_consumers_is_reported_not_resolved(daq: Path) -> None:
    result = ProjectDiscovery().scan(daq)
    questions = {
        a.subject: set(a.options)
        for a in result.ambiguities_of(KIND_CONNECTION)
        if a.classification == AMBIGUOUS
    }

    assert questions["hit_aggregator.event_word"] == {
        "monitor_path.event_word", "trigger_path.event_word",
    }
    assert not any(
        c.producer == "hit_aggregator.event_word" for c in result.connections
    )


def test_the_unambiguous_link_stage_is_still_wired(daq: Path) -> None:
    """An ambiguity elsewhere must not stop FORGE resolving what is clear."""
    result = ProjectDiscovery().scan(daq)
    edges = {(c.producer, c.consumer) for c in result.connections}

    assert ("trigger_path.trigger_word", "readout_link.trigger_word") in edges


def test_a_second_clock_domain_is_reported_as_a_question(daq: Path) -> None:
    result = ProjectDiscovery().scan(daq)
    questions = result.ambiguities_of(KIND_CLOCK)

    assert len(questions) == 1
    assert set(questions[0].options) == {"sys_clk", "link_clk"}


def test_check_blocks_on_the_fan_out_and_names_the_candidates(
    daq: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    plan_adoption(daq).write()

    status = evaluate(daq)
    code, out, _err = _run(capsys, ["check", str(daq)])

    assert status.status == "fail"
    assert code == 1
    assert "ATG037" in out
    assert "trigger_path.event_word" in out
    assert "monitor_path.event_word" in out
