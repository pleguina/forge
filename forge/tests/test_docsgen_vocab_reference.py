"""Tests for forge.docsgen.vocab_reference."""
from __future__ import annotations

import pytest

from forge.docsgen.vocab_reference import (
    TRANSFORMATION_KINDS,
    check_transformation_registry_matches_source,
    generate_canonical_roles_page,
    generate_interface_members_page,
    generate_protocols_page,
    generate_transformations_page,
    real_transformation_kinds_in_source,
)


def test_canonical_roles_page_deterministic_and_covers_reserved_roles() -> None:
    page = generate_canonical_roles_page()
    assert page == generate_canonical_roles_page()
    assert "`clock_secondary`" in page
    assert "reserved" in page


def test_protocols_page_lists_known_protocols() -> None:
    page = generate_protocols_page()
    for protocol in ("combinational", "valid-only", "ready-valid", "fixed-frame"):
        assert f"`{protocol}`" in page


def test_interface_members_page_lists_known_members() -> None:
    page = generate_interface_members_page()
    for member in ("data", "valid", "ready", "last", "metadata"):
        assert f"`{member}`" in page


def test_transformation_registry_currently_matches_source() -> None:
    assert check_transformation_registry_matches_source() == []


def test_real_transformation_kinds_match_expected_eleven() -> None:
    assert real_transformation_kinds_in_source() == {
        "pipeline_register", "latency_delay", "slr_crossing", "fanout",
        "cdc_synchronizer", "async_fifo", "tie_off", "gather_scatter",
        "pulse_sync", "mailbox_transfer", "reset_synchronizer",
    }


def test_reserved_kinds_have_zero_live_instances() -> None:
    real = real_transformation_kinds_in_source()
    for kind in ("width_adapter", "protocol_adapter", "constant_source"):
        assert kind not in real
        assert TRANSFORMATION_KINDS[kind].status == "reserved"


def test_transformations_page_deterministic() -> None:
    assert generate_transformations_page() == generate_transformations_page()


def test_transformations_page_raises_if_registry_drifts_from_source(monkeypatch) -> None:
    """Regression guard for the registry's whole reason to exist: if a new
    real kind appeared in forge/ir/build.py without being registered,
    generation must fail loudly, not silently ship a stale page."""
    monkeypatch.setattr(
        "forge.docsgen.vocab_reference.real_transformation_kinds_in_source",
        lambda: {"pipeline_register", "a_brand_new_unregistered_kind"},
    )
    with pytest.raises(RuntimeError, match="out of sync"):
        generate_transformations_page()
