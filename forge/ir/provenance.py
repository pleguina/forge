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

Slice 5.1 additive fields (all optional/empty-default, so an older
manifest still round-trips): ``plan_hash`` (the generation plan's content
hash, when a caller has one), ``toolchain_versions`` (best-effort tool
version strings via ``forge.core.toolchain_versions``), ``output_hashes``
(content hashes of actually-generated artifacts, `gen-top`-only), and
``project_identity`` (the resolved consumer-root directory name, a more
stable label than the bare design-file-stem ``project_name``).

``generated_at`` is informational metadata only, per §5.2 — it is never
compared when explaining staleness.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from forge import __version__ as _forge_version
from ..core.utils.content_hash import hash_file
from .model import ResolvedProject
from .serialize import content_hash

PROVENANCE_SCHEMA_VERSION = "0.2.0"


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
    # Everything below is additive (Phase 5 slice 5.1) — all optional/
    # empty-default, so a slice-5.0-era manifest still round-trips.
    plan_hash: Optional[str] = None
    # Toolchain versions (forge.core.toolchain_versions) — populated
    # whenever a manifest is built, since it's an environment fact, not
    # generation-specific data only gen-top has access to (unlike
    # plan_hash/output_hashes below).
    toolchain_versions: Dict[str, str] = field(default_factory=dict)
    # Hashes of actually-generated artifacts, keyed the same relative-path
    # way as source_hashes. Empty for forge inspect's read-only path
    # (nothing was generated to hash) — populated only by gen-top.
    output_hashes: Dict[str, str] = field(default_factory=dict)
    # The resolved consumer-root directory name, when known — a more
    # stable project label than project_name (a bare design-file stem,
    # which collides across two designs both named design.yml).
    project_identity: Optional[str] = None

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
            plan_hash=data.get("plan_hash"),
            toolchain_versions=dict(data.get("toolchain_versions", {})),
            output_hashes=dict(data.get("output_hashes", {})),
            project_identity=data.get("project_identity"),
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


def _design_dir(project: ResolvedProject) -> Path:
    """The directory *project*'s design file was loaded from — the base
    every source-hash key is made relative to (see ``_relative_key``)."""
    design_value = project.generated_from.get("design")
    if design_value:
        return Path(design_value).parent
    return Path.cwd()


def _relative_key(path: Path, base_dir: Path) -> str:
    """Portable ``source_hashes``/``output_hashes`` key: *path* relative to
    *base_dir* (the design file's own directory), not an absolute path.

    Two checkouts of the same repo at different absolute locations resolve
    to the same relative key, so a manifest built from one checkout can be
    honestly compared against a manifest built from another — same
    "content hash should not answer 'did the caller's filesystem layout
    change'" rationale as ``serialize.py``'s ``_canonical_design_json``.

    Deliberately keeps its own ``os.path.relpath``-based implementation
    rather than delegating to the newer, general-purpose
    ``forge.core.utils.portable_path.portable_display_path()`` (release-
    plan Phase 8, slice 8.0A): that utility intentionally *never* emits a
    ``'../'``-laden relative path (falling back to a basename + hash
    label instead), which is the right call for a rendered, potentially-
    shared visualization artifact — but wrong here. A real, tested
    contract already relies on ``output_hashes`` keys being reconstructable
    (``(design_dir / key).resolve()`` round-trips to the real output file
    — see ``test_gen_top_writes_provenance_manifest_alongside_design_ir``),
    including for build outputs that legitimately live in a completely
    different directory tree than ``design.yml`` (e.g. a ``--build-dir``
    under a temp root). Falling back to a basename+hash label there would
    silently break that reconstruction — a real regression, not a
    portability improvement. Falls back to the absolute path only when
    *path* has no relative route to *base_dir* at all (e.g. a different
    drive on Windows) — an honest absolute key beats a fabricated
    relative one.
    """
    try:
        return os.path.relpath(path, base_dir)
    except ValueError:
        return str(path)


