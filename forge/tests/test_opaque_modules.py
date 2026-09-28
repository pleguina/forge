"""Coverage for opaque modules (plan §9, Phase D2) — integrating a block
FORGE deliberately does not model inside.

The escape hatch for vendor IP, encrypted blocks and anything whose internal
structure is outside FORGE's current envelope. Before this existed, the
diagnostic for a multi-clock module offered "import as an opaque module" as
its first suggested way forward and there was no way to act on it; the whole
module was simply left out of the design.

What must hold, and what these tests pin:

* A module the user declares opaque is **in** the design, not excluded.
* Its clock and reset pins other than the design's own reach the generated
  **top level**. Left to the global clock/reset fan-out they would all be
  tied to one `ap_clk`/`ap_rst` net, silently shorting distinct domains
  together — which is precisely why such a module could not be integrated.
* Its clocks are **not** candidates for the design's own functional clock.
  FORGE drives none of them, so asking which one runs the design is
  incoherent.
* The declaration survives re-adoption. It is user intent, and adoption
  regenerates everything *except* what the user decided.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from forge.core.cli.main import build_parser
from forge.project.adopt import plan_adoption
from forge.project.config import MANAGEMENT_OPAQUE, ForgeConfig, ModulePolicy
from forge.project.discovery import KIND_CLOCK, ProjectDiscovery
from forge.project.status import PASS, PARTIAL, evaluate

CORPUS = Path(__file__).resolve().parent / "fixtures" / "foreign"


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
def peripheral(tmp_path: Path) -> Path:
    """Project B, adopted once — its vendor IP still reported as unsupported."""
    destination = tmp_path / "peripheral_subsystem"
    shutil.copytree(CORPUS / "peripheral_subsystem", destination)
    plan_adoption(destination).write()
    return destination


def _declare_opaque(project: Path, module: str) -> None:
    """Do what the diagnostic tells the user to do."""
    config = ForgeConfig.load(project)
    config.modules[module] = ModulePolicy(management=MANAGEMENT_OPAQUE)
    config.save()


# ── The configuration surface ─────────────────────────────────────────────

def test_the_declaration_round_trips_through_forge_yml(tmp_path: Path) -> None:
    ForgeConfig(
        project="p", root=tmp_path,
        modules={"vendor_ip": ModulePolicy(management=MANAGEMENT_OPAQUE)},
    ).save()

    loaded = ForgeConfig.load(tmp_path)

    assert loaded.opaque_modules == frozenset({"vendor_ip"})
    assert loaded.modules["vendor_ip"].opaque is True


def test_a_project_that_declares_nothing_has_nothing_opaque(tmp_path: Path) -> None:
    ForgeConfig(project="p", root=tmp_path).save()

    assert "modules" not in (tmp_path / "forge.yml").read_text()
    assert ForgeConfig.load(tmp_path).opaque_modules == frozenset()


def test_an_unknown_management_kind_is_refused(tmp_path: Path) -> None:
    (tmp_path / "forge.yml").write_text(
        "project: p\nmodules:\n  vendor_ip:\n    management: magic\n")

    with pytest.raises(ValueError, match="management"):
        ForgeConfig.load(tmp_path)


def test_a_malformed_modules_block_is_refused_with_an_example(
    tmp_path: Path,
) -> None:
    (tmp_path / "forge.yml").write_text("project: p\nmodules: [a, b]\n")

    with pytest.raises(ValueError, match="mapping"):
        ForgeConfig.load(tmp_path)


# ── The effect on discovery ───────────────────────────────────────────────

def test_an_undeclared_multi_clock_module_is_still_left_out(
    peripheral: Path,
) -> None:
    """The behaviour opaque modules are the answer *to*, unchanged."""
    result = ProjectDiscovery().scan(peripheral)

    assert "axi_bridge" not in {u.name for u in result.managed_units}
    assert len(result.unsupported) == 1


def test_the_diagnostic_tells_the_user_exactly_what_to_write(
    peripheral: Path,
) -> None:
    """A suggested way forward nobody can act on is not a way forward."""
    result = ProjectDiscovery().scan(peripheral)
    unsupported = result.unsupported[0]

    assert any("management: opaque" in option for option in unsupported.options)
    assert "management: opaque" in unsupported.action.description
    assert "forge adopt" in unsupported.action.description


def test_a_declared_module_joins_the_design(peripheral: Path) -> None:
    result = ProjectDiscovery(opaque_modules={"axi_bridge"}).scan(peripheral)

    assert "axi_bridge" in {u.name for u in result.managed_units}
    assert [u.name for u in result.opaque_units] == ["axi_bridge"]
    assert result.unsupported == []


def test_the_reason_it_is_opaque_is_still_recorded(peripheral: Path) -> None:
    """Declaring it opaque answers the question; it does not erase it."""
    result = ProjectDiscovery(opaque_modules={"axi_bridge"}).scan(peripheral)
    unit = result.opaque_units[0]

    assert unit.opaque is True
    assert any("3 clock inputs" in reason for reason in unit.unsupported)


def test_an_opaque_modules_clocks_are_not_candidates_for_the_design_clock(
    peripheral: Path,
) -> None:
    """FORGE drives none of them, so asking which one runs the design is
    incoherent."""
    result = ProjectDiscovery(opaque_modules={"axi_bridge"}).scan(peripheral)

    assert {c.name for c in result.managed_clocks} == {"pclk"}
    assert result.ambiguities_of(KIND_CLOCK) == []
    # Still reported, though — they are real.
    assert "s_axi_aclk" in {c.name for c in result.clocks}


# ── The effect on adoption ────────────────────────────────────────────────

def test_adoption_exposes_the_extra_domains_at_the_top_level(
    peripheral: Path,
) -> None:
    import yaml

    _declare_opaque(peripheral, "axi_bridge")
    plan_adoption(peripheral).write()

    design = yaml.safe_load((peripheral / ".forge/project/design.yml").read_text())
    entry = next(m for m in design["modules"] if m["name"] == "axi_bridge")

    for clock in ("s_axi_aclk", "m_axi_aclk", "ref_clk"):
        assert clock in entry["external_in_ports"]
    assert "s_axi_aresetn" in entry["external_in_ports"]


def test_the_designs_own_clock_is_not_exposed_for_an_opaque_module(
    tmp_path: Path,
) -> None:
    """Only the domains FORGE does *not* drive go to the top level; the
    design's own clock is still fanned out normally."""
    import yaml

    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "core.v").write_text(
        "module core(input pclk, input presetn, output [7:0] q);\nendmodule\n")
    (rtl / "vendor.v").write_text(
        "module vendor(input pclk, input alt_clk, input [7:0] d);\nendmodule\n")
    plan_adoption(tmp_path).write()
    _declare_opaque(tmp_path, "vendor")
    plan_adoption(tmp_path).write()

    design = yaml.safe_load((tmp_path / ".forge/project/design.yml").read_text())
    entry = next(m for m in design["modules"] if m["name"] == "vendor")

    assert "alt_clk" in entry["external_in_ports"]
    assert "pclk" not in entry["external_in_ports"]


