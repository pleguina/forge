"""Shared, small AST-based scanning utilities for forge.docsgen's
registries (release-plan Phase 9, Defect 13 applied consistently to both
the diagnostic-code registry and the transformation-kind registry).

Structural call-site matching, not text/regex grepping: a grep for
``FWV\\d+`` or a bare string search for a transformation kind would
false-positive on tests/comments/docstrings and could miss a kind built
through a local variable rather than typed inline. This module walks the
real AST instead, including one level of local dataflow — a same-function
variable assigned once from a string literal, or from a two-branch
ternary of string literals (the exact pattern
``forge/ir/build.py`` uses for its CDC transformation kind:
``kind = "cdc_synchronizer" if ... else "async_fifo"; ResolvedTransformation(kind=kind, ...)``)
— so it doesn't under-count real emission sites that happen to route a
literal through one local name first.
"""
from __future__ import annotations

import ast
from pathlib import Path


def _string_literal_values(node: ast.AST) -> "set[str] | None":
    """Every possible literal string value *node* could evaluate to, or
    ``None`` if it isn't a (possibly two-branch-ternary) string literal."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        body_vals = _string_literal_values(node.body)
        orelse_vals = _string_literal_values(node.orelse)
        if body_vals is not None and orelse_vals is not None:
            return body_vals | orelse_vals
    return None


def _callee_name(node: ast.Call) -> "str | None":
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def find_call_kwarg_string_values(
    source_path: "str | Path", call_names: "set[str]", kwarg_name: str,
) -> "set[str]":
    """Structurally find every literal string value passed as
    *kwarg_name* to a call whose callee name is in *call_names*, anywhere
    in *source_path*.
    """
    source_path = Path(source_path)
    tree = ast.parse(source_path.read_text(), filename=str(source_path))

    found: "set[str]" = set()
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # Function-scoped variable -> possible string literal value(s),
        # rebuilt per function so a name in one function never leaks into
        # another's resolution.
        local_strings: "dict[str, set[str]]" = {}
        for node in ast.walk(func):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                values = _string_literal_values(node.value)
                if values is not None:
                    local_strings.setdefault(node.targets[0].id, set()).update(values)

        for node in ast.walk(func):
            if not isinstance(node, ast.Call) or _callee_name(node) not in call_names:
                continue
            for kw in node.keywords:
                if kw.arg != kwarg_name:
                    continue
                values = _string_literal_values(kw.value)
                if values is not None:
                    found.update(values)
                elif isinstance(kw.value, ast.Name) and kw.value.id in local_strings:
                    found.update(local_strings[kw.value.id])
    return found
