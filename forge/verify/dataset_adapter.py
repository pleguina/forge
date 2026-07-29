#!/usr/bin/env python3
"""Project dataset-adapter protocol (Phase 7, slice 7.4b — §7.3, layer B).

This is layer B of two — **project-owned**, deliberately a separate
protocol from layer A's format loaders (:mod:`forge.verify.dataset_format`),
not an extension of them.

The architectural boundary (the load-bearing sentence of the whole dataset
design, not a footnote):

    The project owns raw-data interpretation, preprocessing, domain
    mapping, the golden model, and comparison semantics. FORGE owns the
    adapter protocol, contract validation, canonical dataset envelope,
    manifests, hashing, backend serialization, execution, result
    collection, and reporting.

Adapters are selected **explicitly by ``adapter_id``, never inferred from a
file suffix** — two ``.csv`` files, or two ``.root`` files, can require
completely different domain semantics. This is the opposite of layer A's
suffix dispatch, deliberately: a file's *bytes* are unambiguous given its
suffix; what those bytes *mean* is not.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from forge.verify.dataset_format import DatasetMetadata, SerializedDataset


# ── Source and canonical dataset ────────────────────────────────────────

@dataclass(frozen=True)
class DatasetSource:
    """Where a project's data comes from — one of two cases.

    ``serialized`` — an already-FORGE-shaped file, wrapping a layer-A
    :class:`~forge.verify.dataset_format.SerializedDataset` (the common
    case: a dataset that was already ``.xml``/``.json`` and went through a
    format loader).

    ``raw_path`` — genuinely external raw data (a ROOT file, an image
    directory, a PCAP capture) that was never FORGE-shaped in the first
    place. Read directly by the project's own adapter code — no layer-A
    format loader is involved at all for this case.

    Exactly one of the two is set; an adapter that needs the other must
    say so via its own error, not have the framework guess.
    """
    serialized: "SerializedDataset | None" = None
    raw_path:   "Path | None" = None


@dataclass(frozen=True)
class CanonicalDataset:
    """FORGE's own canonical representation — the actual "FORGE transaction"
    set the rest of the verification pipeline consumes from this point on.

    Structurally similar to :class:`SerializedDataset` (events + metadata)
    but a distinct type on purpose: a `SerializedDataset` is a layer-A,
    raw-file-shaped intermediate that may or may not even be involved
    (depending on the adapter and its `DatasetSource`), while a
    `CanonicalDataset` is always what layer B actually produced.
    """
    events:   "list[dict[str, Any]]"
    metadata: DatasetMetadata


# ── Adapter protocol ─────────────────────────────────────────────────────

class ProjectDatasetAdapter(Protocol):
    adapter_id:      str
    adapter_version: str

    def materialize(
        self,
        source: DatasetSource,
        config: "Mapping[str, object]",
    ) -> CanonicalDataset: ...


# ── Registry — keyed by explicit adapter_id, never suffix ──────────────

_ADAPTERS: "dict[str, ProjectDatasetAdapter]" = {}


def register_dataset_adapter(adapter_id: str, adapter: ProjectDatasetAdapter) -> None:
    """Register *adapter* under *adapter_id*.

    Mirrors :func:`forge.verify.dataset_format.register_format_loader`'s
    shape, but keyed on an explicit id string, not a suffix — two datasets
    with the same file suffix can resolve to two completely different
    adapters.
    """
    _ADAPTERS[adapter_id] = adapter


def get_dataset_adapter(adapter_id: str) -> ProjectDatasetAdapter:
    """Return the registered adapter for *adapter_id*.

    Raises:
        ValueError: no adapter is registered under that id.
    """
    adapter = _ADAPTERS.get(adapter_id)
    if adapter is None:
        known = ", ".join(sorted(_ADAPTERS)) or "(none registered)"
        raise ValueError(
            f"No dataset adapter registered under id {adapter_id!r}. "
            f"Registered adapter ids: {known}"
        )
    return adapter


def list_registered_dataset_adapters() -> "list[str]":
    """Return the sorted list of registered adapter ids."""
    return sorted(_ADAPTERS)
