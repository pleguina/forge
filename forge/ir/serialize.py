"""
Serialization, content hashing, and coarse diffing for the canonical IR.

Deliberately separate from ``model.py`` (pure data) and ``build.py``
(construction from existing loaders) so this module can be reused by any
future consumer (CLI, visual explorer, CI) without pulling in either.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional

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


def _design_dir(project: ResolvedProject) -> Optional[Path]:
    """The directory *project*'s design file was loaded from, when known —
    the base every absolute path in the hashed payload is made relative
    to. ``None`` when ``generated_from`` carries no design path (e.g. a
    ``ResolvedProject`` built by hand in a test)."""
    design_value = project.generated_from.get("design")
    return Path(design_value).parent if design_value else None


def _portable_path(path_str: str, base_dir: Optional[Path]) -> str:
    """Rewrite an absolute path recorded on an IR object into a path
    relative to *base_dir*, so it participates in the content hash as
    "which file" rather than "where on this filesystem". Non-absolute
    paths and a missing *base_dir* pass through unchanged."""
    p = Path(path_str)
    if base_dir is None or not p.is_absolute():
        return path_str
    try:
        return os.path.relpath(p, base_dir)
    except ValueError:
        return path_str


def _canonical_design_json(project: ResolvedProject) -> str:
    """The subset of the IR that participates in the content hash.

    ``generated_from`` (absolute local paths to the design/contracts/ip_info
    files used to build the IR) and ``forge_version`` are intentionally
    excluded: a content hash should answer "did the resolved design change?",
    not "did the tool version or the caller's filesystem layout change?".
    The IR carries no timestamp at all, so no time-based redaction is
    needed either.

    Four fields inside ``design`` itself carry the *caller's* absolute
    filesystem paths, not portable design content, and are rewritten
    relative to the design file's own directory before hashing (empirically
    confirmed to change ``content_hash()``'s output when left as-is, by
    building the same real design from two different absolute checkout
    roots — same technique as this module's regression tests):

    - ``design.source`` (a ``SourceLocation`` populated with the
      *resolved absolute path* to ``design.yml``, see ``forge/ir/build.py``)
      — diagnostic metadata (where a diagnostic points back to), dropped
      entirely rather than rewritten, since the design's own file identity
      isn't itself part of "what the design says".
    - Each module's ``contract_path`` — the resolved absolute path to its
      interface-contract YAML (``forge/topgen/ip/contract_loader.py``).
    - Each module's ``source_files`` — resolved to absolute paths by
      ``forge/topgen/config.py``'s registry loader (``_load_registry``)
      for any module that inherits identity fields from a ``modules.yml``
      registry via ``ref:``, which is the common case for both reference
      plugins.
    - Each ``design.diagnostics[]`` entry's ``location.file`` — every
      validation diagnostic attached to the design is stamped with the
      resolved absolute ``design.yml`` path
      (``forge/ir/build.py``, ``DiagnosticReference(location=SourceLocation(file=str(design_path)))``).
      Found the same way as the three fields above: a YAML-key-ordering
      determinism test (release-plan §5.4) building the identical design
      from two different tmp directories caught this as a second,
      independent leak of the same class, not called out by the original
      investigation that added the fields above.
    """
    design_dict = asdict(project.design)
    design_dict.pop("source", None)
    base_dir = _design_dir(project)
    for mod in design_dict.get("modules", []):
        if mod.get("contract_path"):
            mod["contract_path"] = _portable_path(mod["contract_path"], base_dir)
        if mod.get("source_files"):
            mod["source_files"] = [
                _portable_path(s, base_dir) for s in mod["source_files"]
            ]
    for diag in design_dict.get("diagnostics", []):
        location = diag.get("location")
        if location and location.get("file"):
            location["file"] = _portable_path(location["file"], base_dir)
    payload = {
        "schema_version": project.schema_version,
        "design": design_dict,
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
