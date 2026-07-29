"""Deterministic, Python-version-stable type-string formatting for the
generated artifact-model reference page (release-plan Phase 9, Defect 12).

Printing a dataclass field's raw ``field.type`` is not safe: every IR/
provenance/results/dataset module in this repo uses
``from __future__ import annotations``, so ``field.type`` is an
unevaluated *string*, not a type object — and even without postponed
evaluation, ``Optional``/``Union``/generic-alias reprs can vary across the
Python versions this package supports (3.8-3.11). ``format_type`` instead
takes an already-*resolved* type object (see ``typing.get_type_hints`` in
``artifact_walker.py``) and renders it through one small, tested,
recursive formatter, so the generated page's wording never depends on
which interpreter produced it.
"""
from __future__ import annotations

import types
import typing
from typing import Any

_NoneType = type(None)
_UNION_ORIGINS = {typing.Union, getattr(types, "UnionType", object())}

# Normalize typing generic-alias origins to their lowercase builtin
# spelling — `typing.List[int]` and `list[int]` must render identically.
_ORIGIN_NAMES = {
    list: "list",
    dict: "dict",
    set: "set",
    frozenset: "frozenset",
    tuple: "tuple",
}


def format_type(tp: Any) -> str:
    """Return a stable, deterministic string for a resolved type object."""
    if tp is _NoneType or tp is None:
        return "None"
    if tp is Any:
        return "Any"

    origin = typing.get_origin(tp)

    if origin is None:
        name = getattr(tp, "__name__", None)
        return name if name is not None else str(tp)

    args = typing.get_args(tp)

    if origin in _UNION_ORIGINS:
        return _format_union(args)

    if origin is tuple and args and args[-1] is Ellipsis:
        return f"tuple[{format_type(args[0])}, ...]"

    origin_name = _ORIGIN_NAMES.get(origin, getattr(origin, "__name__", str(origin)))
    if not args:
        return origin_name
    return f"{origin_name}[{', '.join(format_type(a) for a in args)}]"


def _format_union(args: "tuple[Any, ...]") -> str:
    non_none = [a for a in args if a is not _NoneType]
    is_optional = len(non_none) != len(args)
    rendered = [format_type(a) for a in non_none]

    if is_optional and len(rendered) == 1:
        return f"Optional[{rendered[0]}]"
    inner = f"Union[{', '.join(rendered)}]"
    return f"Optional[{inner}]" if is_optional else inner
