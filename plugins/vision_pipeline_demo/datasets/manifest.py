#!/usr/bin/env python3
"""Dataset manifest + staleness check.

Every converted dataset gets a sidecar manifest recording its adapter
identity, source-file hashes, preprocessing-config hash, canonical-event
hash, and (optionally) serialized-XML and golden-model hashes, so a
reader can tell exactly which stage of the pipeline produced a given
dataset and verify none of its inputs silently changed. No new hashing
scheme is introduced here: every hash reuses
:mod:`forge.core.utils.content_hash` primitives already established
elsewhere in FORGE (``compute_preprocessing_hash``,
``hash_file``/``hash_bytes``), so a dataset's hashes stay comparable with
hashes computed anywhere else in the framework.

Staleness detection is a pure content-hash comparison, never a
modification-time check — the same principle ``content_hash.py``'s own
docstring states ("Modification times may remain an optimization, but
must not be the source of truth"). A hash-based check survives file
copies, checkouts, and touched-but-unchanged files without false
positives.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from forge.core.utils.content_hash import compute_preprocessing_hash, hash_bytes, hash_file
from forge.verify.results import ArtifactSchema

from .model import DatasetEvent, dataset_event_to_dict

MANIFEST_SCHEMA = ArtifactSchema("forge.dataset_manifest", "1.0")


def compute_canonical_events_hash(events: "Sequence[DatasetEvent]") -> str:
    """Content hash over the canonical (pre-flatten) event model itself —
    distinct from :func:`~forge.verify.dataset_format.compute_events_content_hash`,
    which hashes the *flattened*, per-pixel FORGE event-dict shape. Both
    are recorded in the manifest (``canonical_events`` vs
    ``serialized_dataset``) because they answer different questions: did
    the adapter's own output change, vs. did the on-disk serialization of
    it change.

    Deliberately excludes ``source_metadata`` — adapters (e.g.
    ``ImageFolderAdapter``) populate it with the absolute local source
    path, which must never affect a *semantic* content hash: relocating
    a source directory must not change a dataset's semantic identity,
    the same principle :class:`~forge.verify.dataset_format.SemanticMetadata`
    already applies by excluding :class:`~forge.verify.dataset_format.EnvironmentMetadata`.
    A real, reproducible bug was found here during development: without
    this exclusion, copying a source image directory to a new path
    changed ``canonical_events_hash`` even though every pixel was
    byte-identical.
    """
    canonical = json.dumps(
        [
            {k: v for k, v in dataset_event_to_dict(e).items() if k != "source_metadata"}
            for e in events
        ],
        sort_keys=True, separators=(",", ":"),
    )
    return hash_bytes(canonical.encode("utf-8"))


@dataclass(frozen=True)
class DatasetManifest:
    """The real ``forge.dataset_manifest`` sidecar."""

    dataset_id:        str
    adapter_id:        str
    adapter_version:   str
    event_count:       int
    source_files:      "Mapping[str, str]"           # relative path -> sha256
    preprocessing_config_hash: str
    canonical_events_hash: str
    preprocessing:     "Mapping[str, Any]" = field(default_factory=dict)
    serialized_dataset_hash: "str | None" = None
    serialization:      "Mapping[str, Any]" = field(default_factory=dict)
    golden_model:       "Mapping[str, Any] | None" = None
    created_by:         str = "vision_pipeline_demo"
    deterministic:      bool = True
    schema:             ArtifactSchema = MANIFEST_SCHEMA
    # The exact adapter constructor kwargs used to produce this dataset --
    # what `rebuild` needs to re-materialize a dataset from the manifest
    # alone, with nothing but this file and the original source files.
    adapter_config:     "Mapping[str, Any]" = field(default_factory=dict)

    def to_dict(self) -> "dict[str, Any]":
        return {
            "schema": self.schema.to_dict(),
            "dataset": {
                "id": self.dataset_id,
                "adapter": self.adapter_id,
                "adapter_version": self.adapter_version,
                "created_by": self.created_by,
                "event_count": self.event_count,
                "deterministic": self.deterministic,
            },
            "hashes": {
                "source_files": dict(self.source_files),
                "preprocessing_config": self.preprocessing_config_hash,
                "canonical_events": self.canonical_events_hash,
                "serialized_dataset": self.serialized_dataset_hash,
            },
            "preprocessing": dict(self.preprocessing),
            "serialization": dict(self.serialization),
            "golden_model": dict(self.golden_model) if self.golden_model is not None else None,
            "adapter_config": dict(self.adapter_config),
        }

    @staticmethod
    def from_dict(payload: "Mapping[str, Any]") -> "DatasetManifest":
        schema_raw = payload.get("schema") or {}
        dataset = payload.get("dataset") or {}
        hashes = payload.get("hashes") or {}
        return DatasetManifest(
            schema=ArtifactSchema(
                schema_raw.get("name", MANIFEST_SCHEMA.name),
                schema_raw.get("version", MANIFEST_SCHEMA.version),
            ),
            dataset_id=dataset["id"],
            adapter_id=dataset["adapter"],
            adapter_version=dataset["adapter_version"],
            created_by=dataset.get("created_by", "vision_pipeline_demo"),
            event_count=dataset["event_count"],
            deterministic=dataset.get("deterministic", True),
            source_files=dict(hashes.get("source_files") or {}),
            preprocessing_config_hash=hashes["preprocessing_config"],
            canonical_events_hash=hashes["canonical_events"],
            serialized_dataset_hash=hashes.get("serialized_dataset"),
            preprocessing=dict(payload.get("preprocessing") or {}),
            serialization=dict(payload.get("serialization") or {}),
            golden_model=payload.get("golden_model"),
            adapter_config=dict(payload.get("adapter_config") or {}),
        )


def build_manifest(
    *,
    dataset_id: str,
    adapter_id: str,
    adapter_version: str,
    events: "Sequence[DatasetEvent]",
    preprocessing_config: "Mapping[str, object]",
    preprocessing: "Mapping[str, Any] | None" = None,
    source_paths: "Sequence[Path]" = (),
    source_root: "Path | None" = None,
    serialized_xml_path: "Path | None" = None,
    serialization: "Mapping[str, Any] | None" = None,
    golden_model: "Mapping[str, Any] | None" = None,
    adapter_config: "Mapping[str, Any] | None" = None,
) -> DatasetManifest:
    """Build a real :class:`DatasetManifest` from *events* and the inputs
    that produced them.

    ``source_files`` keys are recorded relative to *source_root* (or, if
    not given, as bare filenames) — never an absolute local path, so that
    relocating a source directory does not change the recorded manifest
    or the portable semantic dataset hash.
    """
    source_files: "dict[str, str]" = {}
    for p in source_paths:
        p = Path(p)
        key = str(p.relative_to(source_root)) if source_root is not None else p.name
        source_files[key] = hash_file(p)

    serialized_hash = hash_bytes(Path(serialized_xml_path).read_bytes()) if serialized_xml_path is not None else None

    return DatasetManifest(
        dataset_id=dataset_id,
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        event_count=len(events),
        source_files=source_files,
        preprocessing_config_hash=compute_preprocessing_hash(preprocessing_config),
        canonical_events_hash=compute_canonical_events_hash(events),
        preprocessing=dict(preprocessing) if preprocessing is not None else {},
        serialized_dataset_hash=serialized_hash,
        serialization=dict(serialization) if serialization is not None else {},
        golden_model=golden_model,
        adapter_config=dict(adapter_config) if adapter_config is not None else {},
    )


def write_manifest(manifest: DatasetManifest, path: "Path | str") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(manifest.to_dict(), sort_keys=False), encoding="utf-8")
    return path


def load_manifest(path: "Path | str") -> DatasetManifest:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return DatasetManifest.from_dict(payload)


def check_staleness(manifest: DatasetManifest, *, source_root: "Path | str") -> "list[str]":
    """Compare *manifest*'s recorded ``source_files`` hashes against the
    real current file contents under *source_root*. Returns a list of
    human-readable staleness reasons — empty means fresh.

    Deliberately hash-based, never modification-time-based (see module
    docstring) — touching a file without changing its bytes must never
    report staleness, and restoring a file's original bytes after an edit
    must clear it.
    """
    source_root = Path(source_root)
    reasons: "list[str]" = []
    for rel_path, recorded_hash in manifest.source_files.items():
        full_path = source_root / rel_path
        if not full_path.exists():
            reasons.append(f"missing source file: {rel_path}")
            continue
        current_hash = hash_file(full_path)
        if current_hash != recorded_hash:
            reasons.append(
                f"source file changed: {rel_path} "
                f"(recorded {recorded_hash[:12]}..., now {current_hash[:12]}...)"
            )
    return reasons
