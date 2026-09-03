"""Coverage for HLS contract maturity and prediction/synthesis reconciliation
(plan §14, Phase I).

An RTL module's ports are a fact — declared in its source, read by FORGE,
nothing to be uncertain about. An HLS module's are not: until Vitis HLS has
synthesised it, its RTL interface is a *prediction* from the C++ signature
and pragmas. FORGE reported that distinction as one flat warning, which left
the interesting question unanswered: how far along is this module, and where
exactly did the prediction turn out to be wrong?

Two properties are worth defending here:

* **No dependency on the tool.** Maturity is read from what is on disk and
  reconciliation compares two port lists. Vitis HLS is needed to *produce*
  the synthesised side, never to reason about it — so this all runs in CI
  without a vendor toolchain.
* **An unexplained difference beats a wrong explanation.** FORGE attaches a
  cause only where one genuinely applies. A zero-width prediction is the
  predictor saying it could not resolve the type at all, not a width that
  happened to be wrong, and must not be explained as byte-rounding.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from forge.hls.port_prediction import PredictedPort
from forge.project.hls_maturity import (
    INFERRED_FROM_CPP,
    MATURITY_LEVELS,
    PREDICTED,
    RECONCILED,
    SYNTHESIZED,
    VERIFIED,
    assess,
    describe,
    find_component_xml,
    is_at_least,
    reconcile,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _predicted(name: str, direction: str, width: int) -> PredictedPort:
    return PredictedPort(name=name, direction=direction, width=width, role_hint="")


def _synth(name: str, direction: str, width: int) -> dict:
    return {"name": name, "direction": direction, "width": width}


def _plant_built_ip(root: Path, module: str, entity_ports: str) -> Path:
    """A minimal built-IP tree of the shape `parse_component` reads."""
    ip = root / "build" / module / "sol" / "impl" / "ip"
    (ip / "hdl" / "vhdl").mkdir(parents=True)
    (ip / "component.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<spirit:component xmlns:spirit="http://www.spiritconsortium.org/'
        'XMLSchema/SPIRIT/1685-2009">\n'
        "  <spirit:vendor>xilinx.com</spirit:vendor>\n"
        "  <spirit:library>hls</spirit:library>\n"
        f"  <spirit:name>{module}</spirit:name>\n"
        "  <spirit:version>1.0</spirit:version>\n"
        "</spirit:component>\n"
    )
    (ip / "hdl" / "vhdl" / f"{module}.vhd").write_text(
        f"entity {module} is port (\n{entity_ports});\nend;\n"
    )
    return ip / "component.xml"


# ── The ladder ────────────────────────────────────────────────────────────

def test_the_ladder_runs_weakest_to_strongest() -> None:
    assert MATURITY_LEVELS == (
        INFERRED_FROM_CPP, PREDICTED, SYNTHESIZED, RECONCILED, VERIFIED,
    )


@pytest.mark.parametrize("level", MATURITY_LEVELS)
def test_every_level_describes_itself(level: str) -> None:
    assert describe(level) != "unknown maturity"


def test_levels_compare_by_position_not_alphabetically() -> None:
    assert is_at_least(RECONCILED, PREDICTED) is True
    assert is_at_least(PREDICTED, RECONCILED) is False
    assert is_at_least(VERIFIED, INFERRED_FROM_CPP) is True
    assert is_at_least(PREDICTED, PREDICTED) is True


def test_an_unknown_level_is_never_at_least_anything() -> None:
    assert is_at_least("wishful", PREDICTED) is False


# ── Reconciliation ────────────────────────────────────────────────────────

def test_an_exact_match_reports_agreement() -> None:
    predicted = [_predicted("ap_clk", "input", 1), _predicted("data_V", "input", 32)]
    synthesized = [_synth("ap_clk", "in", 1), _synth("data_V", "in", 32)]

    result = reconcile("kernel", predicted, synthesized)

    assert result.agrees
    assert len(result.matched) == 2
    assert result.differences == []
    assert "matches the built IP exactly" in result.report()


def test_the_two_sides_spell_direction_differently_and_still_match() -> None:
    """The predictor says input/output; component.xml says in/out. Comparing
    them raw would report every single port as a direction difference."""
    result = reconcile(
        "kernel", [_predicted("q", "output", 8)], [_synth("q", "out", 8)])

    assert result.agrees


def test_a_width_difference_is_reported_with_both_values() -> None:
    result = reconcile(
        "kernel", [_predicted("foo_V", "input", 32)], [_synth("foo_V", "in", 40)])

    assert not result.agrees
    difference = result.differences_of("width")[0]
    assert (difference.predicted, difference.synthesized) == (32, 40)
    assert "predicted 32 bits, synthesised 40 bits" in difference.describe()


def test_a_byte_rounded_width_is_explained() -> None:
    result = reconcile(
        "kernel", [_predicted("foo_V", "input", 33)], [_synth("foo_V", "in", 40)])

    assert "byte-rounded" in result.differences_of("width")[0].reason


def test_an_unresolvable_width_is_not_explained_as_byte_rounding() -> None:
    """A zero-width prediction is the predictor reporting that it could not
    resolve the type at all. Calling that byte-rounding would be a
    confidently wrong explanation of a difference whose real cause is
    already known."""
    result = reconcile(
        "kernel", [_predicted("foo_V", "input", 0)], [_synth("foo_V", "in", 24)])
    reason = result.differences_of("width")[0].reason

    assert "could not resolve" in reason
    assert "byte-rounded" not in reason


def test_a_handshake_pin_the_predictor_missed_is_explained() -> None:
    result = reconcile(
        "kernel", [_predicted("q_V", "output", 8)],
        [_synth("q_V", "out", 8), _synth("q_V_ap_vld", "out", 1)],
    )
    unexpected = result.differences_of("unexpected")[0]

    assert unexpected.port == "q_V_ap_vld"
    assert "handshake pin" in unexpected.reason


def test_a_difference_with_no_known_cause_is_left_unexplained() -> None:
    """Better than a plausible-sounding invention."""
    result = reconcile(
        "kernel", [_predicted("ghost_V", "input", 8)], [_synth("other", "in", 8)])

    assert result.differences_of("missing")[0].reason == ""


def test_ports_match_by_name_not_position() -> None:
    """A positional match would silently pair unrelated ports the moment HLS
    emits one more pin than predicted — which is the common case."""
    result = reconcile(
        "kernel",
        [_predicted("a", "input", 8), _predicted("b", "input", 16)],
        [_synth("extra", "in", 1), _synth("a", "in", 8), _synth("b", "in", 16)],
    )

    assert sorted(result.matched) == ["a", "b"]
    assert [d.port for d in result.differences_of("unexpected")] == ["extra"]


def test_direction_differences_are_reported_separately_from_widths() -> None:
    result = reconcile(
        "kernel", [_predicted("q", "input", 8)], [_synth("q", "out", 8)])

    assert result.differences_of("width") == []
    assert result.differences_of("direction")[0].predicted == "input"


def test_the_report_matches_the_shape_the_plan_specifies() -> None:
    result = reconcile(
        "regression",
        [_predicted("foo_V", "input", 32), _predicted("gone_V", "input", 8)],
        [_synth("foo_V", "in", 40), _synth("surprise", "out", 1)],
    )
    report = result.report()

    for heading in ("HLS interface reconciliation", "Module", "Predicted ports",
                    "Matched", "Width differences", "Unexpected ports",
                    "Missing ports"):
        assert heading in report
    assert "regression" in report


def test_reconciliation_serialises_for_the_json_envelope() -> None:
    result = reconcile(
        "kernel", [_predicted("foo_V", "input", 32)], [_synth("foo_V", "in", 40)])
    payload = result.to_dict()

    assert payload["module"] == "kernel"
    assert payload["counts"]["width"] == 1
    assert payload["differences"][0]["description"]


# ── Assessing a module ────────────────────────────────────────────────────

def test_a_parsed_source_with_no_prediction_is_at_the_bottom(
    tmp_path: Path,
) -> None:
    source = tmp_path / "kernel.cpp"
    source.write_text("void kernel() {}\n")

    assessment = assess("kernel", source=source)

    assert assessment.level == INFERRED_FROM_CPP


def test_a_prediction_alone_reaches_predicted(tmp_path: Path) -> None:
    source = tmp_path / "kernel.cpp"
    source.write_text("void kernel() {}\n")

    assessment = assess(
        "kernel", source=source, predicted=[_predicted("ap_clk", "input", 1)])

    assert assessment.level == PREDICTED
    assert assessment.reconciliation is None


def test_a_built_ip_with_no_prediction_stops_at_synthesized(
    tmp_path: Path,
) -> None:
    component = _plant_built_ip(tmp_path, "kernel", "    ap_clk : IN STD_LOGIC")

    assessment = assess("kernel", component_xml=component)

    assert assessment.level == SYNTHESIZED
    assert assessment.reconciliation is None


def test_a_prediction_plus_a_built_ip_reconciles(tmp_path: Path) -> None:
    component = _plant_built_ip(
        tmp_path, "kernel",
        "    ap_clk : IN STD_LOGIC;\n"
        "    data_V : IN STD_LOGIC_VECTOR (31 downto 0)")

    assessment = assess(
        "kernel",
        predicted=[_predicted("ap_clk", "input", 1), _predicted("data_V", "input", 32)],
        component_xml=component,
    )

    assert assessment.level == RECONCILED
    assert assessment.reconciliation is not None
    assert assessment.reconciliation.agrees


def test_reconciled_does_not_mean_identical(tmp_path: Path) -> None:
    """A difference FORGE can explain is still reconciled. What the level
    means is that nothing about the interface is unaccounted for."""
    component = _plant_built_ip(
        tmp_path, "kernel", "    data_V : IN STD_LOGIC_VECTOR (39 downto 0)")

    assessment = assess(
        "kernel", predicted=[_predicted("data_V", "input", 33)],
        component_xml=component,
    )

    assert assessment.level == RECONCILED
    assert not assessment.reconciliation.agrees


def test_an_unreadable_built_ip_stays_honestly_at_synthesized(
    tmp_path: Path,
) -> None:
    component = _plant_built_ip(tmp_path, "kernel", "    ap_clk : IN STD_LOGIC")
    component.write_text("not xml at all")

    assessment = assess(
        "kernel", predicted=[_predicted("ap_clk", "input", 1)],
        component_xml=component,
    )

    assert assessment.level == SYNTHESIZED
    assert assessment.reconciliation is None


def test_assessment_carries_the_evidence_behind_the_level(
    tmp_path: Path,
) -> None:
    component = _plant_built_ip(tmp_path, "kernel", "    ap_clk : IN STD_LOGIC")
    source = tmp_path / "kernel.cpp"
    source.write_text("void kernel() {}\n")

    assessment = assess(
        "kernel", source=source, predicted=[_predicted("ap_clk", "input", 1)],
        component_xml=component,
    )
    rules = {e.rule for e in assessment.evidence}

    assert "cpp_source_present" in rules
    assert "ports_predicted_from_cpp" in rules
    assert "built_ip_present" in rules
    assert "synthesized_port_count" in rules


# ── Finding the built IP ──────────────────────────────────────────────────

def test_the_right_modules_component_xml_is_found(tmp_path: Path) -> None:
    """An HLS project emits one component.xml per solution, so an
    unfiltered search in a repository with several modules returns the wrong
    one about as often as the right one."""
    _plant_built_ip(tmp_path, "alpha", "    ap_clk : IN STD_LOGIC")
    _plant_built_ip(tmp_path, "beta", "    ap_clk : IN STD_LOGIC")

    found = find_component_xml(tmp_path, "beta")

    assert found is not None
    assert "beta" in str(found)


def test_no_built_ip_is_not_an_error(tmp_path: Path) -> None:
    """Not having built yet is the normal state of an adopted project."""
    assert find_component_xml(tmp_path, "kernel") is None


# ── Through the commands ──────────────────────────────────────────────────

@pytest.fixture
def hls_project(tmp_path: Path) -> Path:
    """A real HLS kernel in an ordinary repository shape, adopted."""
    from forge.project.adopt import plan_adoption

    project = tmp_path / "hls_project"
    (project / "hls").mkdir(parents=True)
    (project / "rtl").mkdir()
    shutil.copy(
        REPO_ROOT / "plugins/trigger_demo/algo/hit_decoder/hit_decoder.cpp",
        project / "hls" / "hit_decoder.cpp")
    shutil.copytree(
        REPO_ROOT / "plugins/trigger_demo/algo/common", project / "hls" / "common")
    (project / "rtl" / "sink.v").write_text(
        "module sink(input ap_clk, input ap_rst, input [31:0] decoded_hit);\n"
        "endmodule\n")
    plan_adoption(project).write()
    return project


def test_check_reports_a_predicted_kernel_as_not_yet_reconciled(
    hls_project: Path,
) -> None:
    from forge.project.status import PARTIAL, evaluate

    status = evaluate(hls_project)
    section = status.section("HLS interfaces")

    assert section.state == PARTIAL
    assert "predicted only" in section.detail
    assert "ATG043" in [d.code for d in status.warnings]


def test_check_reports_the_differences_once_the_ip_is_built(
    hls_project: Path,
) -> None:
    from forge.project.status import evaluate

    _plant_built_ip(
        hls_project, "hit_decoder",
        "    ap_clk : IN STD_LOGIC;\n"
        "    ap_rst : IN STD_LOGIC;\n"
        "    raw_hit : IN STD_LOGIC_VECTOR (23 downto 0);\n"
        "    raw_valid : IN STD_LOGIC;\n"
        "    decoded_hit : OUT STD_LOGIC_VECTOR (31 downto 0);\n"
        "    decoded_valid : OUT STD_LOGIC")

    status = evaluate(hls_project)
    section = status.section("HLS interfaces")

    assert section.state == "WARN"
    assert "reconciled" in section.detail
    assert "ATG048" in [d.code for d in status.warnings]


def test_explain_shows_the_ladder_and_where_the_module_sits(
    hls_project: Path,
) -> None:
    from forge.project.explain import explain

    explanation = explain("hls:hit_decoder", hls_project)
    maturity = next(s for s in explanation.sections if s.heading == "Maturity")

    assert "predicted" in maturity.lines[0]
    assert "[predicted]" in maturity.lines[1]
    assert "inferred_from_cpp" in maturity.lines[1]


def test_explain_shows_the_reconciliation_once_the_ip_is_built(
    hls_project: Path,
) -> None:
    from forge.project.explain import explain

    _plant_built_ip(
        hls_project, "hit_decoder",
        "    ap_clk : IN STD_LOGIC;\n"
        "    decoded_hit : OUT STD_LOGIC_VECTOR (31 downto 0);\n"
        "    decoded_hit_ap_vld : OUT STD_LOGIC")

    explanation = explain("hls:hit_decoder", hls_project)
    headings = [s.heading for s in explanation.sections]
    differences = next(
        s for s in explanation.sections
        if s.heading == "Where prediction and synthesis differ")

    assert "Reconciliation" in headings
    assert any("decoded_hit_ap_vld" in line for line in differences.lines)
    assert any("handshake pin" in line for line in differences.lines)
