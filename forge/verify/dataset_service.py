#!/usr/bin/env python3
"""Unified dataset service (release-plan Phase 10, slice 10.0A —
preflight.md §7).

A thin, single entry point over the two existing dataset layers —
:mod:`forge.verify.dataset_format` (layer A: format loaders, envelope,
hashing) and :mod:`forge.verify.dataset_adapter` (layer B: project-owned
adapters) — so every command path that needs a dataset goes through the
same code, instead of some paths reading raw XML directly and others
going through the adapter registry. No new hashing or parsing logic lives
here: every method below delegates to primitives that already exist.

Before this module, two real bypasses existed:
  * ``forge.core.cli.groups.test``'s ``_enumerate_xml_event_ids`` parsed
    XML directly with :mod:`xml.etree.ElementTree`, never touching layer
    A's loaders or layer B's adapters at all.
  * A plugin's own ``gen_stimulus.py`` (the ``svh_include`` stimulus
    path) called layer A's :func:`~forge.verify.dataset_format.get_format_loader`
    directly, never layer B.

Both are retired to route through :class:`DatasetService` instead (see
``forge/core/cli/groups/test.py`` and
``plugins/passthrough_demo/forge/verify/tools/gen_stimulus.py``).
"""
from __future__ import annotations

import dataclasses
from typing import Any, Mapping

from forge.core.utils.content_hash import compute_preprocessing_hash
from forge.verify.dataset_adapter import CanonicalDataset, DatasetSource, get_dataset_adapter
from forge.verify.dataset_format import SerializedDataset, compute_events_content_hash


class DatasetService:
    """The one entry point every command path should use for dataset
    loading, event selection, materialization, and identity."""

    def load(self, source: DatasetSource) -> SerializedDataset:
        """Resolve *source* into a :class:`SerializedDataset`.

        If *source* already carries a ``serialized`` value, it's returned
        unchanged (idempotent passthrough). Otherwise ``source.raw_path``
        is read as a bare FORGE-shaped file path via layer A's format-
        loader registry — the narrower of the two meanings
        :class:`~forge.verify.dataset_adapter.DatasetSource`'s own
        docstring documents for ``raw_path`` (that docstring's "genuinely
        external raw data" case applies at :meth:`materialize`, where a
        project adapter owns interpretation; here, a caller has nothing
        but a path and needs the one FORGE-shaped file behind it loaded).
        """
        if source.serialized is not None:
            return source.serialized
        if source.raw_path is not None:
            from forge.verify.dataset_format import get_format_loader
            return get_format_loader(source.raw_path).load(source.raw_path)
        raise ValueError("DatasetSource has neither `serialized` nor `raw_path` set")

    def list_events(self, dataset: CanonicalDataset) -> "list[str]":
        return list(dataset.metadata.event_ids)

    def select_events(self, dataset: CanonicalDataset, ids: "list[str]") -> CanonicalDataset:
        """Return a new :class:`CanonicalDataset` containing only the
        events in *ids*, in the order *ids* was given (not the source
        dataset's own order — callers pass an explicit order because they
        want it honored, e.g. ``--event-list``).

        Raises:
            ValueError: any id in *ids* is not present in *dataset*.
        """
        by_id = dict(zip(dataset.metadata.event_ids, dataset.events))
        missing = [eid for eid in ids if eid not in by_id]
        if missing:
            raise ValueError(f"event id(s) not found in dataset: {missing!r}")

        selected_events = [by_id[eid] for eid in ids]
        new_semantic = dataclasses.replace(
            dataset.metadata.semantic,
            source_content_hash=compute_events_content_hash(selected_events),
        )
        new_metadata = dataclasses.replace(
            dataset.metadata, event_ids=list(ids), semantic=new_semantic,
        )
        return CanonicalDataset(events=selected_events, metadata=new_metadata)

    def materialize(
        self,
        source: DatasetSource,
        adapter_id: str,
        config: "Mapping[str, object]",
    ) -> CanonicalDataset:
        """Materialize *source* via the adapter registered under
        *adapter_id*, then re-stamp the result's
        ``semantic.preprocessing_hash`` — the **first real producer** of
        that field; every existing dataset leaves it ``None``.
        """
        adapter = get_dataset_adapter(adapter_id)
        canonical = adapter.materialize(source, config)

        preprocessing_config: "dict[str, Any]" = {
            "adapter_id": adapter.adapter_id,
            "adapter_version": adapter.adapter_version,
            "source_content_hash": canonical.metadata.semantic.source_content_hash,
            **dict(config),
        }
        new_semantic = dataclasses.replace(
            canonical.metadata.semantic,
            preprocessing_hash=compute_preprocessing_hash(preprocessing_config),
        )
        new_metadata = dataclasses.replace(canonical.metadata, semantic=new_semantic)
        return dataclasses.replace(canonical, metadata=new_metadata)

    def compute_identity(self, dataset: CanonicalDataset) -> str:
        """The dataset's content-hash identity — the same primitive
        :func:`~forge.verify.dataset_format.compute_events_content_hash`
        already provides, reused unchanged (no new hashing scheme)."""
        return compute_events_content_hash(dataset.events)
