"""
Tests for forge.core.schema_version: the shared parsing/compatibility
policy used by design.yml, modules.yml, *.interface.yaml, and
design.verification.yml.
"""

from __future__ import annotations

import pytest

from forge.core.schema_version import (
    SchemaVersionIssue,
    check_schema_version,
    parse_schema_version,
)


class TestParseSchemaVersion:
    def test_major_minor(self):
        assert parse_schema_version("1.0") == (1, 0)
        assert parse_schema_version("2.3") == (2, 3)

    def test_bare_major_defaults_minor_to_zero(self):
        assert parse_schema_version("1") == (1, 0)

    def test_non_integer_component_raises(self):
        with pytest.raises(ValueError):
            parse_schema_version("1.x")

    def test_too_many_parts_raises(self):
        with pytest.raises(ValueError):
            parse_schema_version("1.2.3")

    def test_negative_component_raises(self):
        with pytest.raises(ValueError):
            parse_schema_version("-1.0")

    def test_non_string_raises(self):
        with pytest.raises(ValueError):
            parse_schema_version(1.0)  # type: ignore[arg-type]


class TestCheckSchemaVersion:
    def test_absent_is_silent(self):
        assert check_schema_version(None, "1.0", schema_name="x") == []

    def test_exact_match_is_silent(self):
        assert check_schema_version("1.0", "1.0", schema_name="x") == []

    def test_older_minor_is_silent(self):
        assert check_schema_version("1.0", "1.3", schema_name="x") == []

    def test_newer_minor_is_a_warning(self):
        issues = check_schema_version("1.5", "1.0", schema_name="x")
        assert len(issues) == 1
        assert issues[0].severity == "warning"
        assert "newer" in issues[0].message

    def test_different_major_is_an_error(self):
        issues = check_schema_version("2.0", "1.0", schema_name="x")
        assert len(issues) == 1
        assert issues[0].severity == "error"
        assert "incompatible" in issues[0].message

    def test_malformed_value_is_an_error(self):
        issues = check_schema_version("not-a-version", "1.0", schema_name="x")
        assert len(issues) == 1
        assert issues[0].severity == "error"

    def test_schema_name_appears_in_message(self):
        issues = check_schema_version("2.0", "1.0", schema_name="my-schema")
        assert "my-schema" in issues[0].message


def test_issue_str_formats_with_icon():
    assert "❌" in str(SchemaVersionIssue("error", "boom"))
    assert "⚠️" in str(SchemaVersionIssue("warning", "careful"))
