"""Tests for forge.verification.dataset_service.DatasetService.

Uses passthrough_demo's real golden XML fixture and its real
IdentityXmlDatasetAdapter, matching the convention already established by
test_dataset_adapter.py/test_dataset_format.py — not a synthetic stub.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forge.verification.dataset_adapter import DatasetSource
from forge.verification.dataset_service import DatasetService

REPO_ROOT = Path(__file__).resolve().parents[3]
PASSTHROUGH_GOLDEN_XML = (
    REPO_ROOT / "plugins/passthrough_demo/forge/verify/schemas/data/passthrough_demo_golden.xml"
)
PASSTHROUGH_TOOLS_DIR = REPO_ROOT / "plugins/passthrough_demo/forge/verify/tools"


@pytest.fixture(autouse=True)
def _passthrough_identity_adapter_registered():
    """`passthrough.identity-xml` self-registers on import of the
    plugin's own `dataset_adapter.py`, exactly as it does for real when
    the plugin's `bootstrap.py` runs."""
    import sys
    sys.path.insert(0, str(PASSTHROUGH_TOOLS_DIR))
    try:
        import dataset_adapter as _plugin_dataset_adapter  # noqa: F401
        yield
    finally:
        sys.path.remove(str(PASSTHROUGH_TOOLS_DIR))
        sys.modules.pop("dataset_adapter", None)


def test_load_from_raw_path_matches_direct_format_loader():
    from forge.verification.dataset_format import get_format_loader
    direct = get_format_loader(PASSTHROUGH_GOLDEN_XML).load(PASSTHROUGH_GOLDEN_XML)

    via_service = DatasetService().load(DatasetSource(raw_path=PASSTHROUGH_GOLDEN_XML))
    assert via_service.events == direct.events
    assert via_service.metadata.event_ids == direct.metadata.event_ids


def test_load_passes_through_already_serialized():
    from forge.verification.dataset_format import get_format_loader
    serialized = get_format_loader(PASSTHROUGH_GOLDEN_XML).load(PASSTHROUGH_GOLDEN_XML)
    result = DatasetService().load(DatasetSource(serialized=serialized))
    assert result is serialized


def test_load_raises_on_empty_source():
    with pytest.raises(ValueError):
        DatasetService().load(DatasetSource())


def test_list_events_matches_metadata_event_ids():
    service = DatasetService()
    serialized = service.load(DatasetSource(raw_path=PASSTHROUGH_GOLDEN_XML))
    canonical = service.materialize(
        DatasetSource(serialized=serialized), "passthrough.identity-xml", {},
    )
    assert service.list_events(canonical) == list(canonical.metadata.event_ids)


def test_select_events_preserves_given_order():
    service = DatasetService()
    serialized = service.load(DatasetSource(raw_path=PASSTHROUGH_GOLDEN_XML))
    canonical = service.materialize(
        DatasetSource(serialized=serialized), "passthrough.identity-xml", {},
    )
    all_ids = service.list_events(canonical)
    assert len(all_ids) >= 2
    reversed_ids = list(reversed(all_ids))

    selected = service.select_events(canonical, reversed_ids)
    assert selected.metadata.event_ids == reversed_ids
    assert len(selected.events) == len(reversed_ids)


def test_select_events_recomputes_identity_hash():
    service = DatasetService()
    serialized = service.load(DatasetSource(raw_path=PASSTHROUGH_GOLDEN_XML))
    canonical = service.materialize(
        DatasetSource(serialized=serialized), "passthrough.identity-xml", {},
    )
    all_ids = service.list_events(canonical)
    subset = service.select_events(canonical, all_ids[:1])
    assert subset.metadata.semantic.source_content_hash != canonical.metadata.semantic.source_content_hash


def test_select_events_unknown_id_raises():
    service = DatasetService()
    serialized = service.load(DatasetSource(raw_path=PASSTHROUGH_GOLDEN_XML))
    canonical = service.materialize(
        DatasetSource(serialized=serialized), "passthrough.identity-xml", {},
    )
    with pytest.raises(ValueError, match="not found"):
        service.select_events(canonical, ["nonexistent-event-id"])


def test_materialize_produces_a_real_preprocessing_hash():
    """The whole point of this method: preprocessing_hash must no longer
    be None (its state in every dataset that predates this slice)."""
    service = DatasetService()
    serialized = service.load(DatasetSource(raw_path=PASSTHROUGH_GOLDEN_XML))
    canonical = service.materialize(
        DatasetSource(serialized=serialized), "passthrough.identity-xml", {"resize": "none"},
    )
    assert canonical.metadata.semantic.preprocessing_hash is not None


def test_materialize_preprocessing_hash_changes_with_config():
    service = DatasetService()
    serialized = service.load(DatasetSource(raw_path=PASSTHROUGH_GOLDEN_XML))
    a = service.materialize(
        DatasetSource(serialized=serialized), "passthrough.identity-xml", {"resize": "nearest"},
    )
    b = service.materialize(
        DatasetSource(serialized=serialized), "passthrough.identity-xml", {"resize": "bilinear"},
    )
    assert a.metadata.semantic.preprocessing_hash != b.metadata.semantic.preprocessing_hash


def test_materialize_preserves_real_adapter_events():
    service = DatasetService()
    serialized = service.load(DatasetSource(raw_path=PASSTHROUGH_GOLDEN_XML))
    direct_canonical = get_dataset_adapter_result(serialized)
    via_service = service.materialize(
        DatasetSource(serialized=serialized), "passthrough.identity-xml", {},
    )
    assert via_service.events == direct_canonical.events


def get_dataset_adapter_result(serialized):
    from forge.verification.dataset_adapter import get_dataset_adapter
    adapter = get_dataset_adapter("passthrough.identity-xml")
    return adapter.materialize(DatasetSource(serialized=serialized), {})


def test_compute_identity_matches_compute_events_content_hash():
    from forge.verification.dataset_format import compute_events_content_hash
    service = DatasetService()
    serialized = service.load(DatasetSource(raw_path=PASSTHROUGH_GOLDEN_XML))
    canonical = service.materialize(
        DatasetSource(serialized=serialized), "passthrough.identity-xml", {},
    )
    assert service.compute_identity(canonical) == compute_events_content_hash(canonical.events)
