"""Resolve dataclass field annotations into real type objects, working
around a real cross-version gap: several modules this package walks
(``forge/verification/results.py``, ``forge/verification/dataset_format.py``,
``forge/ir/provenance.py``) write PEP 604 union annotations as quoted
strings (``"int | None"``) for readability, but ``X | None`` is only
*executable* on Python 3.10+ — evaluating that string via
``typing.get_type_hints`` on 3.8/3.9 (both still supported per
``forge/pyproject.toml``'s ``requires-python``) raises ``TypeError``,
since ``type.__or__`` doesn't exist yet on those versions. This module
rewrites a top-level ``X | Y`` union into ``typing.Union[X, Y]`` before
evaluating, so field-type resolution is correct on every supported
Python version rather than only 3.10+.
"""
from __future__ import annotations

import dataclasses
import sys
import typing
from typing import Any


def _split_top_level(raw: str, sep: str) -> "list[str]":
    """Split *raw* on *sep* only where bracket nesting depth is zero."""
    parts: "list[str]" = []
    depth = 0
    current: "list[str]" = []
    for ch in raw:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    parts.append("".join(current).strip())
    return parts


def _strip_redundant_quotes(raw: str) -> str:
    """Strip an outer quote layer left by postponed evaluation.

    Under ``from __future__ import annotations``, ``dataclasses.fields()``
    reports the *exact source text* of every annotation — including, for a
    field already written as an explicit forward-reference string (e.g.
    ``event_index: "int | None"``), the literal quote characters
    themselves. Left unstripped, ``"'int | None'"`` evaluates to the
    plain Python string ``'int | None'`` rather than a real type, which
    would silently defeat both the union rewrite below and this package's
    nested-dataclass discovery (a bare ``str`` has no ``__origin__``/
    ``__args__`` for the walker to recurse into). At most one layer is
    ever real here — this repo doesn't doubly-quote annotations — but the
    loop is capped rather than assumed to run exactly once.
    """
    s = raw.strip()
    for _ in range(2):
        if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
            s = s[1:-1].strip()
        else:
            break
    return s


def _rewrite_pep604_union(raw: str) -> str:
    """Rewrite a top-level ``X | Y | ...`` into ``typing.Union[X, Y, ...]``.

    Only rewrites at the top level — this repo's real annotations never
    nest a ``|`` union inside a generic's brackets (verified against every
    field this package currently walks), so a full recursive AST rewrite
    isn't needed; a bracket-depth-aware top-level split is sufficient and
    keeps this honestly scoped to the real cases in the codebase.
    """
    parts = _split_top_level(raw, "|")
    if len(parts) == 1:
        return raw
    return f"typing.Union[{', '.join(parts)}]"


def resolve_dataclass_field_types(cls: type) -> "dict[str, Any]":
    """Return ``{field_name: resolved_type_object}`` for every field of the
    dataclass *cls*, handling both ordinary postponed-evaluation strings
    and PEP 604 ``X | Y`` strings on any Python version this package
    supports (3.8-3.11).
    """
    module = sys.modules.get(cls.__module__)
    globalns = dict(vars(module)) if module is not None else {}
    localns: "dict[str, Any]" = {"typing": typing}

    resolved: "dict[str, Any]" = {}
    for f in dataclasses.fields(cls):
        raw = f.type
        if not isinstance(raw, str):
            resolved[f.name] = raw
            continue
        unquoted = _strip_redundant_quotes(raw)
        rewritten = _rewrite_pep604_union(unquoted)
        resolved[f.name] = eval(rewritten, globalns, localns)  # noqa: S307 - trusted, repo-internal source
    return resolved
