"""Unit tests for forge.verify.dataset_format (Phase 7, slice 7.4a).

Covers both real format loaders against real fixture files (the real
passthrough_demo golden XML, and a real generated JSON sibling), the
format-loader registry, and content-hash determinism/tamper detection.
The passthrough_demo real-read (gen_stimulus.py no longer decorative) and
JSON-format-parity proofs live in tests/test_test_cli_group.py and
tests/verify/test_verify_run_xsim.py, since they need a real simulator.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge.verify.dataset_format import (
    DATASET_SCHEMA,
    DatasetMetadata,
    EnvironmentMetadata,
    JsonDatasetLoader,
    SemanticMetadata,
    SerializedDataset,
    XmlDatasetLoader,
    compute_events_content_hash,
    get_format_loader,
    register_format_loader,
)
from forge.verify.exceptions import DatasetContentHashError

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN_XML = REPO_ROOT / "plugins/passthrough_demo/forge/verify/schemas/data/passthrough_demo_golden.xml"
GOLDEN_JSON = REPO_ROOT / "plugins/passthrough_demo/forge/verify/schemas/data/passthrough_demo_golden.json"


# ── XmlDatasetLoader against the real golden XML ────────────────────────

def test_xml_loader_reads_real_passthrough_demo_golden_events():
    dataset = XmlDatasetLoader().load(GOLDEN_XML)
    assert isinstance(dataset, SerializedDataset)
    assert dataset.metadata.event_ids == ["0", "1"]
    assert len(dataset.events) == 2
    assert dataset.events[0]["in"]["data_in"] == "0x3A"
    assert dataset.events[0]["golden"]["data_out"] == "0x3A"
    assert dataset.events[1]["in"]["data_in"] == "0x00"


def test_xml_loader_event_ids_are_strings_not_ints():
    dataset = XmlDatasetLoader().load(GOLDEN_XML)
    assert all(isinstance(eid, str) for eid in dataset.metadata.event_ids)


def test_xml_loader_populates_real_schema_and_semantic_metadata():
    dataset = XmlDatasetLoader().load(GOLDEN_XML)
    assert dataset.metadata.schema == DATASET_SCHEMA
    assert dataset.metadata.schema.name == "forge.dataset"
    assert len(dataset.metadata.semantic.source_content_hash) == 64  # sha256 hex
    assert dataset.metadata.environment.source_path == str(GOLDEN_XML)


def test_xml_loader_units_honestly_absent_for_unitless_dataset():
    dataset = XmlDatasetLoader().load(GOLDEN_XML)
    assert dataset.metadata.units is None
    assert dataset.metadata.seed is None


def test_xml_loader_generic_across_disjoint_event_shapes(tmp_path: Path):
    """The loader is tag-name-agnostic at the event-field level — a
    completely different real event shape (matching trigger_demo's
    disjoint schema, not passthrough_demo's) parses without any
    per-plugin field-name hardcoding in the loader itself."""
    xml_path = tmp_path / "custom.xml"
    xml_path.write_text(
        '<trigger_events>'
        '<event id="run-1"><hits count="3"/><window start="10" end="20"/></event>'
        '</trigger_events>'
    )
    dataset = XmlDatasetLoader().load(xml_path)
    assert dataset.metadata.event_ids == ["run-1"]
    assert dataset.events[0]["hits"] == {"count": "3"}
    assert dataset.events[0]["window"] == {"start": "10", "end": "20"}


# ── JsonDatasetLoader against the real generated JSON ───────────────────

def test_json_loader_reads_real_generated_golden_json():
    dataset = JsonDatasetLoader().load(GOLDEN_JSON)
    assert dataset.metadata.event_ids == ["0", "1"]
    assert dataset.events[0]["in"]["data_in"] == "0x3A"
    assert dataset.metadata.generator_version == "1.0"


def test_json_loader_events_match_xml_loader_byte_for_byte():
    """Format parity: the same logical dataset, two different file
    formats, produces identical event content."""
    xml_dataset = XmlDatasetLoader().load(GOLDEN_XML)
    json_dataset = JsonDatasetLoader().load(GOLDEN_JSON)
    assert xml_dataset.events == json_dataset.events
    assert xml_dataset.metadata.event_ids == json_dataset.metadata.event_ids
    assert xml_dataset.metadata.semantic.source_content_hash == json_dataset.metadata.semantic.source_content_hash


def test_json_loader_metadata_fields_are_real_top_level_json_keys(tmp_path: Path):
    """Unlike XML, JSON is a natural home for DatasetMetadata's fields as
    real top-level keys — closing the "no dataset metadata exists
    anywhere" gap for datasets that adopt this format."""
    events = [{"x": 1}]
    payload = {
        "event_ids": ["0"],
        "events": events,
        "semantic": {"source_content_hash": compute_events_content_hash(events), "adapter_id": "custom-adapter"},
        "units": {"x": "GeV"},
        "seed": 42,
        "generator_version": "2.3.1",
    }
    path = tmp_path / "d.json"
    path.write_text(json.dumps(payload))

    dataset = JsonDatasetLoader().load(path)
    assert dataset.metadata.semantic.adapter_id == "custom-adapter"
    assert dataset.metadata.units == {"x": "GeV"}
    assert dataset.metadata.seed == 42
    assert dataset.metadata.generator_version == "2.3.1"


def test_json_loader_rejects_declared_hash_mismatch(tmp_path: Path):
    """A declared source_content_hash is never trusted — it's always
    recomputed and a mismatch is a real, reported error."""
    payload = {
        "event_ids": ["0"],
        "events": [{"x": 1}],
        "semantic": {"source_content_hash": "0" * 64},  # deliberately wrong
    }
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(DatasetContentHashError) as exc_info:
        JsonDatasetLoader().load(path)
    assert "mismatch" in str(exc_info.value).lower()
    assert exc_info.value.action  # every ForgeVerifyError must carry an action


def test_json_loader_accepts_correct_declared_hash(tmp_path: Path):
    events = [{"x": 1}]
    payload = {
        "event_ids": ["0"], "events": events,
        "semantic": {"source_content_hash": compute_events_content_hash(events)},
    }
    path = tmp_path / "ok.json"
    path.write_text(json.dumps(payload))
    dataset = JsonDatasetLoader().load(path)  # must not raise
    assert dataset.metadata.semantic.source_content_hash == compute_events_content_hash(events)


def test_json_loader_no_declared_hash_is_fine():
    """A JSON dataset with no declared hash at all is not an error — the
    loader just computes one fresh."""
    dataset = JsonDatasetLoader().load(GOLDEN_JSON)
    assert dataset.metadata.semantic.source_content_hash


# ── Format-loader registry ──────────────────────────────────────────────

def test_get_format_loader_resolves_by_suffix():
    assert isinstance(get_format_loader(GOLDEN_XML), XmlDatasetLoader)
    assert isinstance(get_format_loader(GOLDEN_JSON), JsonDatasetLoader)


def test_get_format_loader_unknown_suffix_raises():
    with pytest.raises(ValueError, match="No dataset format loader"):
        get_format_loader(Path("dataset.root"))


def test_register_format_loader_adds_new_suffix():
    class _FakeLoader:
        def load(self, path):
            return SerializedDataset(events=[], metadata=DatasetMetadata(
                schema=DATASET_SCHEMA, event_ids=[],
                semantic=SemanticMetadata(source_content_hash="x"),
                environment=EnvironmentMetadata(source_path=str(path), generated_at="now"),
            ))

    register_format_loader(".fakefmt", _FakeLoader())
    assert isinstance(get_format_loader(Path("d.fakefmt")), _FakeLoader)


# ── Content-hash determinism + tamper detection ─────────────────────────

def test_content_hash_deterministic_for_same_events():
    events = [{"a": 1, "b": {"c": 2}}]
    assert compute_events_content_hash(events) == compute_events_content_hash(events)


def test_content_hash_independent_of_dict_key_order():
    events_a = [{"a": 1, "b": 2}]
    events_b = [{"b": 2, "a": 1}]
    assert compute_events_content_hash(events_a) == compute_events_content_hash(events_b)


def test_content_hash_changes_when_event_value_changes():
    original = compute_events_content_hash([{"data_out": "0x3A"}])
    changed = compute_events_content_hash([{"data_out": "0x00"}])
    assert original != changed


def test_content_hash_same_file_loaded_twice_matches(tmp_path: Path):
    path = tmp_path / "d.xml"
    path.write_text(GOLDEN_XML.read_text())
    h1 = XmlDatasetLoader().load(path).metadata.semantic.source_content_hash
    h2 = XmlDatasetLoader().load(path).metadata.semantic.source_content_hash
    assert h1 == h2


def test_content_hash_one_event_byte_changed_flagged_by_json_loader(tmp_path: Path):
    """Editing one real event value must change the computed hash and be
    caught as a mismatch against a stale declared hash — the tamper-
    detection half of the corrected design."""
    events = [{"data_out": "0x3A"}]
    correct_hash = compute_events_content_hash(events)
    payload = {"event_ids": ["0"], "events": events, "semantic": {"source_content_hash": correct_hash}}
    path = tmp_path / "d.json"
    path.write_text(json.dumps(payload))
    JsonDatasetLoader().load(path)  # sanity: correct hash loads fine

    # Now mutate one event's value on disk, in place, without updating the
    # declared hash — exactly a hand-edited/tampered file.
    tampered = json.loads(path.read_text())
    tampered["events"][0]["data_out"] = "0x00"
    path.write_text(json.dumps(tampered))
    with pytest.raises(DatasetContentHashError):
        JsonDatasetLoader().load(path)


def test_content_hash_moving_file_to_different_directory_is_identical(tmp_path: Path):
    """source_path is genuinely excluded from the hash — relocating a
    dataset file must not change its semantic identity."""
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b" / "nested"
    dir_a.mkdir()
    dir_b.mkdir(parents=True)
    (dir_a / "d.xml").write_text(GOLDEN_XML.read_text())
    (dir_b / "d.xml").write_text(GOLDEN_XML.read_text())

    hash_a = XmlDatasetLoader().load(dir_a / "d.xml").metadata.semantic.source_content_hash
    hash_b = XmlDatasetLoader().load(dir_b / "d.xml").metadata.semantic.source_content_hash
    assert hash_a == hash_b


def test_environment_metadata_excluded_fields_do_not_affect_hash():
    """The hash calculation excludes every EnvironmentMetadata field
    (source_path, generated_at, host) by construction — it never even sees
    them, since it hashes only `events`."""
    dataset_1 = XmlDatasetLoader().load(GOLDEN_XML)
    dataset_2 = XmlDatasetLoader().load(GOLDEN_XML)
    # generated_at is a real timestamp computed at load time — these two
    # loads happen at (probably) different instants, but the hash matches.
    assert dataset_1.metadata.semantic.source_content_hash == dataset_2.metadata.semantic.source_content_hash
