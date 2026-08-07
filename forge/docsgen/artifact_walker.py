"""Recursive artifact-model reference generator.

``ARTIFACT_ROOTS`` names FORGE's real, versioned structured-artifact
types. Each root's own field table is not enough on its own — e.g.
``ResolvedProject.design: ResolvedDesign`` is meaningless without
``ResolvedDesign`` itself also documented, and ``ResolvedDesign`` nests
``ResolvedInstance``/``ResolvedConnection``/``ResolvedTransformation``/etc.
further — so this module walks every root **recursively**: every reachable
public dataclass or enum gets its own documented section, deduplicated
across roots that happen to share a type (e.g. ``ArtifactSchema``, used by
``FlowResult``, ``SerializedDataset``, and ``DesignGraph`` alike).

These pages are explicitly **versioned artifact-model references** — never
claimed to be JSON Schema. FORGE does not generate actual JSON Schema
documents today (see ``docs/development/SCHEMA_VERSIONING.md`` for the
schema-versioning policy each of these artifacts actually follows).
"""
from __future__ import annotations

import dataclasses
import typing
from enum import Enum
from typing import Any

from forge.analyze.design_explorer.graph_model import DesignGraph
from forge.ir.model import ResolvedProject
from forge.ir.provenance import ProvenanceManifest
from forge.verification.dataset_format import SerializedDataset
from forge.verification.results import FlowResult

from ._type_resolution import resolve_dataclass_field_types
from .type_formatter import format_type

ARTIFACT_ROOTS: "dict[str, type]" = {
    "forge.ir": ResolvedProject,
    "forge.provenance": ProvenanceManifest,
    "forge.verification_results": FlowResult,
    "forge.dataset": SerializedDataset,
    "forge.design_graph": DesignGraph,
}


def _discover_nested(tp: Any, found: "set[type]") -> None:
    if tp is None or tp is type(None) or tp is Any or tp is Ellipsis:
        return
    origin = typing.get_origin(tp)
    if origin is not None:
        for arg in typing.get_args(tp):
            _discover_nested(arg, found)
        return
    if isinstance(tp, type) and (dataclasses.is_dataclass(tp) or issubclass(tp, Enum)):
        found.add(tp)


def _walk(roots: "dict[str, type]") -> "tuple[list[type], list[type]]":
    """Breadth-first, deduplicated walk of every dataclass/enum reachable
    from *roots*. Returns ``(dataclasses_found, enums_found)`` — the
    caller sorts by name before rendering, so this function's own
    traversal order need only be finite and correct, not itself the
    presentation order."""
    seen: "set[type]" = set()
    queue: "list[type]" = list(roots.values())
    dataclasses_found: "list[type]" = []
    enums_found: "list[type]" = []

    while queue:
        cls = queue.pop(0)
        if cls in seen:
            continue
        seen.add(cls)

        if dataclasses.is_dataclass(cls):
            dataclasses_found.append(cls)
            nested: "set[type]" = set()
            for tp in resolve_dataclass_field_types(cls).values():
                _discover_nested(tp, nested)
            for n in sorted(nested, key=lambda c: c.__name__):
                if n not in seen:
                    queue.append(n)
        elif isinstance(cls, type) and issubclass(cls, Enum):
            enums_found.append(cls)

    return dataclasses_found, enums_found


def _format_default(f: "dataclasses.Field") -> str:
    if f.default is not dataclasses.MISSING:
        return f"`{f.default!r}`"
    if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
        try:
            return f"`{f.default_factory()!r}`"  # type: ignore[misc]
        except Exception:
            return "(factory)"
    return "—"


def _anchor(name: str) -> str:
    return name.lower()


def _render_dataclass_section(cls: type) -> str:
    lines = [f"### `{cls.__name__}`", ""]
    lines.append(f"*Defined in `{cls.__module__}`.*")
    lines.append("")
    doc = (cls.__doc__ or "").strip()
    if doc:
        lines.append(doc)
        lines.append("")

    field_types = resolve_dataclass_field_types(cls)
    lines.append("| Field | Type | Required | Default |")
    lines.append("|---|---|---|---|")
    for f in dataclasses.fields(cls):
        tp = field_types[f.name]
        rendered_type = format_type(tp)
        required = "required" if (
            f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING  # type: ignore[misc]
        ) else "optional"
        default = _format_default(f)
        lines.append(f"| `{f.name}` | `{rendered_type}` | {required} | {default} |")
    lines.append("")
    return "\n".join(lines)


def _render_enum_section(cls: type) -> str:
    lines = [f"### `{cls.__name__}`", ""]
    lines.append(f"*Defined in `{cls.__module__}`.*")
    lines.append("")
    doc = (cls.__doc__ or "").strip()
    if doc:
        lines.append(doc.splitlines()[0])
        lines.append("")
    lines.append("| Member | Value |")
    lines.append("|---|---|")
    for member in cls:  # type: ignore[attr-defined]
        lines.append(f"| `{member.name}` | `{member.value!r}` |")
    lines.append("")
    return "\n".join(lines)


def generate_artifacts_page() -> str:
    """Return the complete, deterministic Markdown content for
    ``docs/reference/artifacts.md``."""
    dataclasses_found, enums_found = _walk(ARTIFACT_ROOTS)
    dataclasses_found = sorted(set(dataclasses_found), key=lambda c: c.__name__)
    enums_found = sorted(set(enums_found), key=lambda c: c.__name__)

    lines = [
        "# Artifact Model Reference",
        "",
        "<!-- Generated by `python -m forge.docsgen`. Do not edit directly. -->",
        "",
        "This page is a **versioned artifact-model reference** — the real,",
        "structured shape of every artifact FORGE emits (the canonical IR,",
        "the provenance manifest, verification results, the dataset envelope,",
        "and the visual design-graph model). It is generated directly from",
        "the live Python dataclasses/enums, so it cannot silently drift from",
        "what the code actually emits.",
        "",
        "This is **not** JSON Schema — FORGE does not generate JSON Schema",
        "documents today. See",
        "[Schema Versioning](../development/SCHEMA_VERSIONING.md) for the",
        "schema-versioning policy each of these artifacts actually follows.",
        "",
        "## Roots",
        "",
        "| Root | Type |",
        "|---|---|",
    ]
    for root_name, root_cls in ARTIFACT_ROOTS.items():
        lines.append(f"| `{root_name}` | [`{root_cls.__name__}`](#{_anchor(root_cls.__name__)}) |")
    lines.append("")

    lines.append("## Models")
    lines.append("")
    for cls in dataclasses_found:
        lines.append(_render_dataclass_section(cls))

    if enums_found:
        lines.append("## Enums")
        lines.append("")
        for cls in enums_found:
            lines.append(_render_enum_section(cls))

    return "\n".join(lines).rstrip() + "\n"
