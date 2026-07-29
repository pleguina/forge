"""
Content-hash provenance for the canonical IR (release-plan Phase 5:
"Hash-based provenance and reproducibility").

This is a bounded, honest slice of Phase 5 — it does **not** replace the
existing mtime-based staleness checks used by `topgen gen-top`/`verify`
(`forge/core/stale_detection.py`, `forge/verify/stale_artifact.py`), which
are untouched this session per the "don't touch gen-top/verify's existing
code paths" scoping established in Phase 0/1. It provides a new, parallel,
content-hash-based provenance manifest attached to the canonical IR
(`forge inspect --provenance`/`--explain-staleness`), covering:

- FORGE version, IR schema version, canonical IR content hash;
- content hashes of the actual source files used to build the IR
  (design.yml, modules.yml, interface contracts — via
  ``forge.core.utils.content_hash``, generalizing the hashing pattern
  already used by ``forge.core.utils.port_signature``);
- the command options used;
- an explanation of *why* two manifests differ (changed input file,
  changed IR content, changed FORGE version, changed command options,
  missing/added input files) — release-plan §5.3.

``generated_at`` is informational metadata only, per §5.2 — it is never
compared when explaining staleness.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from forge import __version__ as _forge_version
from ..core.utils.content_hash import hash_file
from .model import ResolvedProject
from .serialize import content_hash

PROVENANCE_SCHEMA_VERSION = "0.1.0"


@dataclass
class ProvenanceManifest:
    schema_version: str = PROVENANCE_SCHEMA_VERSION
    forge_version: str = ""
    project_name: str = ""
    ir_schema_version: Optional[str] = None
    ir_content_hash: Optional[str] = None
    source_hashes: Dict[str, str] = field(default_factory=dict)
    command_options: Dict[str, Any] = field(default_factory=dict)
    generated_at: Optional[str] = None  # informational only — never used for staleness

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProvenanceManifest":
        return cls(
            schema_version=data.get("schema_version", PROVENANCE_SCHEMA_VERSION),
            forge_version=data.get("forge_version", ""),
            project_name=data.get("project_name", ""),
            ir_schema_version=data.get("ir_schema_version"),
            ir_content_hash=data.get("ir_content_hash"),
            source_hashes=dict(data.get("source_hashes", {})),
            command_options=dict(data.get("command_options", {})),
            generated_at=data.get("generated_at"),
        )


def _source_paths(project: ResolvedProject) -> List[Path]:
    """Every real, existing file that fed into *project*'s construction:
    the design file, contracts_from/ip_info if given, and every module's
    resolved interface-contract file."""
    paths: List[Path] = []
    for key in ("design", "contracts_from", "ip_info"):
        value = project.generated_from.get(key)
        if value:
            p = Path(value)
            if p.exists() and p.is_file():
                paths.append(p)
    for mod in project.design.modules:
        if mod.contract_path:
            p = Path(mod.contract_path)
            if p.exists() and p.is_file():
                paths.append(p)
    return paths


def build_provenance(
    project: ResolvedProject,
    *,
    command_options: Optional[Dict[str, Any]] = None,
) -> ProvenanceManifest:
    """Build a provenance manifest for an already-built IR *project*."""
    source_hashes = {str(p): hash_file(p) for p in _source_paths(project)}
    return ProvenanceManifest(
        forge_version=_forge_version,
        project_name=project.design.name,
        ir_schema_version=project.schema_version,
        ir_content_hash=content_hash(project),
        source_hashes=source_hashes,
        command_options=dict(command_options or {}),
    )


def write_provenance(path: "str | Path", manifest: ProvenanceManifest) -> None:
    import datetime

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = manifest.to_dict()
    payload["generated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def read_provenance(path: "str | Path") -> ProvenanceManifest:
    return ProvenanceManifest.from_dict(json.loads(Path(path).read_text()))


@dataclass
class StalenessExplanation:
    stale: bool
    reasons: List[str] = field(default_factory=list)


def explain_staleness(
    previous: ProvenanceManifest, current: ProvenanceManifest,
) -> StalenessExplanation:
    """Compare two provenance manifests and explain, in plain language,
    every reason they differ — release-plan §5.3."""
    reasons: List[str] = []

    if previous.schema_version != current.schema_version:
        reasons.append(
            f"provenance schema version changed: {previous.schema_version!r} -> {current.schema_version!r}"
        )
    if previous.forge_version != current.forge_version:
        reasons.append(
            f"FORGE version changed: {previous.forge_version!r} -> {current.forge_version!r}"
        )
    if previous.ir_schema_version != current.ir_schema_version:
        reasons.append(
            f"IR schema version changed: {previous.ir_schema_version!r} -> {current.ir_schema_version!r}"
        )
    if previous.ir_content_hash != current.ir_content_hash:
        reasons.append(
            f"canonical IR content changed: {previous.ir_content_hash} -> {current.ir_content_hash}"
        )
    if previous.command_options != current.command_options:
        reasons.append(
            f"command options changed: {previous.command_options!r} -> {current.command_options!r}"
        )

    prev_files = set(previous.source_hashes)
    curr_files = set(current.source_hashes)
    for added in sorted(curr_files - prev_files):
        reasons.append(f"new input file: {added}")
    for removed in sorted(prev_files - curr_files):
        reasons.append(f"input file no longer present: {removed}")
    for common in sorted(prev_files & curr_files):
        if previous.source_hashes[common] != current.source_hashes[common]:
            reasons.append(f"changed input: {common}")

    return StalenessExplanation(stale=bool(reasons), reasons=reasons)
