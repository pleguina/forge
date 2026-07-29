"""Coverage for forge/core/utils/common.py (previously 0%)."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.core.exceptions import ConfigurationError, FileNotFoundError as ForgeFileNotFoundError
from forge.core.utils.common import (
    ensure_path_exists,
    format_duration,
    format_memory,
    load_yaml_safe,
    sanitize_identifier,
)


class TestSanitizeIdentifier:
    def test_empty_string_becomes_unnamed(self) -> None:
        assert sanitize_identifier("") == "unnamed"

    def test_replaces_non_alphanumeric_with_underscore(self) -> None:
        assert sanitize_identifier("my module!") == "my_module"

    def test_collapses_repeated_underscores(self) -> None:
        assert sanitize_identifier("a---b") == "a_b"

    def test_strips_leading_and_trailing_underscores(self) -> None:
        assert sanitize_identifier("__foo__") == "foo"

    def test_prefixes_leading_digit_with_id(self) -> None:
        assert sanitize_identifier("123abc") == "id_123abc"

    def test_all_underscores_after_strip_still_prefixed(self) -> None:
        # "!!!"  -> non-alnum -> "_" -> collapse -> "_" -> strip -> ""
        assert sanitize_identifier("!!!") == "id_"


class TestEnsurePathExists:
    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "c.txt"

        result = ensure_path_exists(target)

        assert result == target.resolve()
        assert target.parent.is_dir()


class TestLoadYamlSafe:
    def test_loads_valid_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("key: value\n")

        assert load_yaml_safe(path) == {"key": "value"}

    def test_invalid_yaml_raises_configuration_error(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("key: [unclosed\n")

        with pytest.raises(ConfigurationError, match="Invalid YAML"):
            load_yaml_safe(path)

    def test_missing_file_raises_forge_file_not_found_error(self, tmp_path: Path) -> None:
        # Regression test: load_yaml_safe's except-clauses used to import
        # from the wrong relative path (`forge.core.core.exceptions`,
        # which doesn't exist), so any failure here crashed with
        # ModuleNotFoundError instead of this typed error.
        missing = tmp_path / "missing.yaml"

        with pytest.raises(ForgeFileNotFoundError, match="Cannot read"):
            load_yaml_safe(missing)


class TestFormatDuration:
    def test_seconds(self) -> None:
        assert format_duration(45.2) == "45.2s"

    def test_minutes(self) -> None:
        assert format_duration(125) == "2.1m"

    def test_hours(self) -> None:
        assert format_duration(7500) == "2.1h"


class TestFormatMemory:
    def test_bytes(self) -> None:
        assert format_memory(512) == "512.0B"

    def test_kilobytes(self) -> None:
        assert format_memory(2048) == "2.0KB"

    def test_gigabytes(self) -> None:
        assert format_memory(3 * 1024 ** 3) == "3.0GB"

    def test_terabytes(self) -> None:
        assert format_memory(5 * 1024 ** 4) == "5.0TB"
