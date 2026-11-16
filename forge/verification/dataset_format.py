#!/usr/bin/env python3
"""Dataset neutral envelope and format loaders (layer A).

This is layer A of two. **Layer A only answers "how does FORGE read an
already-FORGE-shaped dataset file (XML or JSON) into a neutral, in-memory
envelope."** It says nothing about interpreting arbitrary external raw
data (ROOT files, image folders, PCAP captures) — that is layer B
(``forge.verification.dataset_adapter``), a deliberately separate
protocol with a different owner:

  * **This layer (A)** — FORGE-owned. Loads a file whose bytes are
    unambiguous given its suffix (a ``.xml`` file is read one way
    regardless of what its events mean domain-wise) into
    :class:`SerializedDataset`.
  * **Layer B** — project-owned. Interprets what the loaded (or raw
    external) data actually *means* and turns it into FORGE transactions.

Suffix dispatch is appropriate at this layer only, via
:func:`register_format_loader`/:func:`get_format_loader` — never at layer
B, where two files of the same suffix can need completely different
domain semantics (see ``dataset_adapter.py``'s module docstring).
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from forge.core.utils.content_hash import hash_bytes
from forge.verification.exceptions import DatasetContentHashError
from forge.verification.results import ArtifactSchema


DATASET_SCHEMA = ArtifactSchema("forge.dataset", "1.0")


# ── Metadata dataclasses ────────────────────────────────────────────────

@dataclass(frozen=True)
class SemanticMetadata:
    """The part of a dataset's identity that the content hash covers.

    Deliberately excludes anything about *where* the file lives or *when*
    it was loaded (that's :class:`EnvironmentMetadata`) — moving a dataset
    file to a different directory, or reloading it at a different time,
    must not change its semantic identity.
    """
    source_content_hash: str
    adapter_id:          "str | None" = None
    adapter_version:     "str | None" = None
    preprocessing_hash:  "str | None" = None


@dataclass(frozen=True)
class EnvironmentMetadata:
    """Where/when this envelope was produced — excluded from the content
    hash on purpose (see :class:`SemanticMetadata`)."""
    source_path:  str
    generated_at: str
    host:         "str | None" = None


@dataclass(frozen=True)
class DatasetMetadata:
    """Real dataset metadata: schema version, event ids, provenance, units,
    seed, generator version.

    ``event_ids`` is always ``list[str]``, never ``int`` — real external
    identifiers (``"run-355100-event-1842"``, ``"frame_000013"``) are not
    guaranteed to be small contiguous integers. The separate, purely
    internal, always-contiguous ``event_index`` that memory-indexed
    stimulus actually addresses by is a position in
    :attr:`SerializedDataset.events` — computed by the caller from this
    list's index, not stored here.

    ``units`` is member-scoped (qualified paths into an event dict, e.g.
    ``{"pixels_in.data": "ADC count"}``), never a bare top-level
    field-name-to-unit mapping — a dataset's fields are themselves
    per-source and nested.
    """
    schema:      ArtifactSchema
    event_ids:   "list[str]"
    semantic:    SemanticMetadata
    environment: EnvironmentMetadata
    units:       "dict[str, str] | None" = None
    seed:        "int | None" = None
    generator_version: "str | None" = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema.to_dict(),
            "event_ids": list(self.event_ids),
            "semantic": {
                "source_content_hash": self.semantic.source_content_hash,
                "adapter_id": self.semantic.adapter_id,
                "adapter_version": self.semantic.adapter_version,
                "preprocessing_hash": self.semantic.preprocessing_hash,
            },
            "environment": {
                "source_path": self.environment.source_path,
                "generated_at": self.environment.generated_at,
                "host": self.environment.host,
            },
            "units": self.units,
            "seed": self.seed,
            "generator_version": self.generator_version,
        }


@dataclass(frozen=True)
class SerializedDataset:
    """The neutral envelope layer A produces.

    ``events`` is kept as plain ``dict``s — event *shape* is inherently
    per-source (passthrough_demo's and trigger_demo's XML schemas are
    completely disjoint); this layer's job is loading + metadata, not
    imposing one universal event structure.
    """
    events:   "list[dict[str, Any]]"
    metadata: DatasetMetadata


# ── Content hashing ──────────────────────────────────────────────────────

def compute_events_content_hash(events: "list[dict[str, Any]]") -> str:
    """The real content hash over canonicalized event content.

    Reuses :mod:`forge.core.utils.content_hash` (no new hashing scheme).
    Canonicalized via sorted-key, separator-normalized JSON so the same
    logical events always hash the same regardless of dict-key insertion
    order. Deliberately hashes only *events* — never any
    :class:`EnvironmentMetadata` field — so relocating or reloading a
    dataset file never changes its computed hash.
    """
    canonical = json.dumps(events, sort_keys=True, separators=(",", ":"))
    return hash_bytes(canonical.encode("utf-8"))


# ── Format-loader protocol and registry ─────────────────────────────────

class DatasetFormatLoader(Protocol):
    def load(self, path: Path) -> SerializedDataset: ...


_FORMAT_LOADERS: "dict[str, DatasetFormatLoader]" = {}


def register_format_loader(suffix: str, loader: DatasetFormatLoader) -> None:
    """Register *loader* for file suffix *suffix* (e.g. ``".xml"``).

    Mirrors :mod:`forge.verification.backend_registry`'s registration pattern at
    the same small scale.
    """
    _FORMAT_LOADERS[suffix.lower()] = loader


_NAMED_LOADERS: "dict[str, DatasetFormatLoader]" = {}


def register_named_format(name: str, loader: DatasetFormatLoader) -> None:
    """Register *loader* under a format name a flow or command can state (``format: empv2``)."""
    _NAMED_LOADERS[name.lower()] = loader


def known_formats() -> "list[str]":
    return sorted(_NAMED_LOADERS)


def get_format_loader(path: "str | Path", fmt: "str | None" = None) -> DatasetFormatLoader:
    """The loader for *path*: the named *fmt* when given, else by suffix, else by content.

    A suffix such as ``.txt`` says nothing about the format, so it is never
    registered; an EMP pattern file is recognised by its header.

    Raises:
        ValueError: *fmt* is not a known format, or *path* matches none.
    """
    if fmt:
        loader = _NAMED_LOADERS.get(fmt.lower())
        if loader is None:
            raise ValueError(f"unknown dataset format {fmt!r}; known: {', '.join(known_formats())}")
        return loader
    suffix = Path(path).suffix.lower()
    loader = _FORMAT_LOADERS.get(suffix)
    if loader is not None:
        return loader
    from forge.integration.apx import is_apx_file
    from forge.integration.emp import is_pattern_file
    if is_pattern_file(Path(path)):
        return _NAMED_LOADERS["empv2"]
    if is_apx_file(Path(path)):
        return _NAMED_LOADERS["apx"]
    known = ", ".join(sorted(_FORMAT_LOADERS)) or "(none registered)"
    raise ValueError(
        f"No dataset format loader registered for suffix {suffix!r} "
        f"(path: {path}). Registered suffixes: {known}; name the format "
        f"(one of {', '.join(known_formats())}) for any other file"
    )


# ── XmlDatasetLoader — the reference/default loader ─────────────────────

def _parse_event_element(elem: "ET.Element") -> "dict[str, Any]":
    """Generic, tag-name-agnostic per-event field extraction.

    For each child of ``<event id="N">``, use the child's own tag as the
    field key and its attributes as the value — e.g. passthrough_demo's
    ``<in data_in="0x3A" .../>`` becomes ``{"in": {"data_in": "0x3A"}}``.
    Repeated child tags collect into a list, so no per-plugin field-name
    hardcoding lives in the loader itself.
    """
    fields: "dict[str, Any]" = {}
    for child in elem:
        attrs = dict(child.attrib)
        if child.tag in fields:
            existing = fields[child.tag]
            if isinstance(existing, list):
                existing.append(attrs)
            else:
                fields[child.tag] = [existing, attrs]
        else:
            fields[child.tag] = attrs
    return fields


class XmlDatasetLoader:
    """Reference/default loader for ``.xml``-suffixed dataset files.

    Scans ``<event id="N">`` elements under any root tag, so the reference
    plugins' different XML schemas load without naming their fields.
    """

    def load(self, path: Path) -> SerializedDataset:
        path = Path(path)
        root = ET.parse(path).getroot()

        events: "list[dict[str, Any]]" = []
        event_ids: "list[str]" = []
        for elem in root.iter("event"):
            raw_id = elem.get("id")
            if raw_id is None:
                continue
            event_ids.append(str(raw_id))
            events.append(_parse_event_element(elem))

        metadata = DatasetMetadata(
            schema=DATASET_SCHEMA,
            event_ids=event_ids,
            semantic=SemanticMetadata(
                source_content_hash=compute_events_content_hash(events),
            ),
            environment=EnvironmentMetadata(
                source_path=str(path),
                generated_at=datetime.now(timezone.utc).isoformat(),
            ),
        )
        return SerializedDataset(events=events, metadata=metadata)


# ── JsonDatasetLoader — second required format, real metadata ───────────

class JsonDatasetLoader:
    """Loader for ``.json``-suffixed dataset files.

    Unlike XML, JSON is a natural home for :class:`DatasetMetadata`'s
    fields as real top-level keys (``schema``/``semantic``/``environment``/
    ``units``/``seed``/``generator_version`` actually present in the file,
    not bolted on separately) — closing the "no dataset metadata exists
    anywhere" gap for real, for datasets that adopt this format.

    A declared ``semantic.source_content_hash`` is never trusted — the
    real hash is always recomputed over the loaded ``events`` and a
    mismatch raises :class:`~forge.verification.exceptions.DatasetContentHashError`.
    """

    def load(self, path: Path) -> SerializedDataset:
        path = Path(path)
        payload = json.loads(path.read_text())

        events: "list[dict[str, Any]]" = list(payload.get("events") or [])
        event_ids = [str(e) for e in (payload.get("event_ids") or [])]

        declared_semantic = payload.get("semantic") or {}
        declared_hash = declared_semantic.get("source_content_hash")
        real_hash = compute_events_content_hash(events)
        if declared_hash is not None and declared_hash != real_hash:
            raise DatasetContentHashError(
                f"Dataset content hash mismatch in {path}: "
                f"declared {declared_hash!r}, computed {real_hash!r}.",
                action=(
                    "Regenerate the dataset file's semantic.source_content_hash "
                    "from its actual event content, or the file has been "
                    "tampered with / hand-edited inconsistently."
                ),
                context={"path": str(path), "declared": declared_hash, "computed": real_hash},
            )

        environment_raw = payload.get("environment") or {}
        metadata = DatasetMetadata(
            schema=DATASET_SCHEMA,
            event_ids=event_ids,
            semantic=SemanticMetadata(
                source_content_hash=real_hash,
                adapter_id=declared_semantic.get("adapter_id"),
                adapter_version=declared_semantic.get("adapter_version"),
                preprocessing_hash=declared_semantic.get("preprocessing_hash"),
            ),
            environment=EnvironmentMetadata(
                source_path=str(path),
                generated_at=datetime.now(timezone.utc).isoformat(),
                host=environment_raw.get("host"),
            ),
            units=payload.get("units"),
            seed=payload.get("seed"),
            generator_version=payload.get("generator_version"),
        )
        return SerializedDataset(events=events, metadata=metadata)


# ── Registration ──────────────────────────────────────────────────────────

class EmpPatternDatasetLoader:
    """Loader for EMP pattern files (format ``empv2``, emp-fwk and CMSSW's EMPv2).

    An event is the packets (start to last valid word) that start in the same
    frame, on every link that carries one: ``{"frame": f, "links": {link:
    [word, ...]}}``, each word with its data and metadata bits, strobe
    included. A link with no packet in that frame is absent from the event,
    never paired with its next packet. Idle frames separate events and are
    not kept; the file itself is the lossless record
    (``forge.integration.emp.read_board_data``).
    """

    adapter_id = "empv2"

    def read(self, path: Path) -> Any:
        from forge.integration.emp import read_board_data
        return read_board_data(path)

    def load(self, path: Path) -> SerializedDataset:
        path = Path(path)
        from forge.integration.emp import associate_events

        board = self.read(path)
        carrying = sorted(link for link in board.links if board.packets(link))
        events: "list[dict[str, Any]]" = []
        for k, grouped in enumerate(associate_events(board, carrying, missing="empty")):
            links = {
                str(link): [
                    {"data": f"0x{w.data:016x}", "valid": w.valid, "start": w.start, "last": w.last,
                     "start_of_orbit": w.start_of_orbit, "strobe": w.strobe}
                    for w in words
                ]
                for link, (_, _, words) in grouped.items() if words
            }
            frame = min(first for first, _, words in grouped.values() if words)
            events.append({"id": str(k), "board": board.name, "frame": frame, "links": links})
        metadata = DatasetMetadata(
            schema=DATASET_SCHEMA,
            event_ids=[e["id"] for e in events],
            semantic=SemanticMetadata(
                source_content_hash=compute_events_content_hash(events),
                adapter_id=self.adapter_id,
            ),
            environment=EnvironmentMetadata(
                source_path=str(path),
                generated_at=datetime.now(timezone.utc).isoformat(),
            ),
        )
        return SerializedDataset(events=events, metadata=metadata)


class ApxPatternDatasetLoader(EmpPatternDatasetLoader):
    """APx pattern files (``forge.integration.apx``), events as for ``empv2``.

    ``apx`` reads the sideband as CMSSW writes it today, ``apx-2021`` as
    CMSSW's reader does.
    """

    def __init__(self, encoding: str = "2025") -> None:
        self.encoding = encoding
        self.adapter_id = "apx" if encoding == "2025" else f"apx-{encoding}"

    def read(self, path: Path) -> Any:
        from forge.integration.apx import read_apx
        return read_apx(path, self.encoding)


register_format_loader(".xml", XmlDatasetLoader())
register_format_loader(".json", JsonDatasetLoader())
register_named_format("xml", XmlDatasetLoader())
register_named_format("json", JsonDatasetLoader())
register_named_format("empv2", EmpPatternDatasetLoader())
register_named_format("apx", ApxPatternDatasetLoader())
register_named_format("apx-2021", ApxPatternDatasetLoader("2021"))
