"""Tests for forge.verify.stimulus_contract — validates stimulus_current.svh
files against the framework's task-signature/no-$finish/closed-task contract
before a simulator is ever launched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.verify.stimulus_contract import (
    STIMULUS_TASK_SIGNATURE,
    StimulusContractResult,
    validate_stimulus,
    validate_stimulus_for_flow,
)

_VALID_SVH = """\
// stimulus_current.svh
task automatic run_stimulus();
    ap_rst <= 1'b1;
    @(posedge ap_clk);
    ap_rst <= 1'b0;
    if (data_out !== 8'hFF) $fatal(1, "FAIL: mismatch");
endtask : run_stimulus
"""


def _write(tmp_path: Path, text: str, name: str = "stimulus_current.svh") -> Path:
    p = tmp_path / name
    p.write_text(text)
    return p


class TestStimulusContractResult:
    def test_ok_true_when_no_errors(self):
        result = StimulusContractResult(warnings=["w1"], notes=["n1"])
        assert result.ok is True

    def test_ok_false_when_errors_present(self):
        result = StimulusContractResult(errors=["boom"])
        assert result.ok is False

    def test_format_errors_includes_errors_and_warnings(self):
        result = StimulusContractResult(errors=["e1"], warnings=["w1"])
        formatted = result.format_errors()
        assert "e1" in formatted
        assert "w1" in formatted
        assert "FAILED" in formatted


class TestValidateStimulusHappyPath:
    def test_valid_file_is_ok(self, tmp_path: Path):
        svh = _write(tmp_path, _VALID_SVH)
        result = validate_stimulus(svh)
        assert result.ok
        assert result.errors == []

    def test_valid_file_produces_ok_note(self, tmp_path: Path):
        svh = _write(tmp_path, _VALID_SVH)
        result = validate_stimulus(svh)
        assert any("Stimulus contract OK" in n for n in result.notes)


class TestValidateStimulusErrors:
    def test_missing_file_is_error(self, tmp_path: Path):
        result = validate_stimulus(tmp_path / "does_not_exist.svh")
        assert not result.ok
        assert any("not found" in e for e in result.errors)

    def test_empty_file_is_error(self, tmp_path: Path):
        svh = _write(tmp_path, "   \n  \n")
        result = validate_stimulus(svh)
        assert not result.ok
        assert any("empty" in e for e in result.errors)

    def test_missing_task_signature_is_error(self, tmp_path: Path):
        svh = _write(tmp_path, "// no task here at all\n")
        result = validate_stimulus(svh)
        assert not result.ok
        assert any("Missing required signature" in e for e in result.errors)

    def test_missing_endtask_is_error(self, tmp_path: Path):
        text = "task automatic run_stimulus();\n  ap_rst <= 1'b1;\n"  # no endtask
        svh = _write(tmp_path, text)
        result = validate_stimulus(svh)
        assert not result.ok
        assert any("endtask" in e for e in result.errors)

    def test_dollar_finish_is_error(self, tmp_path: Path):
        text = _VALID_SVH + "\n$finish;\n"
        svh = _write(tmp_path, text)
        result = validate_stimulus(svh)
        assert not result.ok
        assert any("$finish" in e for e in result.errors)

    def test_duplicate_task_definitions_is_error(self, tmp_path: Path):
        text = _VALID_SVH + "\n" + _VALID_SVH
        svh = _write(tmp_path, text)
        result = validate_stimulus(svh)
        assert not result.ok
        assert any("Multiple" in e for e in result.errors)

    def test_commented_out_signature_not_counted_as_duplicate(self, tmp_path: Path):
        text = _VALID_SVH + "\n// task automatic run_stimulus();\n"
        svh = _write(tmp_path, text)
        result = validate_stimulus(svh)
        # Only one *real* signature; the commented-out one must not count.
        assert not any("Multiple" in e for e in result.errors)


class TestValidateStimulusWarnings:
    def test_missing_clk_reference_is_warning_not_error(self, tmp_path: Path):
        text = (
            "task automatic run_stimulus();\n"
            "    data_in <= 8'h01;\n"
            "    if (data_out !== 8'h01) $fatal(1, \"FAIL: x\");\n"
            "endtask\n"
        )
        svh = _write(tmp_path, text)
        result = validate_stimulus(svh)
        assert result.ok  # still passes overall
        assert any("ap_clk" in w for w in result.warnings)

    def test_no_check_pattern_is_warning(self, tmp_path: Path):
        text = (
            "task automatic run_stimulus();\n"
            "    ap_rst <= 1'b1;\n"
            "    @(posedge ap_clk);\n"
            "endtask\n"
        )
        svh = _write(tmp_path, text)
        result = validate_stimulus(svh)
        assert result.ok
        assert any("check/assert/fail" in w for w in result.warnings)

    def test_no_drive_statement_is_warning(self, tmp_path: Path):
        text = (
            "task automatic run_stimulus();\n"
            "endtask\n"
        )
        svh = _write(tmp_path, text)
        result = validate_stimulus(svh)
        assert any("input-driving" in w for w in result.warnings)


class TestValidateStimulusForFlow:
    def test_derives_path_from_cfg_flow_file(self, tmp_path: Path):
        _write(tmp_path, _VALID_SVH)

        class _Cfg:
            flow_file = str(tmp_path / "verify.flow.yml")

        result = validate_stimulus_for_flow(_Cfg())
        assert result.ok

    def test_flow_dir_override_takes_precedence(self, tmp_path: Path):
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        _write(real_dir, _VALID_SVH)

        class _Cfg:
            flow_file = str(tmp_path / "wrong" / "verify.flow.yml")

        result = validate_stimulus_for_flow(_Cfg(), flow_dir=real_dir)
        assert result.ok

    def test_missing_stimulus_reports_error(self, tmp_path: Path):
        class _Cfg:
            flow_file = str(tmp_path / "verify.flow.yml")

        result = validate_stimulus_for_flow(_Cfg())
        assert not result.ok


def test_stimulus_task_signature_constant_matches_docstring():
    # The framework-owned constant must exactly match what validate_stimulus
    # actually requires — this pins that invariant.
    assert STIMULUS_TASK_SIGNATURE == "task automatic run_stimulus();"
