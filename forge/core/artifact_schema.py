"""A neutral, dependency-free ``{name, version}`` schema tag.

Shared by every structured artifact FORGE emits (verification results,
dataset envelopes, the visual-design-explorer's ``DesignGraph``) so a
future reader can tell which shape it is looking at before assuming field
names. Lives here — not in ``forge.verify.results``, where it was first
introduced (release-plan Phase 7) — because a schema-tag concept has
nothing to do with verification specifically, and an analysis-only
consumer (``forge.analyze.design_explorer``) should not have to depend on
the verification package just to reuse it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArtifactSchema:
    """A ``{"name", "version"}`` tag carried by every structured artifact
    this phase introduces, so a reader can check compatibility before
    assuming field shape."""
    name:    str
    version: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "version": self.version}
