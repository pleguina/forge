"""Unit tests for the FORGE_CHECK machine-readable check-record mechanism
(Phase 7, slice 7.3): SV emission (stimulus_helpers.py) and log parsing
(results.py). Real end-to-end proof against actual xsim output lives in
tests/test_test_cli_group.py.
"""

from __future__ import annotations

from forge.verify.results import CheckResult, parse_forge_check_lines
from forge.verify.stimulus_helpers import StimulusEmitter, emit_output_check


# ── SV emission ──────────────────────────────────────────────────────────

def test_emit_output_check_includes_unconditional_forge_check_line():
    lines = emit_output_check("pt_data_out", 0x3A, width=8, label="data_out_check", event_id=0)
    check_line = lines[0]
    assert check_line.strip().startswith('$display("FORGE_CHECK|')
    assert "check_id=0:data_out_check" in check_line
    assert "label=data_out_check" in check_line
    assert "signal=pt_data_out" in check_line
    assert "expected=0x3a" in check_line
    assert "width=8" in check_line
    # observed/passed come from the live runtime value — %h/%0d format
    # specifiers, not compile-time literals.
    assert "observed=0x%h" in check_line
    assert "passed=%0d" in check_line


def test_emit_output_check_forge_check_line_precedes_the_fail_branch():
    lines = emit_output_check("sig", 1, width=1, label="chk")
    assert "FORGE_CHECK|" in lines[0]
    assert lines[1].strip().startswith("if (")


def test_emit_output_check_human_fail_line_unchanged():
    """The existing FAIL:/$fatal branch is untouched — FORGE_CHECK is a
    separate, additional channel, not a replacement."""
    lines = emit_output_check("sig", 1, width=1, label="chk")
    joined = "\n".join(lines)
    assert 'FAIL: chk — expected 1\'b1, got %0h' in joined
    assert '$fatal(1, "Check failed: chk");' in joined


def test_emit_output_check_without_event_id_uses_label_as_check_id():
    lines = emit_output_check("sig", 1, width=1, label="chk")
    assert "check_id=chk|" in lines[0]


def test_emit_output_check_check_id_falls_back_to_signal_when_no_label():
    lines = emit_output_check("sig", 1, width=1)
    assert "check_id=sig|" in lines[0]
    assert "label=sig|" in lines[0]


def test_stimulus_emitter_check_threads_event_id():
    em = StimulusEmitter()
    em.check("out", 0x00, width=1, label="valid_chk", event_id=1)
    rendered = em.render()
    assert "check_id=1:valid_chk" in rendered


def test_emit_output_check_width_one_hex_formatting():
    lines = emit_output_check("sig", 1, width=1, label="chk")
    assert "expected=0x1" in lines[0]


def test_emit_output_check_wide_value_zero_padded_hex():
    lines = emit_output_check("sig", 0x05, width=16, label="chk")
    # 16 bits -> 4 hex digits, zero-padded.
    assert "expected=0x0005" in lines[0]


# ── Log parsing ──────────────────────────────────────────────────────────

def test_parse_forge_check_lines_passing_record():
    log = (
        "some noise\n"
        "FORGE_CHECK|check_id=0:data_out_check|label=data_out_check|"
        "signal=pt_data_out|expected=0x3a|observed=0x3a|width=8|passed=1\n"
        "TB PASS: completed\n"
    )
    results = parse_forge_check_lines(log)
    assert len(results) == 1
    r = results[0]
    assert isinstance(r, CheckResult)
    assert r.check_id == "0:data_out_check"
    assert r.label == "data_out_check"
    assert r.signal == "pt_data_out"
    assert r.expected == "0x3a"
    assert r.observed == "0x3a"
    assert r.expected == r.observed
    assert r.width == 8
    assert r.passed is True


def test_parse_forge_check_lines_failing_record():
    log = (
        "FORGE_CHECK|check_id=1:data_out_check|label=data_out_check|"
        "signal=pt_data_out|expected=0x00|observed=0x3a|width=8|passed=0\n"
    )
    results = parse_forge_check_lines(log)
    assert len(results) == 1
    r = results[0]
    assert r.expected != r.observed
    assert r.passed is False


def test_parse_forge_check_lines_multiple_records_in_order():
    log = "\n".join([
        "FORGE_CHECK|check_id=0:a|label=a|signal=s|expected=0x1|observed=0x1|width=1|passed=1",
        "FORGE_CHECK|check_id=0:b|label=b|signal=s2|expected=0x0|observed=0x0|width=1|passed=1",
    ])
    results = parse_forge_check_lines(log)
    assert [r.check_id for r in results] == ["0:a", "0:b"]


def test_parse_forge_check_lines_ignores_human_fail_line():
    log = 'FAIL: data_out_check — expected 8\'h3A, got 00\n'
    assert parse_forge_check_lines(log) == []


def test_parse_forge_check_lines_ignores_lines_without_check_id():
    log = "FORGE_CHECK|label=x|passed=1\n"
    assert parse_forge_check_lines(log) == []


def test_parse_forge_check_lines_empty_text():
    assert parse_forge_check_lines("") == []


def test_parse_forge_check_lines_signal_none_when_absent():
    log = "FORGE_CHECK|check_id=x|passed=1\n"
    results = parse_forge_check_lines(log)
    assert results[0].signal is None


def test_check_result_to_dict_shape():
    r = CheckResult(
        check_id="0:chk", label="chk", signal="s", expected="0x1", observed="0x1",
        width=1, passed=True,
    )
    assert r.to_dict() == {
        "check_id": "0:chk", "label": "chk", "signal": "s",
        "expected": "0x1", "observed": "0x1", "width": 1, "passed": True,
    }


# ── Round trip: SV-emitted line format parses back correctly ──────────────

def test_forge_check_emission_and_parsing_round_trip_pass_case():
    """The exact SV `$display` template, with the runtime substitutions a
    real simulator would perform, parses back to a correct CheckResult."""
    lines = emit_output_check("pt_data_out", 0x3A, width=8, label="data_out_check", event_id=0)
    template = lines[0]
    # Simulate what xsim/verilator's $display would actually print at
    # runtime: observed=0x%h -> observed=0x3a, passed=%0d -> passed=1.
    rendered_log_line = (
        template
        .split('$display("')[1]
        .split('", ')[0]
        .replace("observed=0x%h", "observed=0x3a")
        .replace("passed=%0d", "passed=1")
    )
    results = parse_forge_check_lines(rendered_log_line)
    assert len(results) == 1
    assert results[0].expected == results[0].observed == "0x3a"
    assert results[0].passed is True