def build_provenance(
    project: ResolvedProject,
    *,
    command_options: Optional[Dict[str, Any]] = None,
    plan_hash: Optional[str] = None,
    output_paths: Optional[List["str | Path"]] = None,
    project_identity: Optional[str] = None,
    toolchain_versions: Optional[Dict[str, str]] = None,
) -> ProvenanceManifest:
    """Build a provenance manifest for an already-built IR *project*.

    *plan_hash*/*output_paths*/*project_identity* are ``None`` by default
    — the caller supplies them only when it has generation-specific
    context ``build_provenance`` itself can't derive (a ``GenerationPlan``,
    the list of files it just wrote, a resolved consumer root). This is
    what keeps ``forge inspect``'s read-only IR-only path honest: no plan
    exists there (a plan requires generation intent), and nothing was
    generated to hash.

    *toolchain_versions* defaults to a fresh
    ``forge.core.toolchain_versions.collect_toolchain_versions()`` call —
    unlike the three fields above, this is a real environment fact
    available regardless of whether generation happened, so every
    manifest gets it unless a caller explicitly overrides it (e.g. tests).
    """
    design_dir = _design_dir(project)
    source_hashes = {
        _relative_key(p, design_dir): hash_file(p) for p in _source_paths(project)
    }
    output_hashes: Dict[str, str] = {}
    for raw_path in output_paths or []:
        p = Path(raw_path)
        if p.exists() and p.is_file():
            output_hashes[_relative_key(p, design_dir)] = hash_file(p)

    if toolchain_versions is None:
        from ..core.toolchain_versions import collect_toolchain_versions
        toolchain_versions = collect_toolchain_versions()

    return ProvenanceManifest(
        forge_version=_forge_version,
        project_name=project.design.name,
        ir_schema_version=project.schema_version,
        ir_content_hash=content_hash(project),
        source_hashes=source_hashes,
        command_options=dict(command_options or {}),
        plan_hash=plan_hash,
        toolchain_versions=dict(toolchain_versions),
        output_hashes=output_hashes,
        project_identity=project_identity,
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


def render_markdown(manifest: ProvenanceManifest) -> str:
    """Render *manifest* as a Markdown summary — pure presentation over
    already-computed provenance data, no new hashing/comparison logic
    (release-plan Phase 6, §6.5: `forge report`'s provenance summary
    section)."""
    lines: List[str] = ["# Provenance summary", ""]
    lines.append(f"- **schema version**: {manifest.schema_version}")
    lines.append(f"- **forge version**: {manifest.forge_version or '(unknown)'}")
    lines.append(f"- **project**: {manifest.project_identity or manifest.project_name or '(unknown)'}")
    lines.append(f"- **IR schema version**: {manifest.ir_schema_version or '(unknown)'}")
    lines.append(f"- **IR content hash**: `{manifest.ir_content_hash or '(unknown)'}`")
    if manifest.plan_hash:
        lines.append(f"- **plan hash**: `{manifest.plan_hash}`")
    if manifest.generated_at:
        lines.append(f"- **generated at**: {manifest.generated_at}")

    if manifest.toolchain_versions:
        lines.append("")
        lines.append("## Toolchain versions")
        for tool, version in sorted(manifest.toolchain_versions.items()):
            lines.append(f"- `{tool}`: {version}")

    if manifest.source_hashes:
        lines.append("")
        lines.append("## Source file hashes")
        for key, digest in sorted(manifest.source_hashes.items()):
            lines.append(f"- `{key}`: `{digest}`")

    if manifest.output_hashes:
        lines.append("")
        lines.append("## Output artifact hashes")
        for key, digest in sorted(manifest.output_hashes.items()):
            lines.append(f"- `{key}`: `{digest}`")

    if manifest.command_options:
        lines.append("")
        lines.append("## Command options")
        for key, value in sorted(manifest.command_options.items()):
            lines.append(f"- `{key}`: {value!r}")

    return "\n".join(lines) + "\n"


@dataclass
class StalenessExplanation:
    stale: bool
    reasons: List[str] = field(default_factory=list)


def _parse_schema_version(version: Optional[str]) -> tuple:
    """Best-effort dotted-int parse of a ``PROVENANCE_SCHEMA_VERSION``-style
    string, for ordering comparisons only. Unparseable/missing input
    sorts as older than anything real — the safe direction for an
    "is this too old to trust" check."""
    if not version:
        return (0,)
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return (0,)


def explain_staleness(
    previous: ProvenanceManifest, current: ProvenanceManifest,
) -> StalenessExplanation:
    """Compare two provenance manifests and explain, in plain language,
    every reason they differ — release-plan §5.3.

    A *previous* manifest whose ``schema_version`` predates
    ``PROVENANCE_SCHEMA_VERSION`` (e.g. a 0.1.0-era manifest from before
    slice 5.0's ``source_hashes`` key-format change, absolute paths
    instead of relative ones) is flagged as unsupported and compared no
    further — every other field below assumes both manifests share the
    current key/shape conventions, so comparing a 0.1.0 manifest's
    absolute-path keys against a 0.2.0 manifest's relative-path keys
    would silently misreport every real key as both "removed" and
    "added" (a false diff, not a true staleness signal).
    """
    if _parse_schema_version(previous.schema_version) < _parse_schema_version(PROVENANCE_SCHEMA_VERSION):
        return StalenessExplanation(
            stale=True,
            reasons=[
                f"unsupported old manifest (schema_version {previous.schema_version!r}, "
                f"expected >= {PROVENANCE_SCHEMA_VERSION!r}) — regenerate to refresh"
            ],
        )

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

    # Slice 5.1 made toolchain_versions real; now meaningful to diff —
    # same set-diff pattern as source_hashes above.
    prev_tools = set(previous.toolchain_versions)
    curr_tools = set(current.toolchain_versions)
    for added in sorted(curr_tools - prev_tools):
        reasons.append(f"new toolchain detected: {added} ({current.toolchain_versions[added]})")
    for removed in sorted(prev_tools - curr_tools):
        reasons.append(f"toolchain no longer detected: {removed}")
    for common in sorted(prev_tools & curr_tools):
        if previous.toolchain_versions[common] != current.toolchain_versions[common]:
            reasons.append(
                f"changed tool version: {common} "
                f"{previous.toolchain_versions[common]!r} -> {current.toolchain_versions[common]!r}"
            )

    return StalenessExplanation(stale=bool(reasons), reasons=reasons)
