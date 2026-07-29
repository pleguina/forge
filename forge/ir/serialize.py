"""
Serialization, content hashing, and coarse diffing for the canonical IR.

Deliberately separate from ``model.py`` (pure data) and ``build.py``
(construction from existing loaders) so this module can be reused by any
future consumer (CLI, visual explorer, CI) without pulling in either.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any, Dict

from .model import ResolvedProject


def to_json_dict(project: ResolvedProject) -> Dict[str, Any]:
    """Return a plain, JSON-serializable dict for *project*.

    ``dataclasses.asdict`` is safe here because every IR dataclass field is
    a primitive, a list/dict of primitives, or a nested dataclass — no
    sets, no custom objects.
    """
    return asdict(project)


def to_json_str(project: ResolvedProject, *, indent: int | None = 2) -> str:
    """Human/CI-friendly JSON rendering, with sorted keys for stable diffs."""
    return json.dumps(to_json_dict(project), indent=indent, sort_keys=True)


def _canonical_design_json(project: ResolvedProject) -> str:
    """The subset of the IR that participates in the content hash.

    ``generated_from`` (absolute local paths to the design/contracts/ip_info
    files used to build the IR) and ``forge_version`` are intentionally
    excluded: a content hash should answer "did the resolved design change?",
    not "did the tool version or the caller's filesystem layout change?".
    The IR carries no timestamp at all, so no time-based redaction is
    needed either.
    """
    payload = {
        "schema_version": project.schema_version,
        "design": asdict(project.design),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def content_hash(project: ResolvedProject) -> str:
    """Deterministic sha256 hex digest of the resolved design's content."""
    return hashlib.sha256(_canonical_design_json(project).encode("utf-8")).hexdigest()


def diff_projects(a: ResolvedProject, b: ResolvedProject) -> Dict[str, Any]:
    """A coarse but real diff between two IR snapshots.

    Reports content-hash equality plus added/removed/changed instance and
    connection IDs by stable-ID comparison. Deep semantic diffing (e.g.
    "this instance's clock domain changed") is out of scope for this
    slice — see ``docs/development/release-readiness.md``.
    """
    hash_a, hash_b = content_hash(a), content_hash(b)

    inst_a = {i.id: asdict(i) for i in a.design.instances}
    inst_b = {i.id: asdict(i) for i in b.design.instances}
    conn_a = {c.id: asdict(c) for c in a.design.connections}
    conn_b = {c.id: asdict(c) for c in b.design.connections}

    def _diff_ids(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
        added = sorted(set(after) - set(before))
        removed = sorted(set(before) - set(after))
        changed = sorted(k for k in (set(before) & set(after)) if before[k] != after[k])
        return {"added": added, "removed": removed, "changed": changed}

    return {
        "hash_equal": hash_a == hash_b,
        "hash_a": hash_a,
        "hash_b": hash_b,
        "instances": _diff_ids(inst_a, inst_b),
        "connections": _diff_ids(conn_a, conn_b),
    }
