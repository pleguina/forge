"""Tests for forge.verify.golden_model (the GoldenModelProvider
protocol/registry/runner)."""
from __future__ import annotations

import pytest

from forge.verify.dataset_adapter import CanonicalDataset
from forge.verify.dataset_format import (
    DATASET_SCHEMA,
    DatasetMetadata,
    EnvironmentMetadata,
    SemanticMetadata,
)
from forge.verify.golden_model import (
    EXPECTED_DATASET_SCHEMA,
    ExpectedDataset,
    get_golden_model_provider,
    list_registered_golden_model_providers,
    register_golden_model_provider,
    run_golden_model,
    write_provider_provenance,
)


def _make_canonical_dataset(events: "list[dict]") -> CanonicalDataset:
    metadata = DatasetMetadata(
        schema=DATASET_SCHEMA,
        event_ids=[str(i) for i in range(len(events))],
        semantic=SemanticMetadata(source_content_hash="fixture"),
        environment=EnvironmentMetadata(source_path="fixture", generated_at="fixture"),
    )
    return CanonicalDataset(events=events, metadata=metadata)


class _DoublingProvider:
    provider_id = "test.doubling"
    provider_version = "1.0"

    def evaluate(self, dataset, config):
        events = [{"expected": ev["value"] * config.get("factor", 2)} for ev in dataset.events]
        return ExpectedDataset(
            schema=EXPECTED_DATASET_SCHEMA,
            event_ids=list(dataset.metadata.event_ids),
            events=events,
            provider_id=self.provider_id,
            provider_version=self.provider_version,
        )


@pytest.fixture(autouse=True)
def _register_test_provider():
    register_golden_model_provider("test.doubling", _DoublingProvider())
    yield


def test_registry_round_trip():
    provider = get_golden_model_provider("test.doubling")
    assert provider.provider_id == "test.doubling"
    assert "test.doubling" in list_registered_golden_model_providers()


def test_get_unknown_provider_raises_with_known_ids_listed():
    with pytest.raises(ValueError, match="test.doubling"):
        get_golden_model_provider("nonexistent")


def test_provider_returns_hash_fields_as_none():
    """A provider must never self-report identity hashes — only the
    runner does. Calling evaluate() directly (bypassing run_golden_model)
    is the regression case this guards against."""
    dataset = _make_canonical_dataset([{"value": 1}])
    provider = get_golden_model_provider("test.doubling")
    expected = provider.evaluate(dataset, {})
    assert expected.input_dataset_hash is None
    assert expected.output_hash is None


def test_run_golden_model_populates_hashes():
    dataset = _make_canonical_dataset([{"value": 1}, {"value": 2}])
    expected = run_golden_model("test.doubling", dataset, {"factor": 3})
    assert expected.events == [{"expected": 3}, {"expected": 6}]
    assert expected.input_dataset_hash is not None
    assert expected.output_hash is not None


def test_run_golden_model_hashes_are_deterministic():
    dataset = _make_canonical_dataset([{"value": 1}])
    first = run_golden_model("test.doubling", dataset, {"factor": 2})
    second = run_golden_model("test.doubling", dataset, {"factor": 2})
    assert first.input_dataset_hash == second.input_dataset_hash
    assert first.output_hash == second.output_hash


def test_run_golden_model_output_hash_changes_with_config():
    dataset = _make_canonical_dataset([{"value": 1}])
    factor_2 = run_golden_model("test.doubling", dataset, {"factor": 2})
    factor_3 = run_golden_model("test.doubling", dataset, {"factor": 3})
    assert factor_2.output_hash != factor_3.output_hash
    # The input dataset itself didn't change, so its hash must agree.
    assert factor_2.input_dataset_hash == factor_3.input_dataset_hash


def test_run_golden_model_unknown_provider_raises():
    dataset = _make_canonical_dataset([{"value": 1}])
    with pytest.raises(ValueError):
        run_golden_model("nonexistent", dataset, {})


def test_expected_dataset_to_dict_round_trips_schema():
    dataset = _make_canonical_dataset([{"value": 1}])
    expected = run_golden_model("test.doubling", dataset, {})
    payload = expected.to_dict()
    assert payload["schema"] == {"name": "forge.expected_dataset", "version": "1.0"}
    assert payload["provider_id"] == "test.doubling"


def test_write_provider_provenance_writes_real_identity_and_hashes(tmp_path):
    import json

    dataset = _make_canonical_dataset([{"value": 1}])
    expected = run_golden_model("test.doubling", dataset, {})
    sidecar = tmp_path / "golden_model_provenance.json"

    write_provider_provenance(expected, sidecar)

    payload = json.loads(sidecar.read_text())
    assert payload["provider_id"] == "test.doubling"
    assert payload["provider_version"] == expected.provider_version
    assert payload["input_dataset_hash"] == expected.input_dataset_hash
    assert payload["output_hash"] == expected.output_hash


def test_write_provider_provenance_accepts_string_path(tmp_path):
    dataset = _make_canonical_dataset([{"value": 1}])
    expected = run_golden_model("test.doubling", dataset, {})
    sidecar = str(tmp_path / "golden_model_provenance.json")

    write_provider_provenance(expected, sidecar)

    assert (tmp_path / "golden_model_provenance.json").exists()
