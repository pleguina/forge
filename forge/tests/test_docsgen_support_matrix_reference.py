"""Tests for forge.docsgen.support_matrix_reference."""
from __future__ import annotations

from forge.verify.supported_matrix import MATRIX
from forge.docsgen.support_matrix_reference import generate_support_matrix_page


def test_deterministic_output() -> None:
    assert generate_support_matrix_page() == generate_support_matrix_page()


def test_every_matrix_kind_is_listed() -> None:
    page = generate_support_matrix_page()
    for kind in MATRIX:
        assert f"`{kind}`" in page


def test_reports_allowed_backends_not_just_default() -> None:
    """The whole point of MATRIX over the older SUPPORTED_MATRIX view is
    that it carries the full allowed_backends set, not just the default."""
    page = generate_support_matrix_page()
    entry = MATRIX["single_module_rtl"]
    assert len(entry.allowed_backends) > 1
    for backend in entry.allowed_backends:
        assert f"`{backend}`" in page
