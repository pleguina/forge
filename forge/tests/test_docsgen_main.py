"""Tests for forge.docsgen._io and forge.docsgen.__main__: write-discipline
requirements and the staleness check."""
from __future__ import annotations

from pathlib import Path

import pytest

from forge.docsgen._io import write_page
from forge.docsgen.__main__ import main


def test_write_page_writes_content(tmp_path: Path) -> None:
    target = write_page(tmp_path, "sub/page.md", "hello\n")
    assert target == (tmp_path / "sub" / "page.md").resolve()
    assert target.read_text() == "hello\n"


def test_write_page_refuses_to_escape_output_dir(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside output_dir"):
        write_page(tmp_path, "../escape.md", "x")


def test_write_page_is_atomic_no_leftover_tmp_file(tmp_path: Path) -> None:
    write_page(tmp_path, "page.md", "content\n")
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_main_generates_all_pages(tmp_path: Path) -> None:
    exit_code = main(["--output-dir", str(tmp_path)])
    assert exit_code == 0
    expected = {
        "cli.md", "canonical-roles.md", "protocols.md", "interface-members.md",
        "transformations.md", "diagnostics.md", "support-matrix.md", "artifacts.md",
    }
    actual = {p.name for p in tmp_path.glob("*.md")}
    assert expected <= actual


def test_main_check_mode_passes_when_up_to_date(tmp_path: Path) -> None:
    main(["--output-dir", str(tmp_path)])
    assert main(["--check", "--output-dir", str(tmp_path)]) == 0


def test_main_check_mode_fails_when_stale(tmp_path: Path) -> None:
    main(["--output-dir", str(tmp_path)])
    (tmp_path / "cli.md").write_text("stale content\n")
    assert main(["--check", "--output-dir", str(tmp_path)]) == 1


def test_main_check_mode_writes_nothing(tmp_path: Path) -> None:
    main(["--check", "--output-dir", str(tmp_path)])
    assert list(tmp_path.glob("*.md")) == []