def test_the_declaration_survives_re_adoption(peripheral: Path) -> None:
    """Adoption regenerates everything except what the user decided."""
    _declare_opaque(peripheral, "axi_bridge")

    plan_adoption(peripheral).write()
    plan = plan_adoption(peripheral)

    assert plan.config.opaque_modules == frozenset({"axi_bridge"})
    assert [u.name for u in plan.discovery.opaque_units] == ["axi_bridge"]


def test_the_registry_says_the_module_is_deliberately_unmodelled(
    peripheral: Path,
) -> None:
    """A reader of the generated registry would otherwise have no way to
    tell that FORGE is not modelling this module on purpose."""
    _declare_opaque(peripheral, "axi_bridge")
    plan_adoption(peripheral).write()

    registry = (peripheral / ".forge/project/modules.yml").read_text()
    assert "opaque: integrated structurally" in registry


def test_the_decision_log_records_who_decided(peripheral: Path) -> None:
    _declare_opaque(peripheral, "axi_bridge")
    plan = plan_adoption(peripheral)

    declared = [e for e in plan.decisions if e.rule == "declared_opaque_by_user"]
    assert len(declared) == 1
    assert declared[0].confidence == "deterministic"


# ── The effect on generation ──────────────────────────────────────────────

def test_the_generated_top_keeps_the_domains_separate(
    peripheral: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """The whole point. Tying three functional clocks to one ap_clk net
    would elaborate cleanly and be silently, catastrophically wrong."""
    _declare_opaque(peripheral, "axi_bridge")
    plan_adoption(peripheral).write()

    code, _out, err = _run(capsys, [
        "build", str(peripheral / ".forge/project/design.yml"),
        "--contracts-from", str(peripheral / ".forge/project/modules.yml"),
        "--consumer-root", str(peripheral),
        "--output", str(peripheral / ".forge/generated/algo_top.v"), "--apply",
    ])
    assert code == 0, err
    generated = (peripheral / ".forge/generated/algo_top.v").read_text()

    for clock in ("s_axi_aclk", "m_axi_aclk", "ref_clk"):
        assert f"input axi_bridge_{clock}" in generated
        assert f".{clock}(axi_bridge_{clock})" in generated
        assert f".{clock}(ap_clk)" not in generated

    # The modules FORGE *does* model still take the global nets.
    assert ".pclk(ap_clk)" in generated
    assert ".presetn(ap_rst)" in generated


def test_an_explicit_external_declaration_beats_the_clock_auto_map(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """The generator change opaque modules rest on, stated on its own: an
    explicit `external_in_ports` entry must win over an implicit
    name-convention auto-map, and the port declaration and the instance
    connection must agree — a port declared by one loop and not connected by
    the other is a top level that does not elaborate."""
    import yaml

    rtl = tmp_path / "rtl"
    rtl.mkdir()
    # A second *reset* rather than a second clock: the point here is the
    # generator's precedence rule, and a two-clock module would also trip
    # the one-functional-clock-per-module envelope check and never reach a
    # generated design at all.
    (rtl / "core.v").write_text(
        "module core(input clk, input rst, input aux_rst, output [7:0] q);\nendmodule\n")
    plan = plan_adoption(tmp_path)
    plan.write()

    design_path = tmp_path / ".forge/project/design.yml"
    design = yaml.safe_load(design_path.read_text())
    design["modules"][0]["external_in_ports"] = ["aux_rst"]
    design_path.write_text(yaml.safe_dump(design, sort_keys=False))

    code, _out, err = _run(capsys, [
        "build", str(design_path),
        "--contracts-from", str(tmp_path / ".forge/project/modules.yml"),
        "--consumer-root", str(tmp_path),
        "--output", str(tmp_path / ".forge/generated/algo_top.v"), "--apply",
    ])
    assert code == 0, err
    generated = (tmp_path / ".forge/generated/algo_top.v").read_text()

    assert "input core_aux_rst" in generated
    assert ".aux_rst(core_aux_rst)" in generated
    # The undeclared clock and reset still take the global nets.
    assert ".clk(ap_clk)" in generated
    assert ".rst(ap_rst)" in generated


# ── The effect on the health report ───────────────────────────────────────

def test_check_reports_an_open_question_before_the_decision(
    peripheral: Path,
) -> None:
    status = evaluate(peripheral)
    section = status.section("Supported constructs")

    assert section.state == PARTIAL
    assert "axi_bridge" in section.detail
    assert "ATG042" in [d.code for d in status.warnings]


def test_check_reports_a_settled_decision_as_settled(peripheral: Path) -> None:
    _declare_opaque(peripheral, "axi_bridge")
    plan_adoption(peripheral).write()

    status = evaluate(peripheral)
    section = status.section("Supported constructs")

    assert section.state == PASS
    assert "1 opaque: axi_bridge" == section.detail
    assert "ATG042" not in [d.code for d in status.warnings]


def test_explain_reports_a_module_as_opaque(peripheral: Path) -> None:
    from forge.project.explain import explain

    _declare_opaque(peripheral, "axi_bridge")
    plan_adoption(peripheral).write()

    explanation = explain("axi_bridge", peripheral)
    headings = [s.heading for s in explanation.sections]

    assert "Source" in headings
    assert "Ports" in headings
