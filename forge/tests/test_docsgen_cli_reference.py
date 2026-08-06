"""Tests for forge.docsgen.cli_reference."""
from __future__ import annotations

from forge.docsgen.cli_reference import generate_cli_reference_page


def test_deterministic_output() -> None:
    assert generate_cli_reference_page() == generate_cli_reference_page()


def test_deterministic_regardless_of_terminal_width(monkeypatch) -> None:
    monkeypatch.setenv("COLUMNS", "40")
    narrow = generate_cli_reference_page()
    monkeypatch.setenv("COLUMNS", "200")
    wide = generate_cli_reference_page()
    assert narrow == wide


def test_restores_columns_env_var(monkeypatch) -> None:
    monkeypatch.setenv("COLUMNS", "77")
    generate_cli_reference_page()
    assert __import__("os").environ["COLUMNS"] == "77"


def test_covers_every_top_level_group_and_standalone_command() -> None:
    page = generate_cli_reference_page()
    for command in (
        "forge core", "forge topgen", "forge hls", "forge verify",
        "forge analyze", "forge framework", "forge doctor", "forge inspect",
        "forge build", "forge test", "forge report", "forge init",
    ):
        assert f"`{command}`" in page, f"{command} missing from CLI reference"


def test_verify_delegated_subcommands_are_documented() -> None:
    """forge verify hands off to its own separate parser via REMAINDER —
    a naive walk of the main tree alone would stop at the bare 'verify'
    leaf and miss its real subcommands entirely."""
    page = generate_cli_reference_page()
    assert "`forge verify generate`" in page
    assert "`forge verify doctor`" in page
    assert "`forge verify run`" in page


def test_no_absolute_filesystem_paths_leak() -> None:
    page = generate_cli_reference_page()
    assert "/home/" not in page
    assert "/data3/" not in page
