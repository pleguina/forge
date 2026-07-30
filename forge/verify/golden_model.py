#!/usr/bin/env python3
"""Golden-model provider protocol and runner (release-plan Phase 10,
slice 10.0A — preflight.md §5 Decision C).

This is a third, distinct ownership boundary alongside the two dataset
layers already established in :mod:`forge.verify.dataset_format` (layer
A — FORGE-owned format loading) and :mod:`forge.verify.dataset_adapter`
(layer B — project-owned raw-data interpretation):

    The project owns the algorithm — what "expected behavior" means for
    its domain. FORGE owns provider resolution, deterministic invocation,
    input-dataset identity, expected-output serialization, output
    hashing, and (in a later slice) comparison orchestration and
    reporting.

Concretely: a project implements :class:`GoldenModelProvider` and
registers it once; FORGE always invokes it through :func:`run_golden_model`,
never directly, so the identity/output hashes on the returned
:class:`ExpectedDataset` are always FORGE-computed, never
self-reported by the provider.

Scoped strictly to what slice 10.0A/10.1 need: deterministic invocation
plus real hashing. Comparison-result artifacts
(``forge.golden_comparison_result.v1``) are a separate, later concern
(slice 10.0C) and are deliberately not introduced here — a quickstart
tier's "exact comparison" is realized as real per-cycle SV-testbench
assertions computed from a provider's live output, not a new structured
artifact.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from forge.core.artifact_schema import ArtifactSchema
from forge.verify.dataset_adapter import CanonicalDataset
from forge.verify.dataset_format import compute_events_content_hash

EXPECTED_DATASET_SCHEMA = ArtifactSchema("forge.expected_dataset", "1.0")


# ── Expected-output dataset ──────────────────────────────────────────────

@dataclass(frozen=True)
class ExpectedDataset:
    """A golden model's expected-output events, with FORGE-owned identity.

    ``events``/``event_ids`` shape mirrors :class:`CanonicalDataset` —
    event *content* is domain-owned and never imposed here, only
    positionally aligned with the input dataset's ``event_ids``.

    ``input_dataset_hash``/``output_hash`` are always ``None`` as returned
    by a :class:`GoldenModelProvider` — a provider never computes its own
    identity hashes. Only :func:`run_golden_model` ever populates them
    (via :func:`dataclasses.replace`), which is what keeps "FORGE owns
    hashing" a real, enforced fact rather than a documented convention a
    provider could quietly bypass by self-reporting a hash.
    """
    schema:             ArtifactSchema
    event_ids:           "list[str]"
    events:              "list[dict[str, Any]]"
    provider_id:          str
    provider_version:     str
    input_dataset_hash:  "str | None" = None
    output_hash:         "str | None" = None

    def to_dict(self) -> "dict[str, Any]":
        return {
            "schema": self.schema.to_dict(),
            "event_ids": list(self.event_ids),
            "events": list(self.events),
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "input_dataset_hash": self.input_dataset_hash,
            "output_hash": self.output_hash,
        }


# ── Provider protocol ─────────────────────────────────────────────────────

class GoldenModelProvider(Protocol):
    provider_id:      str
    provider_version: str

    def evaluate(
        self,
        dataset: CanonicalDataset,
        config: "Mapping[str, object]",
    ) -> ExpectedDataset: ...


# ── Registry — keyed by explicit provider_id, mirrors dataset_adapter.py ──

_PROVIDERS: "dict[str, GoldenModelProvider]" = {}


def register_golden_model_provider(provider_id: str, provider: GoldenModelProvider) -> None:
    """Register *provider* under *provider_id*.

    Mirrors :func:`forge.verify.dataset_adapter.register_dataset_adapter`'s
    shape exactly — an explicit id string, never inferred.
    """
    _PROVIDERS[provider_id] = provider


def get_golden_model_provider(provider_id: str) -> GoldenModelProvider:
    """Return the registered provider for *provider_id*.

    Raises:
        ValueError: no provider is registered under that id.
    """
    provider = _PROVIDERS.get(provider_id)
    if provider is None:
        known = ", ".join(sorted(_PROVIDERS)) or "(none registered)"
        raise ValueError(
            f"No golden-model provider registered under id {provider_id!r}. "
            f"Registered provider ids: {known}"
        )
    return provider


def list_registered_golden_model_providers() -> "list[str]":
    """Return the sorted list of registered provider ids."""
    return sorted(_PROVIDERS)


# ── Deterministic invocation ──────────────────────────────────────────────

def run_golden_model(
    provider_id: str,
    dataset: CanonicalDataset,
    config: "Mapping[str, object]",
) -> ExpectedDataset:
    """Resolve *provider_id*, invoke it exactly once against *dataset*,
    and return its output re-stamped with FORGE-computed identity hashes.

    No retry, no caching in this slice — a provider is expected to be a
    pure function of ``(dataset, config)``; if that stops being true for
    some future provider, caching would need to become an explicit,
    visible decision, not a silent default.
    """
    provider = get_golden_model_provider(provider_id)
    expected = provider.evaluate(dataset, config)
    input_hash = compute_events_content_hash(dataset.events)
    output_hash = compute_events_content_hash(expected.events)
    return dataclasses.replace(
        expected, input_dataset_hash=input_hash, output_hash=output_hash,
    )
