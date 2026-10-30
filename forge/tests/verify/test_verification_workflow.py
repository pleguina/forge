"""check, test and report agree about a project verified from input and expected data.

forge.yml's ``verification.input``/``expected`` is a configuration `forge
check` recognises, `forge test` writes evidence for, and `forge report` shows
with its status and whether it is still up to date. ``--require-verified``
turns anything short of VERIFIED into a failing exit.
"""

from __future__ import annotations

import json
import random
import shutil
from pathlib import Path

import pytest
import yaml

from forge.core.cli.main import build_parser
from forge.project.adopt import plan_adoption
from forge.project.status import FAIL, PARTIAL, PASS, evaluate
from forge.verification import auto

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "foreign" / "simple_pipeline"
needs_verilator = pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")


def _model(samples):
    out, prev = [], 0
    for seq, d in enumerate(samples[1::2]):
        out.append((seq << 16) | (((d >> 1) + (prev >> 1)) & 0xFFFF))
        prev = d
    return out


def _configure(root: Path, **verification) -> None:
    config = yaml.safe_load((root / "forge.yml").read_text())
    config["verification"] = verification
    (root / "forge.yml").write_text(yaml.safe_dump(config, sort_keys=False))


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "simple_pipeline"
    shutil.copytree(FIXTURE, root)
    plan_adoption(root).write()
    monkeypatch.chdir(root)
    xs = [random.Random(3).randrange(1 << 16) for _ in range(40)]
    (root / "input.json").write_text(json.dumps({"decimator_sample_in": xs}))
    (root / "expected.json").write_text(json.dumps({"packer_packet_out": _model(xs)}))
    _configure(root, input="input.json", expected="expected.json", levels=["design"])
    return root


def _run(capsys, *argv):
    args = build_parser().parse_args(list(argv))
    try:
        args.func(args)
        code = 0
    except SystemExit as exc:
        code = int(exc.code or 0)
    out = capsys.readouterr()
    return code, (json.loads(out.out) if "--json" in argv else out.out), out.err


def _verification_section(root: Path):
    return next(s for s in evaluate(root).sections if s.name == "Verification")


# ── forge check ───────────────────────────────────────────────────────────

def test_check_recognises_input_and_expected_in_forge_yml(project):
    section = _verification_section(project)

    assert section.state in (PASS, "WARN")
    assert "input input.json" in section.detail and "expected expected.json" in section.detail
    assert "not run yet" in section.detail


def test_check_names_a_missing_data_file(project):
    (project / "expected.json").unlink()

    section = _verification_section(project)

    assert section.state == FAIL and "ATG044" in section.codes


def test_check_says_input_without_expected_verifies_nothing(project):
    _configure(project, input="input.json")

    section = _verification_section(project)

    assert section.state == PARTIAL


def test_check_points_an_unconfigured_project_at_input_and_expected(tmp_path):
    root = tmp_path / "p"
    shutil.copytree(FIXTURE, root)
    plan_adoption(root).write()

    status = evaluate(root)

    assert any("verification.input" in (d.action or "") for d in status.report.all
               if d.code == "ATG044")


# ── forge test: missing simulator, --require-verified, next actions ───────

def test_a_missing_simulator_warns_and_require_verified_fails(project, monkeypatch, capsys):
    monkeypatch.setattr(auto.shutil, "which", lambda name: None)

    code, doc, _ = _run(capsys, "test", "--backend", "xsim", "--json")
    assert code == 0 and doc["status"] == "warn"
    assert doc["metrics"]["verification"] == "BLOCKED_MISSING_TOOL"
    assert any("on PATH" in a for a in doc["next_actions"])
    assert not any("expected output" in a for a in doc["next_actions"])

    code, doc, _ = _run(capsys, "test", "--backend", "xsim", "--require-verified", "--json")
    assert code == 1 and doc["status"] == "fail"


