"""Tests for forge.docsgen.diagnostics_registry (release-plan Phase 9,
Defect 13)."""
from __future__ import annotations

from pathlib import Path

from forge.docsgen.diagnostics_registry import (
    DIAGNOSTICS,
    check_diagnostics_registry_matches_source,
    find_diagnostic_codes_in_file,
    generate_diagnostics_page,
)


def test_registry_currently_matches_source() -> None:
    """The real regression this registry exists to catch: a prior
    investigation found FWV000/FWV021/FWV022 emitted but undocumented."""
    assert check_diagnostics_registry_matches_source() == []


def test_previously_undocumented_codes_are_now_registered() -> None:
    for code in ("FWV000", "FWV021", "FWV022"):
        assert code in DIAGNOSTICS


def test_deterministic_output() -> None:
    assert generate_diagnostics_page() == generate_diagnostics_page()


def test_covers_both_families() -> None:
    page = generate_diagnostics_page()
    assert "FWV (verification framework) codes" in page
    assert "ATG (topology generation) codes" in page
    assert "`FWV001`" in page
    assert "`ATG001`" in page


def test_scanner_ignores_docstring_only_mentions(tmp_path: Path) -> None:
    """forge/verify/exceptions.py documents FWV codes in prose
    ('Corresponds to diagnostic code FWV001.') without emitting them — a
    text grep would false-positive on this; the AST scanner must not."""
    src = tmp_path / "sample.py"
    src.write_text(
        '"""\n'
        "Corresponds to diagnostic code FWV999.\n"
        '"""\n'
        "def f():\n"
        "    pass\n"
    )
    assert find_diagnostic_codes_in_file(src) == set()


def test_scanner_finds_structured_construction(tmp_path: Path) -> None:
    src = tmp_path / "sample.py"
    src.write_text(
        "def f():\n"
        "    return Diagnostic(code='FWV999', severity=Severity.ERROR, message='x')\n"
    )
    assert find_diagnostic_codes_in_file(src) == {"FWV999"}


def test_scanner_finds_convenience_method_calls(tmp_path: Path) -> None:
    src = tmp_path / "sample.py"
    src.write_text(
        "def f(report):\n"
        "    report.note('FWV999', 'ok')\n"
        "    report.warn('FWV998', 'careful')\n"
    )
    assert find_diagnostic_codes_in_file(src) == {"FWV999", "FWV998"}


def test_scanner_finds_dict_literal_code_key(tmp_path: Path) -> None:
    src = tmp_path / "sample.py"
    src.write_text(
        "def f():\n"
        "    return {'severity': 'warning', 'code': 'ATG999'}\n"
    )
    assert find_diagnostic_codes_in_file(src) == {"ATG999"}


def test_scanner_finds_bracketed_fstring_prefix(tmp_path: Path) -> None:
    src = tmp_path / "sample.py"
    src.write_text(
        "def f(x):\n"
        "    issues.append(f'[FWV997] something: {x}')\n"
    )
    assert find_diagnostic_codes_in_file(src) == {"FWV997"}


def test_scanner_does_not_match_unanchored_bracket(tmp_path: Path) -> None:
    src = tmp_path / "sample.py"
    src.write_text(
        "def f():\n"
        "    return 'see also [FWV996] for details'\n"  # not anchored at start
    )
    assert find_diagnostic_codes_in_file(src) == set()
