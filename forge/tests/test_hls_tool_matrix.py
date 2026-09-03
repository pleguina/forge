"""
Per-Vitis-version validation of the HLS port predictor — Phase I3.

`test_hls_port_prediction.py` checks the predictor against the reference
version's output, case by case, with the reasoning in each test. This file
checks the other property: that "validated against Vitis HLS X" is a claim
with evidence behind it, and that a version nobody has tested is reported as
untested rather than assumed to work.
"""

from __future__ import annotations

import json

import pytest

from forge.hls import tool_matrix
from forge.hls.tool_matrix import (
    DIVERGENT,
    UNTESTED,
    VERIFIED,
    cases,
    golden_ports,
    known_versions,
    status_for,
    support_matrix,
    validate_version,
)


def test_the_corpus_has_recorded_output_for_at_least_one_version():
    versions = known_versions()
    assert versions, "no golden HLS output — the predictor is validated against nothing"
    assert all(v.replace(".", "").isdigit() for v in versions), versions


@pytest.mark.parametrize("version", known_versions())
def test_every_recorded_version_reproduces_with_nothing_unexplained(version):
    """The suite the plan asks for: C++ source, expected synthesised
    interface, predicted interface, difference classification — replayed for
    each supported version."""
    report = validate_version(version)

    assert report.cases, f"no cases replayed for {version}"
    assert report.status == VERIFIED, report.report()
    assert report.unexplained == []


@pytest.mark.parametrize("version", known_versions())
def test_every_case_in_the_corpus_is_replayed(version):
    """A golden kernel with no entry in cases.json would silently shrink
    what a "verified" version was verified on."""
    replayed = {case.case for case in validate_version(version).cases}

    assert replayed == set(golden_ports(version))


def test_every_documented_gap_carries_a_reason():
    """A difference recorded as known has to say why it is known. A bare
    exemption list would be indistinguishable from ignoring failures."""
    for name, case in cases().items():
        if name.startswith("_"):
            continue
        for gap in case.get("documented_gaps", ()):
            assert gap.get("reason"), f"{name}: a documented gap with no reason"
            assert gap.get("ports") or gap.get("prefixes"), (
                f"{name}: a documented gap that names nothing"
            )


def test_documented_gaps_name_real_ports():
    """An exemption for a port that does not exist on either side is dead
    weight that would hide the next real difference."""
    version = tool_matrix.reference_version()
    golden = golden_ports(version)
    for name, case in cases().items():
        if name.startswith("_"):
            continue
        real = {port["name"] for port in golden.get(name, [])}
        predicted = {p.name for p in tool_matrix.predicted_ports(case)}
        for gap in case.get("documented_gaps", ()):
            for port in gap.get("ports", ()):
                assert port in real or port in predicted, (
                    f"{name}: documented gap names {port!r}, which neither the "
                    f"prediction nor {version}'s output contains"
                )


def test_an_unknown_version_is_untested_never_verified():
    assert status_for("2099.2") == UNTESTED
    assert status_for(None) == UNTESTED
    assert status_for("") == UNTESTED

    report = validate_version("2099.2")
    assert report.status == UNTESTED
    assert report.cases == []
    assert "have not been validated" in report.report()


def test_the_support_matrix_only_lists_versions_with_evidence():
    matrix = support_matrix()

    assert set(matrix) == set(known_versions())
    assert all(status in (VERIFIED, DIVERGENT) for status in matrix.values())


def test_a_divergence_is_reported_rather_than_absorbed(tmp_path, monkeypatch):
    """The suite has to be able to fail. Feed it output from a fictional
    version that renames a port, and it must come back divergent — this is
    what would catch a real tool-version change."""
    version = tool_matrix.reference_version()
    doctored = json.loads(json.dumps(golden_ports(version)))
    doctored["m_scalars"] = [
        {**port, "name": port["name"] + "_renamed"}
        if port["name"] == "in_none" else port
        for port in doctored["m_scalars"]
    ]
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir()
    (golden_dir / "2099.1.json").write_text(json.dumps(doctored))
    monkeypatch.setattr(tool_matrix, "GOLDEN_ROOT", golden_dir)

    report = validate_version("2099.1")

    assert report.status == DIVERGENT
    ports = {d.port for d in report.unexplained}
    assert {"in_none", "in_none_renamed"} <= ports
    assert "! in_none" in report.report()


def test_a_golden_case_with_no_predictor_inputs_is_an_error(tmp_path, monkeypatch):
    version = tool_matrix.reference_version()
    doctored = json.loads(json.dumps(golden_ports(version)))
    doctored["m_invented"] = [{"name": "ap_clk", "direction": "input", "width": "1"}]
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir()
    (golden_dir / "2099.1.json").write_text(json.dumps(doctored))
    monkeypatch.setattr(tool_matrix, "GOLDEN_ROOT", golden_dir)

    with pytest.raises(KeyError, match="m_invented"):
        validate_version("2099.1")


def test_the_report_separates_documented_gaps_from_failures():
    report = validate_version(tool_matrix.reference_version())
    text = report.report()

    assert "VERIFIED" in text
    assert "documented gap port(s)" in text
    assert "Known gaps" in text
    # Every gap line carries its reason, not just a port name.
    for _case, reason in report.documented_gaps:
        assert reason in text


def test_doctor_reports_the_installed_versions_status(monkeypatch, capsys):
    """`forge doctor` must state the status of the release actually on PATH,
    including when that release is one nobody has tested."""
    from forge.core.cli.groups.doctor import _run_checks

    monkeypatch.setattr(tool_matrix, "installed_version", lambda *a, **k: "2099.2")
    checks = _run_checks()

    detail = str(checks["tool:vitis_hls"]["detail"])
    assert "2099.2" in detail
    assert UNTESTED in detail
    assert "nothing has checked" in detail


def test_doctor_is_honest_when_vitis_is_absent(monkeypatch):
    from forge.core.cli.groups.doctor import _run_checks

    monkeypatch.setattr(tool_matrix, "installed_version", lambda *a, **k: None)
    checks = _run_checks()

    assert checks["tool:vitis_hls"]["detail"] == "not on PATH"
    assert checks["tool:vitis_hls"]["status"] == "missing (optional)"


def test_the_generated_docs_page_lists_only_tested_versions():
    from forge.docsgen.support_matrix_reference import generate_support_matrix_page

    page = generate_support_matrix_page()

    assert "## Vitis HLS versions" in page
    for version in known_versions():
        assert f"`{version}`" in page
    assert "has not been tested" in page