def test_no_expected_output_asks_for_one_where_it_is_missing(project, monkeypatch, capsys):
    _configure(project, input="input.json", levels=["design"])
    monkeypatch.setattr(auto, "run", lambda level, backend, work, **kw: auto._evidence(
        level, backend, "NOT_VERIFIED_NO_ORACLE"))

    code, doc, _ = _run(capsys, "test", "--backend", "verilator", "--json")

    assert code == 0
    assert doc["next_actions"] == ["Give an expected output: verification.expected (or --expected) in forge.yml"]


# ── forge report ──────────────────────────────────────────────────────────

def test_report_shows_the_evidence_forge_test_wrote(project, monkeypatch, capsys):
    monkeypatch.setattr(auto.shutil, "which", lambda name: None)
    _run(capsys, "test", "--backend", "xsim", "--json")

    code, doc, _ = _run(capsys, "report", "--json")

    assert doc["metrics"]["verification"] == "BLOCKED_MISSING_TOOL"
    names = [r["name"] for r in doc["metrics"]["verification_results"]]
    assert names == ["design[xsim]"]
    text = (project / ".forge/reports/verification_results.md").read_text()
    assert "BLOCKED_MISSING_TOOL" in text and "No verification results yet" not in text


@needs_verilator
def test_report_flags_results_older_than_their_sources(project, capsys):
    code, doc, _ = _run(capsys, "test", "--backend", "verilator", "--json")
    assert doc["metrics"]["verification"] == "VERIFIED"
    shaper = project / "rtl" / "shaper.v"
    shaper.write_text(shaper.read_text() + "// edited\n")

    _, doc, _ = _run(capsys, "report", "--json")

    assert any("shaper.v" in d["message"] for d in doc["diagnostics"])
    assert "(1 result(s) out of date)" in _verification_section(project).detail


# ── applicability: a result applies only to what it was computed from ─────

def _fake_verified(monkeypatch):
    monkeypatch.setattr(auto, "run", lambda level, backend, work, **kw: auto._evidence(
        level, backend, "VERIFIED"))


def test_every_run_is_kept_and_the_latest_is_reported(project, monkeypatch, capsys):
    _fake_verified(monkeypatch)
    _run(capsys, "test", "--backend", "verilator", "--json")
    _run(capsys, "test", "--backend", "verilator", "--json")

    runs = sorted((project / ".forge/reports/test/runs").iterdir())
    assert len(runs) == 2
    assert json.loads((runs[-1] / "run.json").read_text())["verification"] == "VERIFIED"
    _, doc, _ = _run(capsys, "report", "--json")
    assert doc["metrics"]["verification_runs"] == 2 and doc["metrics"]["verification"] == "VERIFIED"


def test_a_changed_header_makes_a_pass_out_of_date(project, monkeypatch, capsys):
    (project / "rtl" / "widths.vh").write_text("`define SAMPLE_W 16\n")
    shaper = project / "rtl" / "shaper.v"
    shaper.write_text('`include "widths.vh"\n' + shaper.read_text())
    _fake_verified(monkeypatch)
    _run(capsys, "test", "--backend", "verilator", "--json")

    (project / "rtl" / "widths.vh").write_text("`define SAMPLE_W 12\n")
    _, doc, _ = _run(capsys, "report", "--json")

    assert doc["metrics"]["verification"] == "OUT_OF_DATE"
    assert any("widths.vh" in d["message"] for d in doc["diagnostics"])


def test_a_changed_checker_setting_makes_a_pass_out_of_date(project, monkeypatch, capsys):
    _fake_verified(monkeypatch)
    _run(capsys, "test", "--backend", "verilator", "--json")
    _configure(project, input="input.json", expected="expected.json", levels=["design"],
               compare={"mode": "exact_cycle"})

    _, doc, _ = _run(capsys, "report", "--json")

    assert doc["metrics"]["verification"] == "OUT_OF_DATE"
    assert any("verification settings" in d["message"] for d in doc["diagnostics"])
    assert "out of date" in _verification_section(project).detail
